# CC2530 sensor firmware

This directory contains the CC2530F256 firmware used by the optional
IEEE 802.15.4 sensor path. The same source tree builds:

- a standalone UART node;
- a router with local sensing and Basic RF forwarding;
- a wireless sensor that joins the router's custom star network.

Each role can use either a DHT11 temperature and humidity sensor or an
illumination sensor. The application also implements an indicator command,
telemetry framing, command acknowledgements, and a repeatable flashing
workflow.

The radio path uses TI Basic RF and a project-specific application protocol.
It is not a Zigbee Z-Stack implementation.

## Requirements

- SDCC with the MCS-51 backend
- Python 3
- GNU Make
- libusb for native CC2530 programming

On macOS:

```sh
brew install sdcc libusb
```

## Build

Standalone temperature and humidity node:

```sh
make clean
make NODE_KIND=TEMPHUMI NODE_ID=n1
```

Standalone illumination node:

```sh
make clean
make NODE_KIND=ILLU NODE_ID=n1
```

Router and wireless sensor examples:

```sh
make clean
make NODE_ROLE=ROUTER NODE_KIND=TEMPHUMI NODE_ID=n1

make clean
make NODE_ROLE=SENSOR NODE_KIND=ILLU
```

Generated files are written to `build/`. The flashable image is
`build/breezio-sensor.hex`.

Run the complete offline build, Intel HEX validation, and package tests:

```sh
make check
```

Create a Windows SmartRF Flash Programmer handoff archive:

```sh
make package
```

## Flash

From the repository root, probe the target and verify its chip ID and factory
IEEE address:

```sh
PYTHONPATH=tools python3 -m xzg_cli.main probe-ti
```

Then erase, program, verify by readback, and reset:

```sh
PYTHONPATH=tools python3 -m xzg_cli.main flash-ti sensor/build/breezio-sensor.hex
```

Windows users can program the same HEX file with the legacy TI SmartRF Flash
Programmer.

## UART protocol

UART0 uses 38400 baud, 8 data bits, no parity, and one stop bit. The default
uplink uses a CRC-16 protected frame:

```text
~BZ1,<length_hex>,<payload_hex>,<crc16_hex>
```

Set `LEGACY_TEXT_PROTOCOL=1` at build time to emit the older
`@BATCH`/`@NODE`/`@END` telemetry format.

Commands and acknowledgements retain the accepted command ID end to end:

```text
@CMD,<command_id>,<node_id>,LAMP,ON
@ACK,<command_id>,<node_id>,OK,ON
```

For this CC2530 text path, `command_id` must contain 1–36 ASCII characters
from `A-Z`, `a-z`, `0-9`, `.`, `_`, `:`, or `-`. The gateway rejects empty,
oversized, comma-containing, or line-breaking IDs instead of truncating them.

The parser also accepts `OFF`, `TOGGLE`, `OPEN`, and `CLOSE` actions for the
indicator state.

## Hardware map

| Signal | Pin |
| --- | --- |
| DHT11 | P0.5 |
| Illumination ADC | P0.1 |
| Indicator D7 | P1.0 |
| Receive indicator D6 | P1.1 |
| UART0 | 38400 8N1 |

See [`docs/wireless-mesh.md`](docs/wireless-mesh.md) for the custom star
protocol and [`../docs/sensor-network-protocol.md`](../docs/sensor-network-protocol.md)
for the gateway-facing data contract.

## Licensing

Project-authored files are distributed under Apache-2.0. The Basic RF, HAL,
and clock sources retain Texas Instruments notices and device-specific use
terms. See [`../THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md).
