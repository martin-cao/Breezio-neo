# smartwin gateway（主机侧）

这是 Breezio window 节点的一个小型、轻依赖主机 gateway。它读取
`$BRZ2` USB 串口协议（[`../docs/PROTOCOL.zh-CN.md`](../docs/PROTOCOL.zh-CN.md)），
转换成结构化 JSON，并把 window/buzzer 命令发回节点。

- `smartwin_gateway/protocol.py` - 纯 `$BRZ2` codec（解析帧、编码命令、XOR 校验和）。
  无依赖；对应固件里的 `src/proto.rs`。
- `smartwin_gateway/gateway.py` - stream -> normalized JSON，以及串口 helper。
- `smartwin_gateway/__main__.py` - `monitor` / `send` CLI。

只有访问真实串口时才需要 `pyserial`；其他部分（以及整个测试套件）只依赖标准库。

## 监控实时遥测

```sh
# 真实硬件（需要：pip install pyserial）
python -m smartwin_gateway monitor --serial-port /dev/cu.usbserial-XXXX

# 无硬件 - 回放捕获日志
python -m smartwin_gateway monitor --stdin < capture.txt
```

每一行输入/输出：

```text
$BRZ2,T,1,24.5,48.0,73,612,CLOSED,0*01
{"captured_at":"...","type":"telemetry","node_id":"win-01","seq":1,
 "temperature_c":24.5,"humidity_pct":48.0,"brightness_pct":73,
 "air_quality_mv":612,"window_open":false,"alarm":false,"air_quality_band":"GOOD"}
```

## 发送命令

```sh
# 打开 / 关闭 / 切换模拟窗户
python -m smartwin_gateway send --serial-port /dev/cu.usbserial-XXXX --device WINDOW --action OPEN

# 静音空气质量报警，或发出一次测试蜂鸣
python -m smartwin_gateway send --serial-port /dev/cu.usbserial-XXXX --device BUZZER --action MUTE

# dry-run：只打印已封帧命令，不发送
python -m smartwin_gateway send --stdout --device WINDOW --action TOGGLE
# -> $BRZ2,CMD,12345,WINDOW,TOGGLE*..
```

节点执行动作，更新 OLED + RGB LED，并回复匹配的 `ACK`（可在 `monitor` 中看到）。

## 查找串口

- macOS：`ls /dev/cu.usbserial-* /dev/cu.wchusbserial*`
- Linux：`ls /dev/ttyUSB* /dev/ttyACM*`（CH340 通常是 `ttyUSB0`）

使用 **CH340** 端口（板子左侧 USB-C），不要用 USB-JTAG 端口 - 日志走 JTAG 端口，协议数据
走 CH340。

## 测试

```sh
cd sensor-new/gateway
PYTHONPATH=. python -m unittest discover -s tests -v
```
