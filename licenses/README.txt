Third-party software notices
============================

This application uses Python and the third-party libraries listed in build-info.json.
The application's MIT license does not replace these components' licenses.

Qt / PySide6 / Shiboken 6.11.2
-----------------------------
Copyright (C) The Qt Company Ltd. and other contributors.
This distribution uses the LGPLv3 option for the included Qt libraries and Python
bindings. LGPL-3.0.txt and GPL-3.0.txt are included in Qt/.
Qt third-party attribution texts for Core, GUI, Network, Widgets, OpenGL, SVG and
image formats are included in Qt/; some upstream notices describe features not
used by this application.

The libraries are distributed as separate shared libraries. Users may replace
compatible libraries and debug modifications, including reverse engineering for
that purpose as permitted by the applicable license. Do not impose restrictions
that remove these rights. After replacing libraries, keep their ABI, architecture
and dependent runtime files compatible; alternatively rebuild from source.

Upstream source access:
https://download.qt.io/official_releases/qt/6.11/6.11.2/submodules/
https://code.qt.io/cgit/qt/qtbase.git/?h=v6.11.2
https://code.qt.io/cgit/pyside/pyside-setup.git/?h=v6.11.2
https://doc.qt.io/qtforpython-6/building_from_source/index.html

OpenSSL 3.0.13: https://github.com/openssl/openssl/tree/openssl-3.0.13
Python 3.11.9: https://www.python.org/downloads/release/python-3119/
NumPy 1.23.5 (including OpenBLAS notices): https://github.com/numpy/numpy/tree/v1.23.5
OpenCV Python 4.9.0.80 (including FFmpeg notices): https://github.com/opencv/opencv-python/tree/4.9.0.80
Pillow 12.3.0: https://github.com/python-pillow/Pillow
windows-capture 2.0.1: https://github.com/NiiightmareXD/windows-capture
pywin32 306: https://github.com/mhammond/pywin32/tree/b306
PyInstaller 6.20.0 bootloader: https://github.com/pyinstaller/pyinstaller/tree/v6.20.0

Each component's copied license and copyright texts are in its subdirectory.
Python's notices also cover bundled support components. Microsoft runtime DLLs
are Microsoft components, not software licensed under the application's MIT.

For maintainers: these notices were collected for the versions above. Updating
binary dependencies requires updating their notices and source access information.
Providing license texts alone is not a substitute for fulfilling source-availability
and other applicable redistribution requirements.
