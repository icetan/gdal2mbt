#!/usr/bin/env python

"""Usage: %s COMMAND [OPTIONS] arguments...

Options:

    -c <config file>    Read config JSON from file or if `-` from STDIN.

    -v                  Verbose output.

Commands:

    create <file> <num_levels> <source>

        Create an MBTiles file from a GDAL source.

        -r      Resume the creation of a MBTiles file, use with `create`.

        -m      Use a in-memory database while creating a new MBTiles file,
                this is not compatible with the `-r` option.

    config <source> <num_levels> Generate gdal2mbt JSON configs.

    merge  <file> <mbtiles>...   Merge MBTiles files into one.

        -s      Delete each file after they are merged, to save space during
                the process.

    levels <file> <num_levels>   Add or remove levels from MBTiles file.

    help                         This message.

    version                      Print gdal2mbt version.

"""

import json, logging, os
from sys import exit, stderr, stdin, argv
from os.path import join, isfile, basename
from getopt import getopt
import sqlite3
from pkg_resources import require

import gdal2mbt

def main():
    config = {}
    args = []

    def usage():
        stderr.write(__doc__ % argv[0])
        exit()

    def error(e):
        print "Error:", e
        print "To get usage try: %s help" % argv[0]
        exit(1)

    def update_config(keys, value=args):
        try:
            config.update(dict(zip(keys, args)))
        except Exception as e:
            error(e)

    try:
        if len(argv) < 2: usage()
        # Get input parameters from shell
        command = argv[1].lower()
        opts, args = getopt(argv[2:], 'c:rmvs')
        for o, v in opts:
            if o == '-c':
                if v == '-':
                    config = json.load(stdin)
                else:
                    if isfile(v):
                        with open(v, 'rb') as fp: config = json.load(fp)
                    else:
                        config = json.loads(v)
            if o == '-v':
                logging.basicConfig(level=logging.INFO, format='%(message)s')
    except Exception as e:
        error(e)

    if command == 'create':
        update_config(('mbtiles', 'num_levels', 'source'))
        if isfile(config['mbtiles']):
            if ('-m','') in opts:
                stderr.write(("MBTiles file %s already exists (resume -r can't" +
                             " be used together with in-memory database -m).\n") %
                             config['mbtiles'])
                exit(2)
            if ('-r','') in opts:
                config.pop('metadata', None)
                gdal2mbt.resume(**config)
            else:
                stderr.write("MBTiles file %s already exists, use -r to resume.\n" %
                             config['mbtiles'])
                exit(2)
        else:
            if ('-m','') in opts:
                db_fn = config['mbtiles']
                mem_db = config['mbtiles'] = sqlite3.connect(':memory:')
                gdal2mbt.create(**config)
                gdal2mbt.copy(mem_db, db_fn)
            else:
                gdal2mbt.create(**config)

    elif command == 'merge':
        if ('-s','') in opts:
            out = args[0]
            for db in args[1:]:
                gdal2mbt.merge(out, db)
                os.remove(db)
        else:
            gdal2mbt.merge(*args)

    elif command == 'levels':
        update_config(('mbtiles', 'num_levels'))
        gdal2mbt.set_levels(config['mbtiles'], config['num_levels'])

    elif command == 'config':
        update_config(('source', 'num_levels'))
        source_name = basename(config['source'])
        source_name = source_name[:source_name.rfind('.')]
        name = config.get('metadata', {}).get('name', source_name).replace(' ','_')
        mbtiles_tmpl = "%s.%%d.mbtiles" % name
        count = 0
        for sub in gdal2mbt.split(config['num_levels'], config['source']):
            config['mbtiles'] = mbtiles_tmpl % count
            config['sub_bounds'] = sub
            print json.dumps(config)
            count += 1

    elif command == 'help':
        usage()

    elif command == 'version':
        print require('gdal2mbt')[0]

if __name__ == '__main__':
    main()
