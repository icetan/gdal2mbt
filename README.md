# gdal2mbt

*Create MBTiles from a GDAL file*

## Requirements

- Python 2.7

## Installation

```
git clone https://github.com/icetan/gdal2mbt.git
pip install -e gdal2mbt
```

## Creating a MBTile file

```sh
gdal2mbt create goteborg.mbtiles 8 goteborg.vrt
```

Or use a config JSON file:

```json
{
    "source": "goteborg.vrt",
    "num_levels": 8
    "metadata": {
        "name": "goteborg",
        "description": "Arial photos over Göteborg city"
    }
}
```

```sh
gdal2mbt create -c goteborg.json goteborg.mbtiles
```

## Parallel jobs

Create an MBTiles file for each tile on zoom level 0, this will depend on the
second argument which defines how many zoom levels to generate in total.

```sh
gdal2mbt config goteborg.vrt 6 | parallel gdal2mbt resume -c
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

Add zoom levels that might have been omited due to the amount of tiles on zoom
level 0 at time of creation.

```sh
gdal2mbt levels goteborg.mbtiles 8
```
