# Breezio Python Gateway

Gateway 将设备侧 `$BRZ2` 或兼容串口帧归一化为统一遥测结构，再通过 MQTT
接入后端。主硬件路径由 ESP32-S3 主动连接 Gateway 的 TCP 监听端口，并通过
mDNS/DNS-SD 发现服务。串口解析保留给本地诊断、模拟器与可选 CC2530 固件。

## ESP32-S3 Wi-Fi/TCP 主链

先启动本地 MQTT 基础设施：

```sh
docker compose --env-file .env -f deploy/docker-compose.yml up -d emqx
```

然后启动 TCP 监听、mDNS 广播和 MQTT 发布：

```sh
PYTHONPATH=gateway \
uv run --with paho-mqtt --with zeroconf \
  python -m breezio_gateway.main \
  --wifi-listen 0.0.0.0:5568 \
  --sensor-dialect brz2 \
  --mqtt-host 127.0.0.1 \
  --gateway-id gw-smartwin-01
```

ESP32-S3 建立连接后会先发送 `HELLO`，随后在同一连接上传遥测并接收控制命令。
Gateway 发布标准 topic：

```text
breezio/gateways/<gateway_id>/batch
breezio/nodes/<node_id>/telemetry
breezio/nodes/<node_id>/command
breezio/nodes/<node_id>/command_ack
```

不连接 MQTT 时可以省略 `--mqtt-host`。解析后的消息会写入被 Git 忽略的
`var/gateway-offline.jsonl`，适合协议调试。

## 串口与模拟器

从标准输入验证兼容帧，不需要硬件或 MQTT：

```sh
printf '{A0=58,A1=27}\n' |
  PYTHONPATH=gateway uv run python -m breezio_gateway.main --stdin
```

运行 PTY 虚拟传感器和 Gateway：

```sh
PYTHONPATH=gateway uv run python tools/run_simulated_gateway.py --once
```

接入一个硬件串口：

```sh
PYTHONPATH=gateway \
uv run --with paho-mqtt \
  python -m breezio_gateway.main \
  --topology router \
  --serial-port /dev/cu.usbserial-EXAMPLE \
  --mqtt-host 127.0.0.1 \
  --gateway-id gw-serial-01
```

多个独立串口节点使用 `--topology direct`，并重复传入 `--serial-port`。

## 控制转发

仅检查 MQTT 命令到串口帧的转换：

```sh
PYTHONPATH=gateway \
uv run --with paho-mqtt \
  python -m breezio_gateway.command_listener \
  --mqtt-host 127.0.0.1 \
  --node-id n1 \
  --stdout
```

## 测试

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=gateway \
  uv run python -m unittest discover -s gateway/tests
```
