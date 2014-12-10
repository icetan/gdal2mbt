#!/usr/bin/env python
# -*- coding: utf-8 -*-

from setuptools import setup

setup(
    name='gdal2mbt',
    version='0.4.0',
    install_requires = ['Pillow>=2.5', 'GDAL>=1.8'],
    py_modules=['gdal2mbt'],
    scripts=['gdal2mbt_cli.py'],
    entry_points={
        'console_scripts': [
            'gdal2mbt = gdal2mbt_cli:main',
        ],
    },

    description='Create MBTiles from GDAL files.',
    url='http://github.com/icetan/gdal2mbt/',
    license='MIT',
    author=u'Chirstopher Fredén',
    author_email='c.freden@gmail.com',
    keywords = ['gdal', 'mbtiles', 'gis', 'tms'],
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'Natural Language :: English',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.6',
        'Programming Language :: Python :: 2.7',
        'Topic :: Scientific/Engineering :: GIS',
    ]
)
