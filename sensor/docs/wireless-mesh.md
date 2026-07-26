# CC2530 custom star network

The CC2530 radio path uses TI Basic RF for IEEE 802.15.4 transmission and a
small project-specific protocol for joining, telemetry, retries, and commands.
It does not use Z-Stack and does not implement Zigbee routing.

## Build roles

```sh
make clean
make NODE_ROLE=ROUTER NODE_KIND=TEMPHUMI NODE_ID=n1

make clean
make NODE_ROLE=SENSOR NODE_KIND=ILLU
```

`NODE_KIND` accepts `TEMPHUMI` and `ILLU`. A router keeps a local sensor,
maintains a table of up to eight wireless nodes, and forwards their readings to
UART. A sensor derives a short radio address from its factory IEEE address,
joins a router, and reports local readings.

## Radio settings

| Setting | Value |
| --- | --- |
| PAN ID | `0x2530` |
| Channel | 26 |
| Broadcast address | `0xFFFF` |
| Maximum registered sensors | 8 |
| Receive buffer | 80 bytes |

The SDCC port polls the radio receive flag from the main loop. Application
acknowledgements and bounded retries provide delivery feedback.

## Message types

| Code | Direction | Meaning |
| --- | --- | --- |
| `0x01` | Sensor to router | Join request |
| `0x02` | Router to sensor | Join acceptance |
| `0x03` | Sensor to router | Sensor data |
| `0x04` | Router to sensor | Data acknowledgement |
| `0x05` | Router to sensor | Control command |
| `0x06` | Sensor to router | Command acknowledgement |

The join request carries the sender's short address, factory IEEE address, and
sensor kind. The router assigns application node IDs beginning at `n2`; `n1`
is reserved for the router's local sensor.

Sensor data contains:

```text
type | sender_addr | seq | sensor_kind | value1 | value2
```

Temperature and humidity use tenths. An illumination node uses `value1` for
its ADC reading.

## Reliability

- Join requests retry with a deterministic per-node offset.
- Sensor data is retried up to three times while waiting for a matching
  sequence acknowledgement.
- Three consecutive data failures cause the sensor to rejoin.
- Router commands are retried up to three times.
- Command and acknowledgement buffers are sized for the current fixed fields.
- The router reports the original gateway command ID when it publishes the
  final UART acknowledgement.

## Hardware verification checklist

1. Build and validate both router and sensor HEX images.
2. Program each physical board and record its factory IEEE address.
3. Power the router before the sensor.
4. Observe the join request and assigned application node ID.
5. Compare sensor data values with the local sensor condition.
6. Send an indicator command and verify the matching acknowledgement.
7. Power-cycle one side and verify bounded recovery.

The gateway-facing UART and MQTT formats are documented in
[`../../docs/sensor-network-protocol.md`](../../docs/sensor-network-protocol.md).
