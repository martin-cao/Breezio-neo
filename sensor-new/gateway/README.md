# smartwin gateway (host side)

A tiny, dependency-light host gateway for the Breezio window node. It reads the
`$BRZ2` USB-serial protocol ([`../docs/PROTOCOL.md`](../docs/PROTOCOL.md)) and turns
it into structured JSON, and sends window/buzzer commands back.

- `smartwin_gateway/protocol.py` — pure `$BRZ2` codec (parse frames, encode
  commands, XOR checksum). No dependencies; mirrors the firmware's `src/proto.rs`.
- `smartwin_gateway/gateway.py` — stream → normalized JSON, plus serial helpers.
- `smartwin_gateway/__main__.py` — the `monitor` / `send` CLI.

`pyserial` is only needed to touch a real serial port; everything else (and the
whole test suite) runs with the standard library alone.

## Monitor live telemetry

```sh
# real hardware (needs: pip install pyserial)
python -m smartwin_gateway monitor --serial-port /dev/cu.usbserial-XXXX

# no hardware — replay a captured log
python -m smartwin_gateway monitor --stdin < capture.txt
```

Each line in / out:

```
$BRZ2,T,1,24.5,48.0,73,612,CLOSED,0*01
{"captured_at":"...","type":"telemetry","node_id":"win-01","seq":1,
 "temperature_c":24.5,"humidity_pct":48.0,"brightness_pct":73,
 "air_quality_mv":612,"window_open":false,"alarm":false,"air_quality_band":"GOOD"}
```

## Send a command

```sh
# open / close / toggle the simulated window
python -m smartwin_gateway send --serial-port /dev/cu.usbserial-XXXX --device WINDOW --action OPEN

# mute an air-quality alarm, or sound a test beep
python -m smartwin_gateway send --serial-port /dev/cu.usbserial-XXXX --device BUZZER --action MUTE

# dry-run: print the framed command line instead of sending it
python -m smartwin_gateway send --stdout --device WINDOW --action TOGGLE
# -> $BRZ2,CMD,12345,WINDOW,TOGGLE*..
```

The node applies the action, updates the OLED + RGB LED, and replies with a
matching `ACK` (visible in `monitor`).

## Find the serial port

- macOS: `ls /dev/cu.usbserial-* /dev/cu.wchusbserial*`
- Linux: `ls /dev/ttyUSB* /dev/ttyACM*` (CH340 usually `ttyUSB0`)

Use the **CH340** port (the left USB-C on the board), not the USB-JTAG one — logs
go to the JTAG port, protocol data goes to CH340.

## Tests

```sh
cd sensor-new/gateway
PYTHONPATH=. python -m unittest discover -s tests -v
```
