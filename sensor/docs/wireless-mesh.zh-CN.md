# CC2530 自定义星型网络

CC2530 无线支线使用 TI Basic RF 完成 IEEE 802.15.4 收发，并在其上实现入网、遥测、
重试和控制协议。该实现未使用 Z-Stack，也不包含 Zigbee 多跳路由。

## 构建角色

```sh
make clean
make NODE_ROLE=ROUTER NODE_KIND=TEMPHUMI NODE_ID=n1

make clean
make NODE_ROLE=SENSOR NODE_KIND=ILLU
```

`NODE_KIND` 可选 `TEMPHUMI` 或 `ILLU`。Router 保留本地传感器，维护最多 8 个无线节点，
并把远端读数转发到 UART。Sensor 根据出厂 IEEE 地址生成短地址，加入 Router 后上报
本地读数。

## 无线参数

| 参数 | 值 |
| --- | --- |
| PAN ID | `0x2530` |
| 信道 | 26 |
| 广播地址 | `0xFFFF` |
| 最大登记节点数 | 8 |
| 接收缓冲区 | 80 字节 |

SDCC 版本在主循环中轮询无线接收标志，并使用应用层确认和有限次数重试。

## 消息类型

| 编号 | 方向 | 含义 |
| --- | --- | --- |
| `0x01` | Sensor 到 Router | 入网请求 |
| `0x02` | Router 到 Sensor | 入网确认 |
| `0x03` | Sensor 到 Router | 传感数据 |
| `0x04` | Router 到 Sensor | 数据确认 |
| `0x05` | Router 到 Sensor | 控制命令 |
| `0x06` | Sensor 到 Router | 命令确认 |

入网请求携带节点短地址、出厂 IEEE 地址和传感器类型。Router 从 `n2` 开始分配应用节点
编号，`n1` 保留给 Router 的本地传感器。

传感数据格式为：

```text
type | sender_addr | seq | sensor_kind | value1 | value2
```

温湿度使用十分之一为单位，照度节点使用 `value1` 保存 ADC 读数。

## 可靠性设计

- 入网请求使用与节点地址相关的错峰重试。
- 传感数据最多发送三次，并等待相同序列号的数据确认。
- 连续三次数据失败后，Sensor 重新入网。
- Router 下发命令时最多重试三次。
- 命令和回执缓冲区能够容纳当前固定字段。
- Router 向网关回复时保留原始命令 ID。

## 硬件验证清单

1. 分别构建 Router 与 Sensor，并校验 HEX。
2. 烧录两块板，记录各自出厂 IEEE 地址。
3. 先给 Router 上电，再给 Sensor 上电。
4. 观察入网请求与分配的应用节点编号。
5. 将上报值与传感器现场状态对照。
6. 下发指示灯命令并核对回执。
7. 分别断电重启，检查恢复是否在有限时间内完成。

面向网关的 UART 与 MQTT 格式见
[`../../docs/sensor-network-protocol.md`](../../docs/sensor-network-protocol.md)。
