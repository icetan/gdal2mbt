#!/usr/bin/env python

import os, sys, math, json
from itertools import dropwhile
from getopt import getopt
from os.path import join, isfile
from io import BytesIO

import sqlite3

import gdal, ogr, osr
from gdalconst import *

from PIL import Image

WGS84 = osr.SpatialReference()
WGS84.ImportFromEPSG(4326)

TILE_SIZE = 256
FORMAT = 'PNG'

config = None
_ds = None

def get_gdal():
    global _ds
    if _ds is None:
        _ds = gdal.Open(config['source'], GA_ReadOnly)
        if _ds is None: raise Error("Couldn't read GDAL file %s" % fn)
    return _ds

def create_mbtile(fn, **metadata):
    if isfile(fn): raise Error("MBTiles database file already exists")

    # Create empty MBTile file.
    db = sqlite3.connect(fn)
    db.execute("CREATE TABLE metadata (name text, value text)")
    db.execute("CREATE TABLE tiles (zoom_level integer,\
                tile_column integer, tile_row integer, tile_data blob)")
    db.execute("CREATE UNIQUE INDEX metadata_idx ON metadata (name)")
    db.execute("CREATE UNIQUE INDEX tiles_idx ON tiles\
                (zoom_level, tile_column, tile_row)")
    for k, v in metadata.iteritems(): insert_metadata(db, k, v)
    db.commit()
    return db

def insert_tile(db, level, tx, ty, buf):
    db.execute("INSERT INTO tiles(zoom_level, tile_column, tile_row, tile_data)\
                VALUES(?, ?, ?, ?)", (level, tx, ty, buf))

def insert_metadata(db, name, value):
    db.execute("INSERT INTO metadata(name, value) VALUES(?, ?)", (name, value))

def insert_bounds(db, left, bottom, right, top):
    insert_metadata(db,
        'bounds', "%s,%s,%s,%s" % (left, bottom, right, top))

def tile_exists(db, level, tx, ty):
    cur = db.cursor()
    cur.execute("SELECT COUNT(*) FROM tiles WHERE zoom_level=? AND\
                 tile_column=? AND tile_row=?", (level, tx, ty))
    return cur.next()[0] != 0

def get_extent():
    ds = get_gdal()
    srs = osr.SpatialReference(wkt=ds.GetProjection())

    # Read transform data from GeoTiff
    top_left_x, x_res, _, top_left_y, _, negative_y_res = ds.GetGeoTransform()
    if x_res != -negative_y_res:
        raise Error("Vertical resolution not same as horizontal.")
    return (top_left_x,
            top_left_y + negative_y_res * ds.RasterYSize,
            top_left_x + x_res * ds.RasterXSize,
            top_left_y,
            ds.RasterXSize,
            ds.RasterYSize,
            x_res,
            srs)

def get_gdal_image(ox, oy, w, h):
    ds = get_gdal()
    if ds.RasterCount == 3:
        mode = 'RGB'
    elif ds.RasterCount == 4:
        mode = 'RGBA'
    else:
        raise Error("Number of bands not supported %d" % ds.RasterCount)
    if verbose: print "Reading GDAL raster %d %d %d %d" % (ox, oy, w, h)
    raw = ds.ReadRaster(ox, oy, w, h)
    # Convert GDALs raster format to a sane one. rrrgggbbb -> rgbrgbrgb
    data = ''.join(''.join(raw[x+w*y::w*h]\
        for x in xrange(0,w))\
        for y in xrange(0,h))
    return Image.frombytes(mode, (w, h), data)

def get_quad_mbtiles(db, level, tx, ty):
    img = Image.new('RGBA', (TILE_SIZE*2, TILE_SIZE*2), (0,)*4)
    cur = db.cursor()
    x, y = tx*2, ty*2
    for row in cur.execute(
        "SELECT tile_column, tile_row, tile_data \
         FROM tiles WHERE zoom_level=%d AND (%s)" %\
            (level+1, " OR ".join(("(tile_column=? AND tile_row=?)",)*4)),
        (x, y, x+1, y, x, y+1, x+1, y+1)):

        if verbose: print "Reading MBTile %d %d %d" % (level+1, row[0], row[1])

        tile_img = Image.open(BytesIO(row[2]))
        img.paste(tile_img, ((row[0]-x)*TILE_SIZE, (1+y-row[1])*TILE_SIZE))
    return img

def to_wgs84(srs, lng, lat):
    # Create a geometry from coordinates
    point = ogr.Geometry(ogr.wkbPoint)
    point.AddPoint(lng, lat)

    # Transform point
    point.Transform(osr.CoordinateTransformation(srs, WGS84))
    return (point.GetX(), point.GetY())

# Get input parameters from shell
opts, args = getopt(sys.argv[1:], 'vrc:')
verbose = ('-v','') in opts
resume = ('-r','') in opts
for o, v in opts:
    if o == '-c':
        with open(v, 'rb') as fp: config = json.load(fp)

if config is None:
    out_fn = args[2]
    config = { 'source':args[0], 'num_levels':int(args[1]) }
else:
    out_fn = args[0]

num_levels = config['num_levels']
levels = range(0, num_levels)[::-1]
in_left, in_bottom, in_right, in_top, in_width, in_height, in_res, srs =\
    get_extent()

metadata = config['metadata'] if config.has_key('metadata') else {}
default_metadata = {
    'name': config['source'][:config['source'].rfind('.')],
    'type': 'overlay',
    'version': '1',
    'description': '',
    'format': FORMAT,
    'bounds': "%s,%s,%s,%s" %\
        (to_wgs84(srs, in_left, in_bottom) + to_wgs84(srs, in_right, in_top)),
    # Non-standard metadata
    'srs': srs.GetAttrValue('AUTHORITY', 0) + ':' +\
        srs.GetAttrValue('AUTHORITY', 1),
    'resolutions': ','.join((str(2 ** x * in_res) for x in levels))
}

for k, v in default_metadata.iteritems():
    if not metadata.has_key(k): metadata[k] = v

def create_tile(db, level, tx, ty):
    if verbose: print "Creating tile %d/%d/%d" % (level, tx, ty)
    out = BytesIO()
    if level == levels[0]:
        ox = tx*TILE_SIZE
        oy = ((in_ytiles-ty)*TILE_SIZE) - TILE_SIZE -\
            (in_ytiles*TILE_SIZE - in_height)
        img = get_gdal_image(ox, max(0, oy),
            min(TILE_SIZE, in_width-ox), min(TILE_SIZE, TILE_SIZE+oy))
        if  TILE_SIZE+oy < TILE_SIZE or in_width-ox < TILE_SIZE:
            img_ = Image.new('RGBA', (TILE_SIZE, TILE_SIZE), (0,)*4)
            img.paste(img, (0, min(TILE_SIZE, TILE_SIZE+oy)))
            img = img_
    else:
        img = get_quad_mbtiles(db, level, tx, ty)
        img.thumbnail((TILE_SIZE, TILE_SIZE))
    img.save(out, format=metadata['format'])
    out.seek(0)
    insert_tile(db, level, tx, ty, buffer(out.read()))
    db.commit()

def get_tile_nr(level, tx, ty):
    w, h = get_level_tiles(level)
    return 1 + sum(level_tiles[:levels[0]-level]) + tx + ty * w

def get_level_size(level):
    invf = float(2 ** (levels[0] - level))
    return (in_width / invf, in_height / invf)

def get_level_tiles(level):
    w, h = get_level_size(level)
    return int(math.ceil(w / TILE_SIZE)), int(math.ceil(h / TILE_SIZE))

in_xtiles, in_ytiles = get_level_tiles(levels[0])
level_tiles = [w*h for w, h in (get_level_tiles(level) for level in levels)]
total_tiles = sum(level_tiles)

if isfile(out_fn):
    if resume:
        db = sqlite3.connect(out_fn)
    else:
        stderr.writeln("MBTiles file already exists, use -r to resume.")
        exit(1)
else:
    db = create_mbtile(out_fn, **metadata)

tile_coords = ((level, x, y)
    for level, (level_xtiles, level_ytiles) in\
    ((level, get_level_tiles(level)) for level in levels)\
    for y in xrange(0, level_ytiles)\
    for x in xrange(0, level_xtiles))

if resume:
    coord =\
        dropwhile(lambda coord: tile_exists(db, *coord), tile_coords).next()
    if verbose: print "Resuming at tile #%d" % get_tile_nr(*coord)
    create_tile(db, *coord)

for coord in tile_coords:
    if verbose: print "At tile #%d of %d" % (get_tile_nr(*coord), total_tiles)
    create_tile(db, *coord)

db.close()
