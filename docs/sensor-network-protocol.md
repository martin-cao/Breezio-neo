# Sensor network protocol

This document describes the device-facing protocols used by Breezio. The main
runtime path is the ESP32-S3 Wi-Fi/TCP link. The CC2530 implementation is an
optional IEEE 802.15.4 path that shares the same gateway and MQTT data model.

## 1. Common data model

The gateway normalizes every device frame into a reading with these fields:

| Field | Meaning |
| --- | --- |
| `gateway_id` | Gateway instance that accepted the frame |
| `node_id` | Stable application node identifier |
| `seq` | Device sequence number |
| `captured_at` | Gateway receive time in ISO 8601 format |
| `temperature` | Temperature in degrees Celsius, or `null` |
| `humidity` | Relative humidity percentage, or `null` |
| `light` | Illumination value, or `null` |
| `air_quality` | Air quality ADC value, or `null` |
| `battery_mv` | Battery voltage in millivolts, or `null` |
| `rssi` | Received signal strength, or `null` |
| `flags` | Device state bit field |
| `source` | Transport name such as `wifi_tcp` or `serial_hex` |

The gateway publishes a batch to:

```text
breezio/gateways/<gateway_id>/batch
```

It also publishes each normalized reading to:

```text
breezio/nodes/<node_id>/telemetry
```

Control commands use:

```text
breezio/nodes/<node_id>/command
breezio/nodes/<node_id>/command_ack
```

## 2. ESP32-S3 Wi-Fi/TCP path

### 2.1 Connection lifecycle

The node joins a 2.4 GHz Wi-Fi network in STA mode and obtains an address with
DHCP. Real credentials are provided at build time:

```sh
BREEZIO_WIFI_SSID=<primary> \
BREEZIO_WIFI_FALLBACK_SSID=<fallback> \
BREEZIO_WIFI_PASS=<password> \
cargo build --release --features wifi
```

After joining the network, the node browses for
`_breezio-gateway._tcp.local` with mDNS/DNS-SD. If discovery fails, it uses the
build-time `BREEZIO_GATEWAY_HOST` and `BREEZIO_GATEWAY_PORT` values. The node
initiates the TCP connection and repeats discovery after a disconnect.

The gateway listener starts with:

```sh
PYTHONPATH=gateway python -m breezio_gateway.main \
  --wifi-listen 0.0.0.0:5568 \
  --sensor-dialect brz2 \
  --mqtt-host 127.0.0.1
```

### 2.2 `$BRZ2` framing

Each line has an NMEA-style XOR checksum:

```text
$<body>*<checksum>\r\n
```

The checksum is the XOR of all ASCII bytes in `body`.

The first line identifies the connection:

```text
$BRZ2,HELLO,<firmware_version>,<node_id>*CS
```

Telemetry has this form:

```text
$BRZ2,T,<seq>,<temperature>,<humidity>,<light>,<air_quality>,<window_state>,<alarm>*CS
```

Missing numeric fields use `NA`. `window_state` is `OPEN` or `CLOSED`, and
`alarm` is `0` or `1`.

Commands and acknowledgements use a 15-bit short ID:

```text
$BRZ2,CMD,<short_id>,<device>,<action>*CS
$BRZ2,ACK,<short_id>,<device>,<result>,<state>*CS
```

The gateway derives `short_id` from the backend command ID and keeps the
reverse mapping until the matching ACK arrives. The MQTT acknowledgement
therefore carries the original backend command ID.

Supported devices include `WINDOW`, `LED`, and `BUZZER`. Supported actions
depend on the device and include `OPEN`, `CLOSE`, `TOGGLE`, `ON`, `OFF`,
`MUTE`, and `TEST`.

### 2.3 Device flags

The gateway maps the ESP32 state into `flags`:

| Bit | Meaning |
| --- | --- |
| 0 | Window is open |
| 1 | Air quality alarm is active |

## 3. CC2530 optional path

The CC2530 firmware uses TI Basic RF as an IEEE 802.15.4 radio layer and adds
a project-specific star protocol. It does not implement the Zigbee Z-Stack
network layer or Zigbee multi-hop routing.

Three build roles are available:

| Role | Behavior |
| --- | --- |
| Standalone | Samples a local sensor and sends telemetry through UART |
| `ROUTER` | Samples locally and forwards frames for joined wireless sensors |
| `SENSOR` | Joins a router and sends local readings through Basic RF |

### 3.1 UART telemetry

The default UART uplink is a CRC-16 protected line:

```text
~BZ1,<length_hex>,<payload_hex>,<crc16_hex>\r\n
```

`length_hex` is a four-digit byte count. `payload_hex` contains uppercase
hexadecimal bytes. `crc16_hex` uses CRC-16/CCITT-FALSE with polynomial
`0x1021`, initial value `0xFFFF`, no reflection, and no final XOR.

Telemetry payload fields are little-endian:

| Field | Type |
| --- | --- |
| `version` | `uint8`, currently `1` |
| `msg_type` | `uint8`, `0x01` |
| `batch_id` | `uint16` |
| `record_count` | `uint8`, currently `1` |
| `node_id_len` | `uint8` |
| `node_id` | ASCII bytes |
| `seq` | `uint16` |
| `temperature_x10` | `int16`, missing value `0x7FFF` |
| `humidity_x10` | `int16`, missing value `0x7FFF` |
| `light` | `uint16`, missing value `0xFFFF` |
| `battery_mv` | `uint16`, missing value `0xFFFF` |
| `rssi` | `int8`, missing value `-128` |
| `flags` | `uint8` |

Set `LEGACY_TEXT_PROTOCOL=1` during compilation to use the compatibility
`@BATCH`, `@NODE`, and `@END` telemetry format.

### 3.2 Commands

CC2530 commands and acknowledgements retain the original string command ID:

```text
@CMD,<command_id>,<target_node_id>,<device>,<action>
@ACK,<command_id>,<target_node_id>,<result>,<state>
```

The complete ID lets the backend update the matching `control_commands` row
without a lossy hash translation.

## 4. Reliability rules

- The ESP32 node increments `seq` for each telemetry sample.
- The CC2530 firmware increments both batch and reading sequence numbers.
- The gateway rejects malformed lengths, invalid hexadecimal data, and
  checksum mismatches.
- MQTT uses QoS 1, so the backend also enforces message-level deduplication.
- A command moves through `pending`, `sent`, and either `acked` or `failed`.
- Transport errors return structured failures and do not create synthetic
  sensor readings.

## 5. Related code

- ESP32 firmware: `sensor-new/src/`
- CC2530 firmware: `sensor/source/`
- Gateway parsers: `gateway/breezio_gateway/protocol.py`
- Wi-Fi transport: `gateway/breezio_gateway/wifi.py`
- MQTT ingestion: `backend/workers/mqtt_ingestor.py`
- Control dispatch: `backend/workers/control_dispatcher.py`
