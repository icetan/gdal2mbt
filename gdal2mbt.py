#!/usr/bin/env python

import math
from itertools import dropwhile
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

def _get_gdal_image(ds, ox, oy, w, h):
    if ds.RasterCount == 3:
        mode = 'RGB'
    elif ds.RasterCount == 4:
        mode = 'RGBA'
    else:
        raise Exception("Number of bands not supported %d" % ds.RasterCount)
    info("Reading GDAL raster x:%d y:%d %dx%d" % (ox, oy, w, h))
    raw = ds.ReadRaster(ox, oy, w, h)
    # Convert GDALs raster format to a sane one. rrrgggbbb -> rgbrgbrgb
    data = ''.join(''.join(raw[x+w*y::w*h]\
        for x in xrange(0,w))\
        for y in xrange(0,h))
    return Image.frombytes(mode, (w, h), data)

def _copy_table(db, src, table):
    info("Copying table %s from %s" % (table, src))
    cur = db.cursor()
    cur.execute("ATTACH DATABASE ? AS attached_db", (src,))
    cur.execute("INSERT INTO %s SELECT * FROM %s" % (table, 'attached_db.'+table))
    cur.execute("DETACH DATABASE attached_db")

def _create_mbtiles(fn, metadata={}):
    db = (sqlite3.connect(fn) if _isstr(fn) else fn)

    # Create empty MBTile file.
    db.execute("CREATE TABLE metadata (name text, value text)")
    db.execute("CREATE TABLE tiles (zoom_level integer,\
                tile_column integer, tile_row integer, tile_data blob)")
    db.execute("CREATE UNIQUE INDEX metadata_idx ON metadata (name)")
    _create_tile_index(db)
    for k, v in metadata.iteritems(): _insert_metadata(db, k, v)
    db.commit()
    return db

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
    return _create_mbtiles(fn, _get_metadata_from_gdal(ds, metadata))

def _create_tile_index(db):
    info("Creating tile index")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS tiles_idx ON tiles\
                (zoom_level, tile_column, tile_row)")
    db.commit()

def _drop_tile_index(db):
    info("Dropping tile index")
    db.execute("DROP INDEX IF EXISTS tiles_idx")
    db.commit()

def _create_zoom_level_index(db):
    info("Creating zoom level index")
    db.execute("CREATE INDEX IF NOT EXISTS zoom_level_idx ON tiles\
                (zoom_level)")
    db.commit()

def _drop_zoom_level_index(db):
    info("Dropping zoom level index")
    db.execute("DROP INDEX IF EXISTS zoom_level_idx")
    db.commit()

def _insert_tile(db, level, tx, ty, buf):
    db.execute("INSERT INTO tiles(zoom_level, tile_column, tile_row, tile_data)\
                VALUES(?, ?, ?, ?)", (level, tx, ty, buf))

def _insert_metadata(db, name, value):
    db.execute("INSERT INTO metadata(name, value) VALUES(?, ?)", (name, value))

def _insert_bounds(db, left, bottom, right, top):
    insert_metadata(db,
        'bounds', "%s,%s,%s,%s" % (left, bottom, right, top))

def _read_metadata(db, name):
    row = db.cursor().execute(
        "SELECT value FROM metadata WHERE name=?", (name,)).fetchone()
    return row[0]

def _tile_exists(db, level, tx, ty):
    row = db.cursor().execute(
        "SELECT COUNT(*) FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
        (level, tx, ty)).fetchone()
    return row[0] != 0

def _get_quad_mbtiles(db, level, tx, ty):
    img = Image.new('RGBA', (TILE_SIZE*2, TILE_SIZE*2), (0,)*4)
    x, y = tx*2, ty*2
    for row in db.cursor().execute(
        "SELECT tile_column, tile_row, tile_data \
         FROM tiles WHERE zoom_level=%d AND (%s)" %\
            (level+1, " OR ".join(("(tile_column=? AND tile_row=?)",)*4)),
        (x, y, x+1, y, x, y+1, x+1, y+1)):

        info("Reading MBTile %d %d %d" % (level+1, row[0], row[1]))

        tile_img = Image.open(BytesIO(row[2]))
        img.paste(tile_img, ((row[0]-x)*TILE_SIZE, (1+y-row[1])*TILE_SIZE))
    img.thumbnail((TILE_SIZE, TILE_SIZE))
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

    def get_tile_nr(level, tx, ty):
        w, h = get_level_tiles(level)
        return 1 + sum(level_tiles[:levels[0]-level]) + tx + ty * w

    def get_gdal_tile(level, tx, ty):
        if ds is None: raise Exception("No GDAL source supplied")
        ox = tx*TILE_SIZE
        oy = ((in_ytiles-ty)*TILE_SIZE) - TILE_SIZE -\
            (in_ytiles*TILE_SIZE - in_height)
        img = _get_gdal_image(ds, ox, max(0, oy),
            min(TILE_SIZE, in_width-ox), min(TILE_SIZE, TILE_SIZE+oy))
        if  TILE_SIZE+oy < TILE_SIZE or in_width-ox < TILE_SIZE:
            img_ = Image.new('RGBA', (TILE_SIZE, TILE_SIZE), (0,)*4)
            img.paste(img, (0, min(TILE_SIZE, TILE_SIZE+oy)))
            img = img_
        return img

    def create_tile(level, tx, ty):
        info("Creating tile %d/%d/%d" % (level, tx, ty))
        out = BytesIO()
        if level == levels[0]:
            img = get_gdal_tile(level, tx, ty)
        else:
            img = _get_quad_mbtiles(db, level, tx, ty)
        img.save(out, format=format_)
        out.seek(0)
        _insert_tile(db, level, tx, ty, buffer(out.read()))
        db.commit()

    in_xtiles, in_ytiles = get_level_tiles(levels[0])
    level_tiles = [w*h for w, h in (get_level_tiles(level) for level in levels)]
    total_tiles = sum(level_tiles)

    tile_coords = ((level, x, y)
        for level, (left, bottom, right, top) in\
        ((level, get_level_sub(level)) for level in levels)\
        for y in xrange(bottom, top)\
        for x in xrange(left, right))

    try:
        info("Checking for existing tiles")
        coord = dropwhile(lambda coord: _tile_exists(db, *coord), tile_coords).next()
    except StopIteration:
        info("All tiles exist, doing nothing")
    else:
        info("Starting at tile #%d" % get_tile_nr(*coord))
        create_tile(*coord)

        for coord in tile_coords:
            info("At tile #%d of %d" % (get_tile_nr(*coord), total_tiles))
            create_tile(*coord)

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
            out = sqlite3.connect(out)
            _copy_table(out, mbtiles[0], 'metadata')
    for db in mbtiles:
        _copy_table(out, db, 'tiles')

def set_levels(mbtiles, num_levels):
    db = (sqlite3.connect(mbtiles) if _isstr(mbtiles) else mbtiles)
    cur = db.cursor()
    #_create_zoom_level_index(db)
    row = cur.execute("SELECT MAX(zoom_level) FROM tiles").fetchone()
    diff = int(num_levels) - int(row[0])
    if diff > 0:
        info("Adding %d levels to MBTiles file" % diff)
        _drop_tile_index(db)
        info("Transposing tile coordinates")
        db.execute("UPDATE tiles SET zoom_level=zoom_level+?", (diff,))
        db.commit()
        _create_tile_index(db)
        resume(num_levels, db)
    elif diff < 0:
        info("Dropping %d levels from MBTiles file" % -diff)
        _drop_tile_index(db)
        info("Deleting levels")
        db.execute("DELETE FROM tiles WHERE zoom_level<?", (-diff,))
        info("Transposing tile coordinates")
        db.execute("UPDATE tiles SET zoom_level=zoom_level+?", (diff,))
        db.commit()
        _create_tile_index(db)
    else:
        info("There are already %s zoom levels, doing nothing" % num_levels)
