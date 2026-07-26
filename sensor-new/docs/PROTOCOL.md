# Breezio-2 line protocol (`$BRZ2`)

A tiny, human-readable, NMEA-style line protocol between the ESP32-S3 node and
the Python gateway. The primary deployment carries it over **Wi-Fi/TCP**.
The firmware also exposes the same lines over **USB serial
(UART0 → CH340 → the left USB-C port)** at **115200 baud, 8-N-1** for
diagnostics.

It is deliberately plain ASCII so you can watch it in any serial monitor and read
it with your eyes, yet every line carries an integrity checksum so the gateway can
silently drop bytes corrupted on the wire.

## Line format

```
$BRZ2,<TYPE>,<field>,<field>,...*<CS>\r\n
```

- Starts with `$`, ends with `*` + 2 hex checksum digits + newline.
- `<CS>` = **XOR of every byte between `$` and `*`** (exclusive), as 2 uppercase
  hex chars — the same checksum NMEA GPS sentences use. Trivial on both sides.
- Fields are comma-separated. A missing sensor value is the literal `NA`.
- The receiver ignores anything before `$` and re-syncs on the next `$`, so
  noise/garbage never desyncs the stream.
- `\r` is optional; the gateway accepts `\n`-only lines too.

## Node → host

### Telemetry (`T`) — sent every ~2 s
```
$BRZ2,T,<seq>,<temp>,<hum>,<light>,<aq>,<win>,<alarm>*CS
```
| Field   | Meaning | Example |
|---------|---------|---------|
| `seq`   | u32 monotonically increasing counter | `41` |
| `temp`  | temperature °C, 1 decimal, or `NA` | `24.5` |
| `hum`   | relative humidity %, 1 decimal, or `NA` | `48.0` |
| `light` | raw photoresistor reading, rescaled to 0–100, integer — **not calibrated lux** | `73` |
| `aq`    | MQ-135 air-quality reading, raw millivolts 0–3300 | `612` |
| `win`   | window state: `OPEN` or `CLOSED` | `CLOSED` |
| `alarm` | `1` if the buzzer alarm is active, else `0` | `0` |

Example: `$BRZ2,T,41,24.5,48.0,73,612,CLOSED,0*35`

> `light` is an uncalibrated index derived directly from the photoresistor's raw
> ADC voltage (see `LIGHT_INVERTED` in `src/sensors.rs`) — it is **not** lux. Turning
> it into real lux would need a calibrated reference measurement per board; nobody
> has done that here, so we don't call it lux.

> Higher `aq` millivolts = more gas (ammonia/benzene/ethanol/smoke/CO₂). The MQ-135
> is uncalibrated, so the host maps `aq` to a coarse GOOD/FAIR/POOR band rather than
> a true ppm. The node raises `alarm=1` when `aq` crosses its on-board threshold.

### Boot banner (`HELLO`) — sent once at startup
```
$BRZ2,HELLO,<fw_version>,<node_id>*CS
```
Example: `$BRZ2,HELLO,1.0.0,win-01*7B`

### Command acknowledgement (`ACK`)
```
$BRZ2,ACK,<id>,<device>,<result>,<state>*CS
```
| Field | Meaning | Example |
|-------|---------|---------|
| `id` | echoes the command `id` being answered | `7` |
| `device` | `WINDOW` or `BUZZER` | `WINDOW` |
| `result` | `OK` or `ERR` | `OK` |
| `state` | resulting device state | `OPEN` / `CLOSED` / `MUTED` |

Example: `$BRZ2,ACK,7,WINDOW,OK,OPEN*36`

## Host → node

### Command (`CMD`)
```
$BRZ2,CMD,<id>,<device>,<action>*CS
```
| `device` | valid `action` values | effect |
|----------|-----------------------|--------|
| `WINDOW` | `OPEN`, `CLOSE`, `TOGGLE` | opens/closes the simulated window (RGB LED + OLED) |
| `BUZZER` | `MUTE`, `TEST` | mutes the active alarm / sounds a short test beep |

Example: `$BRZ2,CMD,7,WINDOW,OPEN*1D`

The node applies the action, updates the OLED + on-board RGB LED (green = open,
red = closed), and replies with a matching `ACK`.

## Why not the old `~BZ1` hex protocol?

`~BZ1` was built for a compact CC2530 transport where packing and CRC-16
mattered. The ESP32-S3 uses TCP or a short local USB link, so `$BRZ2`
prioritizes legibility while retaining an XOR checksum and line
resynchronization.

## Wi-Fi/TCP transport (feature = `wifi`)

The exact same `$BRZ2` lines are also sent over TCP when the firmware is built
with `cargo build --features wifi`:

- **Join**: STA mode and DHCP. The node tries the primary SSID first and then
  the fallback SSID. The assigned IP is printed in the boot log.
- **Credentials**: public defaults in `src/wifi.rs` are placeholders. Override
  them at compile time with `BREEZIO_WIFI_SSID`,
  `BREEZIO_WIFI_FALLBACK_SSID` and `BREEZIO_WIFI_PASS`. Do not commit real
  credentials.
- **Gateway discovery**: after Wi-Fi is up, the node browses mDNS/DNS-SD for
  `_breezio-gateway._tcp.local` (`src/discovery.rs`, `esp_idf_svc::mdns`) to find
  the gateway's current host/port, so the normal path needs no compiled-in IP. If discovery
  times out or fails, it falls back to `BREEZIO_GATEWAY_HOST`/
  `BREEZIO_GATEWAY_PORT` (default port `5568`), settable at build time. It
  re-runs discovery on every reconnect, so a gateway IP change (moved host,
  DHCP renewal) is picked up automatically without reflashing.
- Once connected, the node sends `$BRZ2,HELLO,<fw>,win-01`, then streams
  telemetry and receives commands on the same TCP connection. UART0 keeps
  working in parallel.
- **Host side**: `python -m breezio_gateway.main --wifi-listen 0.0.0.0:5568`
  ingests telemetry into the standard MQTT topics, forwards
  `breezio/nodes/<id>/command` as `$BRZ2,CMD,...` with a CRC16-derived u16 id
  (translating the ACK id back), and publishes the `_breezio-gateway._tcp.local`
  DNS-SD service (instance name = `--gateway-id`, TXT `protocol=<dialect>` and
  `gateway_id=<id>`) via the optional `zeroconf` package. If it isn't
  installed, the gateway logs a warning and keeps serving TCP without
  advertising.
