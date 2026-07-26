#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

python3 tools/check_port.py --static
grep -q '@BATCH' source/main.c
grep -q '@NODE' source/main.c
grep -q '@CMD' source/main.c
grep -q '@ACK' source/main.c
python3 -m py_compile \
    tools/check_port.py \
    tools/create_flash_package.py \
    tools/serial_monitor.py

if [ -f Makefile ]; then
    make clean
    make
    python3 tools/check_port.py --hex build/breezio-sensor.hex
    make package
    python3 -c 'import zipfile; z=zipfile.ZipFile("dist/breezio-sensor-sdcc.zip"); expected={"breezio-sensor.hex","breezio-sensor.mem","SHA256SUMS.txt","FLASHING.txt"}; assert set(z.namelist()) == expected'
fi
