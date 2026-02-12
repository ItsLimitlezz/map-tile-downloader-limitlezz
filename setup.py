from setuptools import setup

setup(
    name='map-tile-downloader',
    version='0.1',
    py_modules=['TileDL', 'qt_app'],
    package_dir={'': 'src'},
    install_requires=[
        'flask',
        'flask-socketio',
        'requests',
        'mercantile',
        'shapely',
        'pillow',
        'PySide6',
    ],
    entry_points={
        'console_scripts': [
            'map-tile-downloader = TileDL:main',
            'map-tile-downloader-qt = qt_app:launch_qt_app',
        ]
    },
)
