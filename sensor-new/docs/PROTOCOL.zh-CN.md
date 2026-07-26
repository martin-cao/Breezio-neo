# Breezio-2 行协议（`$BRZ2`）

这是 ESP32-S3 节点和 Python Gateway 之间使用的一个小型、可读、类似 NMEA
的行协议。主部署通过 **Wi-Fi/TCP** 承载；固件也会在 **USB 串口
（UART0 -> CH340 -> 左侧 USB-C 口）** 上输出同样的协议，便于以
**115200 baud, 8-N-1** 进行本地诊断。

它刻意保持纯 ASCII，这样你可以在任意串口监视器里直接看懂；同时每一行都带完整性
校验和，网关可以静默丢弃线上损坏的字节。

## 行格式

```text
$BRZ2,<TYPE>,<field>,<field>,...*<CS>\r\n
```

- 以 `$` 开头，以 `*` + 2 位十六进制校验和 + 换行结尾。
- `<CS>` = **`$` 和 `*` 之间所有字节的 XOR**（不包含 `$` 和 `*`），用 2 个大写
  十六进制字符表示，和 NMEA GPS 语句使用的校验方式相同。两端实现都很简单。
- 字段用逗号分隔。缺失的传感器值使用字面量 `NA`。
- 接收方会忽略 `$` 之前的所有内容，并在下一个 `$` 重新同步，因此噪声/垃圾字节
  不会让数据流永久失步。
- `\r` 是可选的；网关也接受只有 `\n` 的行。

## 节点 -> 主机

### 遥测（`T`）- 约每 2 秒发送一次

```text
$BRZ2,T,<seq>,<temp>,<hum>,<light>,<aq>,<win>,<alarm>*CS
```

| 字段 | 含义 | 示例 |
|------|------|------|
| `seq` | u32 单调递增计数器 | `41` |
| `temp` | 温度，单位 °C，1 位小数，或 `NA` | `24.5` |
| `hum` | 相对湿度百分比，1 位小数，或 `NA` | `48.0` |
| `light` | 光敏电阻原始读数换算到 0-100 的整数指标——**不是经过校准的照度（lux）** | `73` |
| `aq` | MQ-135 空气质量读数，原始毫伏值 0-3300 | `612` |
| `win` | 窗户状态：`OPEN` 或 `CLOSED` | `CLOSED` |
| `alarm` | 蜂鸣器报警激活时为 `1`，否则为 `0` | `0` |

示例：`$BRZ2,T,41,24.5,48.0,73,612,CLOSED,0*35`

> `light` 是直接由光敏电阻原始 ADC 电压换算出的未校准指标（见 `src/sensors.rs`
> 中的 `LIGHT_INVERTED`），**不是**真正的照度单位 lux。要变成真正的 lux 需要
> 针对每块板子做校准测量，这里没有人做过，所以不叫它 lux。

> `aq` 毫伏值越高，表示气体浓度越高（氨气/苯/乙醇/烟雾/CO2）。MQ-135 未校准，
> 所以主机只会把 `aq` 映射成粗略的 GOOD/FAIR/POOR 档位，而不是真实 ppm。
> 当 `aq` 超过板载阈值时，节点会置 `alarm=1`。

### 启动横幅（`HELLO`）- 启动时发送一次

```text
$BRZ2,HELLO,<fw_version>,<node_id>*CS
```

示例：`$BRZ2,HELLO,1.0.0,win-01*7B`

### 命令确认（`ACK`）

```text
$BRZ2,ACK,<id>,<device>,<result>,<state>*CS
```

| 字段 | 含义 | 示例 |
|------|------|------|
| `id` | 回显正在响应的命令 `id` | `7` |
| `device` | `WINDOW` 或 `BUZZER` | `WINDOW` |
| `result` | `OK` 或 `ERR` | `OK` |
| `state` | 动作后的设备状态 | `OPEN` / `CLOSED` / `MUTED` |

示例：`$BRZ2,ACK,7,WINDOW,OK,OPEN*36`

## 主机 -> 节点

### 命令（`CMD`）

```text
$BRZ2,CMD,<id>,<device>,<action>*CS
```

| `device` | 有效 `action` 值 | 效果 |
|----------|------------------|------|
| `WINDOW` | `OPEN`, `CLOSE`, `TOGGLE` | 打开/关闭模拟窗户（RGB LED + OLED） |
| `BUZZER` | `MUTE`, `TEST` | 静音当前报警 / 响一次短测试音 |

示例：`$BRZ2,CMD,7,WINDOW,OPEN*1D`

节点执行动作，更新 OLED 和板载 RGB LED（绿色 = 打开，红色 = 关闭），并返回匹配的
`ACK`。

## 为什么不用旧的 `~BZ1` 十六进制协议？

`~BZ1` 面向需要紧凑打包和 CRC-16 的 CC2530 传输。ESP32-S3 使用 TCP 或短距离
USB 链路，因此 `$BRZ2` 优先保证可读性，同时保留 XOR 校验和与行级重新同步。

## Wi-Fi/TCP 传输

使用 `cargo build --release --features wifi` 构建后，节点以 STA 模式接入
2.4 GHz Wi-Fi，通过 DHCP 获取地址，并使用 mDNS/DNS-SD 查找
`_breezio-gateway._tcp.local`。发现失败时会回退到编译期设置的
`BREEZIO_GATEWAY_HOST` 和 `BREEZIO_GATEWAY_PORT`。

节点主动连接 Gateway 的 TCP 监听端口，在同一连接上发送 `HELLO`、遥测和 ACK，
并接收控制命令。UART0 同时保留相同的 `$BRZ2` 数据流。主机侧可按以下方式启动：

```sh
PYTHONPATH=gateway \
uv run --with paho-mqtt --with zeroconf \
  python -m breezio_gateway.main \
  --wifi-listen 0.0.0.0:5568 \
  --sensor-dialect brz2 \
  --mqtt-host 127.0.0.1
```
