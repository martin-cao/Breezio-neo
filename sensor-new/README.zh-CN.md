# Breezio sensor-new - ESP32-S3 "智能窗户"节点（embedded Rust）

这是 Goouuu **ESP32-S3-DevKitC-1 (N16R8)** 的固件，会把
[`docs/WIRING.zh-CN.md`](docs/WIRING.zh-CN.md) 中接好的面包板变成一个
"智能窗户"节点。它会：

- 读取**温度 + 湿度**（DHT11，GPIO4），
- 读取**亮度**（光敏电阻 -> GPIO8 上的 ADC1），
- 读取**空气质量**（MQ-135 -> GPIO9 上的 ADC1），并在空气质量变差时触发
  **蜂鸣器报警**（GPIO5），
- 在 **OLED** 上显示实时**状态屏幕**（SSD1306，GPIO6/7 上的 I2C），
- 模拟一个可由主机打开/关闭的**窗户**，状态显示在板载 **RGB LED**（GPIO48）和 OLED 上，
- 通过 **Wi-Fi/TCP** 和 `$BRZ2` 行协议连接 Python Gateway，支持
  mDNS/DNS-SD 发现与断线重连；
- 同时在 **USB 串口** 输出相同协议，便于本地诊断。

主机侧主 Gateway 位于 [`../gateway/`](../gateway/)，[`gateway/`](gateway/)
中另有一个轻量 USB 串口检查工具。

## 架构

```text
main.rs        循环：轮询命令 -> 采样传感器（2 s）-> 报警 -> 渲染 -> 发出遥测
├─ dht11.rs    bit-banged DHT11 one-wire driver（通过 esp_timer_get_time 做微秒计时）
├─ sensors.rs  ADC mV -> 亮度百分比、空气质量阈值（需要校准的数字）
├─ led.rs      通过 RMT 控制 WS2812 RGB（绿色=打开，琥珀色=关闭，闪烁红色=空气报警）
├─ buzzer.rs   有源蜂鸣器，高电平触发
├─ display.rs  SSD1306 OLED 状态屏幕（embedded-graphics）
├─ link.rs     UART0 <-> $BRZ2 lines（遥测输出，命令输入）
├─ wifi.rs     Wi-Fi STA 初始化与编译期配置
├─ discovery.rs  mDNS/DNS-SD Gateway 发现
├─ net_link.rs TCP 传输与断线重连
├─ proto.rs    $BRZ2 编解码 + NMEA 风格 XOR 校验和
└─ app.rs      共享 AppState
```

控制台日志（`log::info!`）走 **USB Serial/JTAG** 端口；`$BRZ2` 数据流走
**UART0 -> CH340 -> 左侧 USB-C 口**。两者不会混在一起，所以 gateway 会看到干净的协议流。
这个设置在 `sdkconfig.defaults` 中。

## 引脚映射

| 信号 | GPIO | 说明 |
|------|------|------|
| 光敏电阻 `AO` | GPIO8 | ADC1_CH7 |
| MQ-135 `AO` | GPIO9 | ADC1_CH8 |
| DHT11 `DATA` | GPIO4 | 模块上拉 |
| 蜂鸣器 `I/O` | GPIO5 | HIGH = 响 |
| OLED `SDA` / `SCK` | GPIO6 / GPIO7 | I2C0 @ 400 kHz，地址 0x3C |
| RGB LED | GPIO48 | 板载 WS2812 |
| UART0 TX / RX | GPIO43 / GPIO44 | 连接 CH340（左侧 USB-C） |

所有传感器模块都使用 **3.3V** 供电（原因见接线指南）。

## 构建和烧录

前置条件：**Espressif Rust toolchain**（`espup install`，然后
`. ~/export-esp.sh`），以及 `ldproxy` 和 `espflash`：

```sh
cargo install ldproxy espflash
```

先设置本地网络参数，构建主 Wi-Fi 固件：

```sh
export BREEZIO_WIFI_SSID=<ssid>
export BREEZIO_WIFI_FALLBACK_SSID=<fallback-ssid>
export BREEZIO_WIFI_PASS=<password>
export BREEZIO_GATEWAY_HOST=<gateway-ip>
cargo build --release --features wifi
```

插入 CH340/左侧 USB-C 线后，烧录并打开 monitor：

```sh
cargo run --release --features wifi
```

只有构建 USB 串口诊断版本时才省略 `--features wifi`。

## 不通过 gateway 试运行

用任意串口监视器以 **115200 baud** 打开 CH340 串口。你应该看到：

```text
$BRZ2,HELLO,1.0.0,win-01*7B
$BRZ2,T,1,24.5,48.0,73,612,CLOSED,0*01
...
```

输入一行命令（需要校验和，gateway 会帮你计算）：

```text
$BRZ2,CMD,7,WINDOW,OPEN*1D
```

随后 LED 会变绿，OLED 显示 `Win:OPEN`，并返回
`$BRZ2,ACK,7,WINDOW,OK,OPEN*36`。

## 校准参数

所有需要在 bench 上调的参数都在 [`src/sensors.rs`](src/sensors.rs)：
`LIGHT_INVERTED`（如果亮度读数方向反了就翻转），以及 `AQ_ALARM_MV` /
`AQ_CLEAR_MV`（空气质量报警开启/关闭阈值，带 hysteresis）。信任 MQ-135 数字前，先让它
预热几分钟。

这个固件按 `esp-idf-hal 0.45` 和 `esp-idf-svc 0.51` 编写。建议按
`rust-toolchain.toml` 固定工具链；如果依赖补丁版本改变了 ADC 接口，应先核对
`main.rs` 中两个通道的配置，再执行烧录。
