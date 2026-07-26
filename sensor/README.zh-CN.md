# CC2530 传感器固件

本目录包含 CC2530F256 固件，用于可选的 IEEE 802.15.4 传感支线。同一套源码可以构建：

- 通过 UART 直接接入网关的独立节点；
- 负责本地采集和 Basic RF 转发的 Router；
- 加入自定义星型网络的无线 Sensor。

每种角色均可选择 DHT11 温湿度传感器或照度传感器。应用代码还包含指示灯控制、遥测成帧、
命令回执和刷写工具。

无线部分使用 TI Basic RF 和项目自定义应用协议，不是基于 Z-Stack 的 Zigbee 实现。

## 依赖

- 带 MCS-51 后端的 SDCC
- Python 3
- GNU Make
- 用于原生刷写的 libusb

macOS 安装命令：

```sh
brew install sdcc libusb
```

## 构建

独立温湿度节点：

```sh
make clean
make NODE_KIND=TEMPHUMI NODE_ID=n1
```

独立照度节点：

```sh
make clean
make NODE_KIND=ILLU NODE_ID=n1
```

Router 与无线 Sensor 示例：

```sh
make clean
make NODE_ROLE=ROUTER NODE_KIND=TEMPHUMI NODE_ID=n1

make clean
make NODE_ROLE=SENSOR NODE_KIND=ILLU
```

构建产物位于 `build/`，可刷写文件为 `build/breezio-sensor.hex`。

执行完整离线构建、Intel HEX 校验和打包测试：

```sh
make check
```

生成供 Windows SmartRF Flash Programmer 使用的自包含压缩包：

```sh
make package
```

## 刷写

在项目根目录先探测目标芯片，并核对芯片 ID 与出厂 IEEE 地址：

```sh
PYTHONPATH=tools python3 -m xzg_cli.main probe-ti
```

随后执行擦除、写入、回读校验和复位：

```sh
PYTHONPATH=tools python3 -m xzg_cli.main flash-ti sensor/build/breezio-sensor.hex
```

Windows 也可以使用旧版 TI SmartRF Flash Programmer 写入同一 HEX 文件。

## UART 协议

UART0 使用 38400 baud、8 数据位、无校验和 1 停止位。默认遥测采用带 CRC-16 的帧：

```text
~BZ1,<length_hex>,<payload_hex>,<crc16_hex>
```

构建时设置 `LEGACY_TEXT_PROTOCOL=1` 可改用旧版
`@BATCH`、`@NODE`、`@END` 文本遥测。

命令与回执会端到端保留通过校验的命令 ID：

```text
@CMD,<command_id>,<node_id>,LAMP,ON
@ACK,<command_id>,<node_id>,OK,ON
```

在这条 CC2530 文本链路中，`command_id` 必须由 1–36 个 ASCII 字符组成，
可使用 `A-Z`、`a-z`、`0-9`、`.`、`_`、`:` 和 `-`。网关会直接拒绝空 ID、
超长 ID、含逗号或换行符的 ID，不会静默截断。

## 硬件引脚

| 信号 | 引脚 |
| --- | --- |
| DHT11 | P0.5 |
| 照度 ADC | P0.1 |
| 指示灯 D7 | P1.0 |
| 接收指示灯 D6 | P1.1 |
| UART0 | 38400 8N1 |

自定义星型协议见 [`docs/wireless-mesh.zh-CN.md`](docs/wireless-mesh.zh-CN.md)，
网关数据契约见 [`../docs/sensor-network-protocol.md`](../docs/sensor-network-protocol.md)。

## 许可

项目原创文件采用 Apache-2.0。Basic RF、HAL 和时钟相关源码保留 Texas Instruments
原始声明及限定条款，详见 [`../THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md)。
