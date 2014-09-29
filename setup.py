from distutils.core import setup

setup(
    name='gdal2mbtiles',
    version='0.1.0',
    requires={
        'Pillow': '>=2.5',
        'GDAL': '>=1.8'
    },
    py_modules=['gdal2mbtiles'],
    scripts=['gdal2mbtiles'],

    description='Create MBTiles from GDAL files.',
    url='http://github.com/icetan/gdal2mbtiles/',
    license='MIT',
    author='Chirstopher Freden',
    author_email='c.freden@gmail.com',
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: Developers',
        'Natural Language :: English',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.6',
        'Programming Language :: Python :: 2.7',
        'Topic :: Software Development :: GIS',
    ]
)
