# Third-party notices

The Apache-2.0 license in the repository root applies to project-authored
files. It does not replace notices embedded in third-party source files.

## Texas Instruments CC2530 Basic RF and HAL sources

The following files are derived from Texas Instruments CC2530 Basic RF, HAL,
and clock sources and are not relicensed under the repository's Apache-2.0
license:

- `sensor/source/basic_rf.c`
- `sensor/source/basic_rf.h`
- `sensor/source/clock.c`
- `sensor/source/clock.h`
- `sensor/source/hal_assert.c`
- `sensor/source/hal_assert.h`
- `sensor/source/hal_defs.h`
- `sensor/source/hal_int.c`
- `sensor/source/hal_int.h`
- `sensor/source/hal_mcu.c`
- `sensor/source/hal_mcu.h`
- `sensor/source/hal_rf.c`
- `sensor/source/hal_rf.h`
- `sensor/source/hal_types.h`

Most of these files retain an embedded Texas Instruments copyright and license
block. `sensor/source/clock.h` identifies its Texas Instruments CC253x origin
but does not repeat the complete block, so it is listed here explicitly rather
than described as carrying a full embedded notice.

The embedded Texas Instruments terms limit use, modification, copying, and
distribution to software embedded on a Texas Instruments microcontroller or
used solely with a Texas Instruments radio-frequency transceiver. Those terms
govern the TI-derived files wherever they appear.

## XZG-MT

The workflow and protocol research used by `tools/xzg_cli/` were informed by
XZG-MT.

MIT License

Copyright (c) 2025 xyzroe

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
