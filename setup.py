import os

from distutils.core import setup

def read(*paths):
    """Build a file path from *paths* and return the contents."""
    with open(os.path.join(*paths), 'r') as f:
        return f.read()

setup(
    name='gdal2mbtiles',
    version='0.1.0',
    install_requires=['Pillow>=2.5', 'GDAL>=1.8'],
    scripts=['gdal2mbtiles.py'],

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
    ],
)
