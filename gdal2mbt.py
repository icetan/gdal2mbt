#!/usr/bin/env python

import math
from itertools import islice
from io import BytesIO
from logging import info, debug
from os.path import isfile

import sqlite3

import gdal, ogr, osr
from gdalconst import *
from PIL import Image

WGS84 = osr.SpatialReference()
WGS84.ImportFromEPSG(4326)

TILE_SIZE = 256
DEFAULT_FORMAT = 'PNG'
DEFAULT_SUB = (0, 0, float('inf'), float('inf'))

# Private functions

def _isstr(x): return isinstance(x, str) or isinstance(x, unicode)

def _get_gdal_extent(ds):
    srs = osr.SpatialReference(wkt=ds.GetProjection())

    # Read transform data from GeoTiff
    top_left_x, x_res, _, top_left_y, _, negative_y_res = ds.GetGeoTransform()
    if x_res != -negative_y_res:
        raise Exception("Vertical resolution not same as horizontal.")
    return (top_left_x,
            top_left_y + negative_y_res * ds.RasterYSize,
            top_left_x + x_res * ds.RasterXSize,
            top_left_y,
            ds.RasterXSize, ds.RasterYSize,
            x_res, srs)

def _get_mbtiles_extent(db):
    srs = osr.SpatialReference()
    srs.ImportFromProj4(str(_read_metadata(db, 'srs')))
    resolution = float(_read_metadata(db, 'resolution'))
    (west, south, east, north) =\
        (float(x) for x in _read_metadata(db, 'bounds').split(','))
    left, bottom = _from_wgs84(srs, west, south)
    right, top = _from_wgs84(srs, east, north)
    return (left, bottom, right, top,
            int((right - left) / resolution),
            int((top - bottom) / resolution),
            resolution, srs)

def _image_to_buffer(img, format_):
    out = BytesIO()
    img.save(out, format=format_)
    out.seek(0)
    return buffer(out.read())

def _get_gdal_image(ds, ox, oy, w, h):
    if ds.RasterCount == 3:
        mode = 'RGB'
    elif ds.RasterCount == 4:
        mode = 'RGBA'
    else:
        raise Exception("Number of bands not supported %d" % ds.RasterCount)
    info("Reading GDAL raster x:%d y:%d %dx%d" % (ox, oy, w, h))
    raw = ds.ReadRaster(ox, oy, w, h)
    # Check if image is over an empty area.
    if all(ord(x) == 0 for x in raw):
        return None
    # Convert GDALs raster format to a sane one. rrrgggbbb -> rgbrgbrgb
    data = ''.join(''.join(raw[x+w*y::w*h]\
        for x in xrange(0,w))\
        for y in xrange(0,h))
    return Image.frombytes(mode, (w, h), data)

def _copy_tables(db, src, tables):
    info("Copying tables %s from %s" % (', '.join(tables), src))
    cur = db.cursor()
    cur.execute("ATTACH DATABASE ? AS _db", (src,))
    for table in tables:
        cur.execute("INSERT INTO %s SELECT * FROM %s" % (table, '_db.'+table))
    cur.execute("DETACH DATABASE _db")

def _copy_tiles(db, src):
    info("Copying tiles from %s" % src)
    cur = db.cursor()
    cur.execute("ATTACH DATABASE ? AS _db", (src,))
    cur.execute("INSERT INTO images SELECT * FROM _db.images WHERE tile_id!=0")
    cur.execute("INSERT INTO map SELECT * FROM _db.map")
    cur.execute("DETACH DATABASE _db")

def _save_to_file(db, fn):
    info("Copying database to file %s" % fn)
    cur = db.cursor()
    cur.execute("ATTACH DATABASE ? AS _db", (fn,))
    cur.execute("INSERT INTO _db.metadata SELECT * FROM metadata")
    cur.execute("INSERT INTO _db.images SELECT tile_data, NULL FROM images")
    cur.execute("INSERT INTO _db.map\
                 SELECT zoom_level, tile_column, tile_row, NULL\
                 FROM map")
    cur.execute("DETACH DATABASE _db")

def _get_metadata_from_gdal(ds, metadata={}):
    ds = _get_gdal_dataset(ds)
    in_left, in_bottom, in_right, in_top, in_width, in_height,\
        in_res, srs = _get_gdal_extent(ds)

    default_metadata = {
        'name': 'untitled',
        'type': 'overlay',
        'version': '1',
        'description': '',
        'format': DEFAULT_FORMAT,
        'bounds': "%s,%s,%s,%s" % (
            _to_wgs84(srs, in_left, in_bottom) +
            _to_wgs84(srs, in_right, in_top)),
        # Non-standard metadata
        'srs': srs.ExportToProj4(),
        'resolution': in_res
    }

    for k, v in default_metadata.iteritems():
        if not metadata.has_key(k): metadata[k] = v
    return metadata

def _create_mbtiles_from_gdal(fn, ds, metadata={}):
    return create_empty(fn, _get_metadata_from_gdal(ds, metadata))

def _create_tile_index(db):
    info("Creating tile index")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS tiles_idx ON map\
                (zoom_level, tile_column, tile_row)")

def _drop_tile_index(db):
    info("Dropping tile index")
    db.execute("DROP INDEX IF EXISTS tiles_idx")

def _transpose_levels(db, diff):
    info("Transposing tiles zoom level by %d" % diff)
    _drop_tile_index(db)
    db.execute("UPDATE map SET zoom_level=zoom_level+?", (diff,))
    _create_tile_index(db)
    db.commit()

def _insert_tile_map(db, id_, level, tx, ty):
    db.execute("INSERT INTO map VALUES(?, ?, ?, ?)", (level, tx, ty, id_))

def _insert_tile_image(db, id_, buf, ignore=False):
    if ignore:
        db.execute("INSERT OR IGNORE INTO images VALUES(?, ?)", (buf, id_))
    else:
        db.execute("INSERT INTO images VALUES(?, ?)", (buf, id_))

def _insert_tile(db, id_, level, tx, ty, buf):
    _insert_tile_map(db, id_, level, tx, ty)
    _insert_tile_image(db, id_, buf)

def _insert_metadata(db, name, value):
    db.execute("INSERT INTO metadata(name, value) VALUES(?, ?)", (name, value))

def _insert_bounds(db, left, bottom, right, top):
    insert_metadata(db,
        'bounds', "%s,%s,%s,%s" % (left, bottom, right, top))

def _read_metadata(db, name):
    cur = db.cursor()
    cur.execute("SELECT value FROM metadata WHERE name=?", (name,))
    return cur.fetchone()[0]

def _tile_count(db):
    cur = db.cursor()
    cur.execute("SELECT COUNT(*) FROM map")
    return cur.fetchone()[0]

def _get_quad_mbtiles(db, level, tx, ty):
    img = None
    x, y = tx*2, ty*2
    for row in db.cursor().execute(
        "SELECT tile_column, tile_row, tile_data \
         FROM map JOIN images ON map.tile_id=images.tile_id \
         WHERE map.tile_id!=0 AND zoom_level=%d AND (%s)" %\
            (level+1, " OR ".join(("(tile_column=? AND tile_row=?)",)*4)),
        (x, y, x+1, y, x, y+1, x+1, y+1)):

        info("Reading MBTile %d %d %d" % (level+1, row[0], row[1]))

        if img is None:
            img = Image.new('RGBA', (TILE_SIZE*2, TILE_SIZE*2), (0,)*4)
        tile_img = Image.open(BytesIO(row[2]))
        img.paste(tile_img, ((row[0]-x)*TILE_SIZE, (1+y-row[1])*TILE_SIZE))
    if img is not None: img.thumbnail((TILE_SIZE, TILE_SIZE))
    return img

def _trans(from_srs, to_srs, x, y):
    point = ogr.Geometry(ogr.wkbPoint)
    point.AddPoint(x, y)
    point.Transform(osr.CoordinateTransformation(from_srs, to_srs))
    return (point.GetX(), point.GetY())
def _from_wgs84(srs, x, y): return _trans(WGS84, srs, x, y)
def _to_wgs84(srs, x, y): return _trans(srs, WGS84, x, y)

def _get_gdal_dataset(source):
    if _isstr(source):
        source = gdal.Open(source, GA_ReadOnly)
        if source is None: raise Exception("Couldn't read GDAL file %s" % source)
    return source

def _tile_coord(x, y, tile_size=TILE_SIZE):
    return (int(math.ceil(x / float(tile_size))),
            int(math.ceil(y / float(tile_size))))


# API functions

def create_empty(fn, metadata={}):
    db = (sqlite3.connect(fn) if _isstr(fn) else fn)

    # Create empty MBTile file.
    db.execute("CREATE TABLE IF NOT EXISTS metadata\
                (name TEXT PRIMARY KEY, value TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS images\
                (tile_data BLOB, tile_id INTEGER PRIMARY KEY)")
    db.execute("CREATE TABLE IF NOT EXISTS map\
                (zoom_level INTEGER,\
                 tile_column INTEGER,\
                 tile_row INTEGER,\
                 tile_id INTEGER)")
    db.execute("CREATE VIEW IF NOT EXISTS tiles AS\
                SELECT zoom_level, tile_column, tile_row, tile_data\
                FROM map\
                JOIN images ON map.tile_id = images.tile_id")
    _create_tile_index(db)
    for k, v in metadata.iteritems(): _insert_metadata(db, k, v)
    db.commit()
    return db

def copy(src, dst_fn):
    if _isstr(dst_fn):
        create_empty(dst_fn).close()
        _save_to_file(src, dst_fn)
    else:
        raise Exception("Destination must be a filename string")

def create(num_levels, mbtiles, source, sub_bounds=DEFAULT_SUB, metadata={}):
    ds = _get_gdal_dataset(source)
    resume(num_levels, _create_mbtiles_from_gdal(mbtiles, ds, metadata),
           ds, sub_bounds)

def resume(num_levels, mbtiles, source=None, sub_bounds=DEFAULT_SUB):
    ds = _get_gdal_dataset(source)
    db = (sqlite3.connect(mbtiles) if _isstr(mbtiles) else mbtiles)

    format_ = _read_metadata(db, 'format')
    levels = range(int(num_levels), -1, -1)
    in_left, in_bottom, in_right, in_top, in_width, in_height, in_res, srs =\
        _get_mbtiles_extent(db)

    def get_level_size(level):
        invf = float(2 ** (levels[0] - level))
        return (in_width / invf, in_height / invf)

    def get_level_tiles(level):
        return _tile_coord(*get_level_size(level))

    def get_level_sub(level):
        level_xtiles, level_ytiles = get_level_tiles(level)
        invf = 2 ** (levels[0] - level)
        left, bottom, right, top = map(lambda x: x/invf, sub_bounds)
        return (max(0, left), max(0, bottom),
                min(level_xtiles, right), min(level_ytiles, top))

    def get_total_level_sub_tiles(level):
        left, bottom, right, top = get_level_sub(level)
        return (right - left) * (top - bottom)

    def get_tile_nr(level, tx, ty):
        l = levels[0]-level
        return level_sum_tiles[l] + tx + ty * level_tile_dims[l][0] + 1

    def get_gdal_tile(level, tx, ty):
        if ds is None: raise Exception("No GDAL source supplied")
        ox = tx*TILE_SIZE
        oy = ((in_ytiles-ty)*TILE_SIZE) - TILE_SIZE -\
            (in_ytiles*TILE_SIZE - in_height)
        img = _get_gdal_image(ds, ox, max(0, oy),
            min(TILE_SIZE, in_width-ox), min(TILE_SIZE, TILE_SIZE+oy))
        if img is None:
            return None
        if TILE_SIZE+oy < TILE_SIZE or in_width-ox < TILE_SIZE:
            img_ = Image.new('RGBA', (TILE_SIZE, TILE_SIZE), (0,)*4)
            img.paste(img, (0, min(TILE_SIZE, TILE_SIZE+oy)))
            img = img_
        return img

    def create_tile(id_, level, tx, ty):
        info("Creating tile %d (%d/%d/%d)" % (id_, level, tx, ty))
        if level == levels[0]:
            img = get_gdal_tile(level, tx, ty)
        else:
            img = _get_quad_mbtiles(db, level, tx, ty)
        if img is None:
            info("Tile %d (%d/%d/%d) is empty" % (id_, level, tx, ty))
            _insert_tile_map(db, 0, level, tx, ty)
        else:
            _insert_tile(db, id_, level, tx, ty, _image_to_buffer(img, format_))

    def create_transparent_tile():
        img = Image.new('RGBA', (TILE_SIZE, TILE_SIZE), (0,)*4)
        _insert_tile_image(db, 0, _image_to_buffer(img, format_), ignore=True)

    in_xtiles, in_ytiles = get_level_tiles(levels[0])
    level_tile_dims = [(w, h, w*h) for w, h in (get_level_tiles(level) for level in levels)]
    level_tiles = map(lambda x: x[2], level_tile_dims)
    level_sum_tiles = [sum(level_tiles[:i]) for i in range(len(levels))]
    level_subs = map(lambda l: get_level_sub(l), levels)
    total_tiles = sum(level_tiles)
    total_sub_tiles = sum(get_total_level_sub_tiles(l) for l in levels)

    create_transparent_tile()

    info("Checking for existing tiles")
    start_at = _tile_count(db)

    tile_coords = islice(
        enumerate(
            ((level, x, y)
                for level, (left, bottom, right, top) in\
                    ((level, level_subs[levels[0]-level]) for level in levels)\
                for y in xrange(bottom, top)\
                for x in xrange(left, right)
            ), 1
        ), start_at, None
    )

    try:
        #db.execute("PRAGMA synchronous=OFF")
        #db.execute("PRAGMA journal_mode=WAL")
        #db.commit()
        for n, coord in tile_coords:
            id_ = get_tile_nr(*coord)
            info("At tile #%d of %d id:%d" % (n, total_sub_tiles, id_))
            create_tile(id_, *coord)
            db.commit()
    finally:
        #db.execute("PRAGMA journal_mode=DELETE")
        #db.execute("PRAGMA synchronous=NORMAL")
        db.commit()

    info("Done!")

def split(num_levels, source):
    ds = _get_gdal_dataset(source)
    in_left, in_bottom, in_right, in_top, in_width, in_height,\
        in_res, srs = _get_gdal_extent(ds)
    chunk_size = 2 ** int(num_levels) * TILE_SIZE
    xchunks, ychunks = _tile_coord(in_width, in_height, chunk_size)
    return (_tile_coord(cx * chunk_size, cy * chunk_size) +\
            _tile_coord((cx+1) * chunk_size, (cy+1) * chunk_size)
                for cy in xrange(0, ychunks)
                for cx in xrange(0, xchunks))

def merge(out, *mbtiles):
    if _isstr(out):
        if isfile(out):
            out = sqlite3.connect(out)
        else:
            out = create_empty(out)
            _copy_tables(out, mbtiles[0], ('metadata',))
    for db in mbtiles:
        _copy_tiles(out, db)

def set_levels(mbtiles, num_levels):
    db = (sqlite3.connect(mbtiles) if _isstr(mbtiles) else mbtiles)
    cur = db.cursor()
    #_create_zoom_level_index(db)
    row = cur.execute("SELECT MAX(zoom_level) FROM map").fetchone()
    diff = int(num_levels) - int(row[0])
    if diff > 0:
        info("Adding %d levels to MBTiles file" % diff)
        _transpose_levels(db, diff)
        resume(num_levels, db)
    elif diff < 0:
        info("Dropping %d levels from MBTiles file" % -diff)
        db.execute("DELETE FROM images\
                    WHERE images.tile_id IN\
                    (SELECT images.tile_id FROM images\
                     JOIN map ON map.tile_id = images.tile_id\
                     WHERE map.zoom_level<?)", (-diff,))
        db.execute("DELETE FROM map WHERE zoom_level<?", (-diff,))
        db.commit()
        _transpose_levels(db, diff)
    else:
        info("There are already %s zoom levels, doing nothing" % num_levels)
