# User interface

The Qt Quick interface uses PySide6 / Qt for Python, distributed under its
applicable LGPLv3/GPLv3/commercial terms. Runtime distribution includes the
PySide6, PySide6_Essentials, and shiboken6 package license metadata. Source and
license details: https://code.qt.io/pyside/pyside-setup.git/ and
https://www.qt.io/licensing/.

Icons are from Microsoft Fluent System Icons, Copyright (c) Microsoft Corporation,
MIT license. Original SVG assets are retained and recolored at runtime. The full
MIT license is included at ui/assets/Fluent-Icons-LICENSE.txt. Icon source:
https://github.com/microsoft/fluentui-system-icons (Wi-Fi SVG from the same
project's @fluentui/svg-icons 1.1.316 package).

The pale background asset was generated with the built-in Image Gen tool.
Windows system fonts are used and are not redistributed.

# Campus network protocol references

The campus network integration was independently implemented in Python's standard
library. The authentication endpoint, operator suffixes and portal status query
were informed by these MIT-licensed projects:

- [barkure/CSU-Net-Portal](https://github.com/barkure/CSU-Net-Portal/tree/d52a48f011ec984eaab0e85510f71005d7835274)
  — Copyright (c) 2026 barkure.
- [Temparo/CSU-WIFI-AutoLogin](https://github.com/Temparo/CSU-WIFI-AutoLogin/tree/c92676e7238600c97d3448ed2d4c8c33beb6e202)
  — Copyright (c) 2024 Temparo.

The original projects' installers, services and UI frameworks are not bundled.
Their applicable MIT license text is reproduced below.

## MIT License

Copyright (c) 2026 barkure

Copyright (c) 2024 Temparo

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
