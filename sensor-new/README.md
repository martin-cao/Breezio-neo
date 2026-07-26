# Breezio sensor-new — ESP32-S3 "smart window" node (embedded Rust)

Firmware for the Goouuu **ESP32-S3-DevKitC-1 (N16R8)** that turns the wired-up
breadboard from [`docs/WIRING.md`](docs/WIRING.md) into a single "smart window"
node. It:

- reads **temperature + humidity** (DHT11, GPIO4),
- reads **brightness** (photoresistor → ADC1 on GPIO8),
- reads **air quality** (MQ-135 → ADC1 on GPIO9) and raises a **buzzer alarm**
  (GPIO5) on bad air,
- shows a live **status screen** on the **OLED** (SSD1306, I2C on GPIO6/7),
- pretends to be a **window** the host can open/close, shown on the on-board
  **RGB LED** (GPIO48) and the OLED,
- connects to the Python gateway over **Wi-Fi/TCP** using the `$BRZ2` line
  protocol, with mDNS/DNS-SD discovery and reconnect support,
- exposes the same `$BRZ2` stream over **USB serial** for local diagnostics.

The primary host gateway lives in [`../gateway/`](../gateway/). A small
USB-serial inspection helper is also available in [`gateway/`](gateway/).

## Architecture

```
main.rs        loop: poll commands -> sample sensors (2 s) -> alarm -> render -> emit telemetry
├─ dht11.rs    bit-banged DHT11 one-wire driver (µs timing via esp_timer_get_time)
├─ sensors.rs  ADC mV -> brightness %, air-quality thresholds (the numbers to calibrate)
├─ led.rs      WS2812 RGB over RMT (green=open, amber=closed, blinking red=air alarm)
├─ buzzer.rs   active buzzer, high-level trigger
├─ display.rs  SSD1306 OLED status screen (embedded-graphics)
├─ link.rs     UART0 <-> $BRZ2 lines (telemetry out, commands in)
├─ wifi.rs     Wi-Fi STA setup with compile-time credentials
├─ discovery.rs  mDNS/DNS-SD gateway discovery
├─ net_link.rs TCP transport and reconnect loop
├─ proto.rs    $BRZ2 encode/decode + NMEA-style XOR checksum
└─ app.rs      shared AppState
```

Console logs (`log::info!`) go to the **USB Serial/JTAG** port; the `$BRZ2` data
stream goes to **UART0 → CH340 → the left USB-C port**. They never mix, so the
gateway sees a clean protocol stream. This is set in `sdkconfig.defaults`.

## Pin map

| Signal | GPIO | Notes |
|--------|------|-------|
| Photoresistor `AO` | GPIO8 | ADC1_CH7 (left header) |
| MQ-135 `AO` | GPIO9 | ADC1_CH8 (left header) |
| DHT11 `DATA` | GPIO4 | on-module pull-up |
| Buzzer `I/O` | GPIO5 | HIGH = beep |
| OLED `SDA` / `SCK` | GPIO6 / GPIO7 | I2C0 @ 400 kHz, addr 0x3C |
| RGB LED | GPIO48 | on-board WS2812 |
| UART0 TX / RX | GPIO43 / GPIO44 | to CH340 (left USB-C) |

All sensor modules powered from **3.3V** (see the wiring guide for why).

## Build & flash

Prerequisites: the **Espressif Rust toolchain** (`espup install`, then
`. ~/export-esp.sh`), plus `ldproxy` and `espflash`:

```sh
cargo install ldproxy espflash
```

Export the local network settings and build the primary Wi-Fi firmware:

```sh
export BREEZIO_WIFI_SSID=<ssid>
export BREEZIO_WIFI_FALLBACK_SSID=<fallback-ssid>
export BREEZIO_WIFI_PASS=<password>
export BREEZIO_GATEWAY_HOST=<gateway-ip>
cargo build --release --features wifi
```

With the CH340/left USB-C cable connected, flash and open the monitor:

```sh
cargo run --release --features wifi
```

Omit `--features wifi` only when building the USB-serial diagnostic variant.

## Try it without the gateway

Open the CH340 serial port in any monitor at **115200 baud**. You should see:

```
$BRZ2,HELLO,1.0.0,win-01*7B
$BRZ2,T,1,24.5,48.0,73,612,CLOSED,0*01
...
```

Type a command line (checksum required — the gateway computes it for you):

```
$BRZ2,CMD,7,WINDOW,OPEN*1D
```

and the LED turns green, the OLED shows `Win:OPEN`, and you get back
`$BRZ2,ACK,7,WINDOW,OK,OPEN*36`.

## Calibration knobs

Everything you'd tune on the bench is in [`src/sensors.rs`](src/sensors.rs):
`LIGHT_INVERTED` (flip if brightness reads backwards), and `AQ_ALARM_MV` /
`AQ_CLEAR_MV` (air-quality alarm on/off thresholds, with hysteresis). Let the
MQ-135 warm up a few minutes before trusting its numbers.

This firmware targets `esp-idf-hal 0.45` and `esp-idf-svc 0.51`. Use the pinned
toolchain in `rust-toolchain.toml`. If a dependency patch changes the ADC API,
check the two channel configurations in `main.rs` before flashing.
