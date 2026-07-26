# XZG CLI

`xzg_cli` validates CC2530 Intel HEX images and provides two programming
backends:

- a native libusb path for TI CC Debugger and compatible SmartRF04EB devices;
- a compatibility path that prepares or runs `cc-tool`.

Install libusb on macOS:

```sh
brew install libusb
```

From the repository root, validate and probe before writing:

```sh
PYTHONPATH=tools python3 -m xzg_cli.main check-hex sensor/build/breezio-sensor.hex
PYTHONPATH=tools python3 -m xzg_cli.main probe-ti
```

Program the target with erase, write, readback verification, and reset:

```sh
PYTHONPATH=tools python3 -m xzg_cli.main flash-ti sensor/build/breezio-sensor.hex
```

`probe-ti` prints the chip ID and factory IEEE address. Check both values
against the intended physical board before flashing.

The `flash-cc2530` and `probe-cc` commands remain available for programmers
supported by `cc-tool`. Bridge discovery and serial-control commands support
XZG-MT compatible HTTP endpoints.

The bridge workflow and programmer protocol were informed by XZG-MT. See
[`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md) for attribution.
