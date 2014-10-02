# gdal2mbt

*Create MBTiles from a GDAL file*

## Requirements

- Python 2.7

## Installation

```
pip install https://github.com/icetan/gdal2mbt/archive/master.zip
```

Or from source

```
git clone https://github.com/icetan/gdal2mbt.git
pip install -e gdal2mbt
```

## Creating a MBTile file

```sh
gdal2mbt create goteborg.mbtiles 8 goteborg.vrt
```

Or use a JSON config file with an optional metadata dictionary that will
overwrite the defaults in the MBTiles metadata table.

```json
{
    "source": "goteborg.vrt",
    "num_levels": 8,
    "metadata": {
        "name": "goteborg",
        "description": "Aerial photos over Göteborg city"
    }
}
```

```sh
gdal2mbt create -c goteborg.json goteborg.mbtiles
```

To resume an aborted MBTiles creation process pass the `-r` flag to `create`.

## Parallel jobs

Speed up MBTiles creation by distributing the load over several processors or
even computers by using [GNU Parallel](http://www.gnu.org/software/parallel/).

Create an MBTiles file for each tile on zoom level 0, this will depend on the
second argument which defines how many zoom levels to generate in total.

```sh
gdal2mbt config goteborg.vrt 6 | parallel gdal2mbt create -c
```

Merge all the created MBTiles to one.

```sh
gdal2mbt merge goteborg.mbtiles goteborg.*.mbtiles
```

Or to squash all MBTiles into one to save space use the destructive `-s` flag
which will remove each merged MBTiles file.

```sh
gdal2mbt merge -s goteborg.*.mbtiles
```

Add zoom levels that might have been omitted due to the amount of tiles on zoom
level 0 at time of creation.

```sh
gdal2mbt levels goteborg.mbtiles 8
```
