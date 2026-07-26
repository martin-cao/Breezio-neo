#include <stdio.h>
#include <string.h>
#include "platform.h"
#if defined(NODE_KIND_ILLU)
#include "light_sensor.h"
#else
#include "dht11.h"
#endif
#include "sys_init.h"
#include "uart.h"
#include "info.h"
#include "hal_defs.h"
#include "hex_protocol.h"
#if defined(NODE_ROLE_ROUTER) || defined(NODE_ROLE_SENSOR)
#include "hal_mcu.h"
#include "mesh_net.h"
#endif
/* ----------------------- 应用常量 ----------------------- */
#define RESPONSE_POLL_INTERVAL_MS    1
#define SAMPLE_INTERVAL_MS           1000
#define GATEWAY_ID                   "gw-01"
#ifndef NODE_ID
#define NODE_ID                      "n1"
#endif

static uint16 telemetry_seq = 0;
static uint16 batch_seq = 0;
static uint8 indicator_on = 0;

/*重定向printf输出到串口*/
int putchar(int ch)
{
    U0DBUF = ch;
    while(UTX0IF == 0);
    UTX0IF = 0;
    return ch;
}

static uint8 crc8_update(uint8 value, uint8 byte)
{
    uint8 i;
    value ^= byte;
    for (i = 0; i < 8; i++) {
        if (value & 0x80) {
            value = (uint8)((value << 1) ^ 0x07);
        } else {
            value = (uint8)(value << 1);
        }
    }
    return value;
}

static uint8 crc8_text(uint8 value, char *text)
{
    while (*text != '\0') {
        value = crc8_update(value, (uint8)*text++);
    }
    return value;
}

static void uart_send_line(char *line)
{
    Uart_Send_String(line);
    Uart_Send_String("\r\n");
}

#ifdef BOOT_TRACE
static void boot_trace(char *line)
{
    uart_send_line(line);
}
#else
#define boot_trace(line) do { } while (0)
#endif

#if defined(NODE_KIND_ILLU)
#if defined(LEGACY_TEXT_PROTOCOL)
/*将光照数据按旧文本@BATCH/@NODE/@END协议上报给网关（噪声串口线上容易被破坏，
  仅作为十六进制成帧协议的兼容回退，通过LEGACY_TEXT_PROTOCOL选择编译）*/
static void uart_send_sensor(unsigned int light)
{
    char header[32];
    char node[64];
    char end[16];
    uint8 crc;

    telemetry_seq++;
    batch_seq++;

    sprintf(header, "@BATCH,%s,%u,1", GATEWAY_ID, (unsigned int)batch_seq);
    sprintf(
        node,
        "@NODE,%s,%u,,,%u,,0,0",
        NODE_ID,
        (unsigned int)telemetry_seq,
        light
    );

    crc = crc8_text(0x00, header);
    crc = crc8_update(crc, (uint8)'\n');
    crc = crc8_text(crc, node);
    sprintf(end, "@END,%02X", (unsigned int)crc);

    uart_send_line(header);
    uart_send_line(node);
    uart_send_line(end);
}
#else
/*将光照数据按~BZ1,十六进制成帧协议上报给网关*/
static void uart_send_sensor(unsigned int light)
{
    telemetry_seq++;
    batch_seq++;
    hexProtoSendTelemetry(batch_seq, NODE_ID, telemetry_seq,
                          HEX_PROTO_MISSING_I16, HEX_PROTO_MISSING_I16,
                          (uint16)light, HEX_PROTO_MISSING_U16, 0, 0);
}
#endif
#else
#if defined(LEGACY_TEXT_PROTOCOL)
/*将传感数据按旧文本@BATCH/@NODE/@END协议上报给网关（同上，兼容回退）*/
static void uart_send_sensor(uint8 humidity, uint8 temp)
{
    char header[32];
    char node[64];
    char end[16];
    uint8 crc;

    telemetry_seq++;
    batch_seq++;

    sprintf(header, "@BATCH,%s,%u,1", GATEWAY_ID, (unsigned int)batch_seq);
    sprintf(
        node,
        "@NODE,%s,%u,%u,%u,,,0,0",
        NODE_ID,
        (unsigned int)telemetry_seq,
        (unsigned int)temp * 10u,
        (unsigned int)humidity * 10u
    );

    crc = crc8_text(0x00, header);
    crc = crc8_update(crc, (uint8)'\n');
    crc = crc8_text(crc, node);
    sprintf(end, "@END,%02X", (unsigned int)crc);

    uart_send_line(header);
    uart_send_line(node);
    uart_send_line(end);
}
#else
/*将传感数据按~BZ1,十六进制成帧协议上报给网关*/
static void uart_send_sensor(uint8 humidity, uint8 temp)
{
    telemetry_seq++;
    batch_seq++;
    hexProtoSendTelemetry(batch_seq, NODE_ID, telemetry_seq,
                          (int16)((uint16)temp * 10u), (int16)((uint16)humidity * 10u),
                          HEX_PROTO_MISSING_U16, HEX_PROTO_MISSING_U16, 0, 0);
}
#endif
#endif

#if defined(NODE_ROLE_ROUTER)
#if defined(LEGACY_TEXT_PROTOCOL)
/*把远程节点（通过RF上报）的数据按旧文本@BATCH/@NODE/@END协议转发给网关
  （兼容回退），节点种类在运行时判断（远程节点的NODE_KIND可能与本机不同），
  因此和按编译期NODE_KIND_ILLU分支的uart_send_sensor()是分开的两个函数*/
static void send_remote_report(char *node_id, uint16 seq, uint8 node_kind, uint16 value1, uint16 value2)
{
    char header[32];
    char node[64];
    char end[16];
    uint8 crc;

    batch_seq++;

    sprintf(header, "@BATCH,%s,%u,1", GATEWAY_ID, (unsigned int)batch_seq);
    if (node_kind == MESH_KIND_ILLU) {
        sprintf(node, "@NODE,%s,%u,,,%u,,0,0", node_id, (unsigned int)seq, (unsigned int)value1);
    } else {
        sprintf(
            node,
            "@NODE,%s,%u,%u,%u,,,0,0",
            node_id,
            (unsigned int)seq,
            (unsigned int)value1,
            (unsigned int)value2
        );
    }

    crc = crc8_text(0x00, header);
    crc = crc8_update(crc, (uint8)'\n');
    crc = crc8_text(crc, node);
    sprintf(end, "@END,%02X", (unsigned int)crc);

    uart_send_line(header);
    uart_send_line(node);
    uart_send_line(end);
}
#else
/*把远程节点（通过RF上报）的数据按~BZ1,十六进制成帧协议转发给网关*/
static void send_remote_report(char *node_id, uint16 seq, uint8 node_kind, uint16 value1, uint16 value2)
{
    batch_seq++;
    if (node_kind == MESH_KIND_ILLU) {
        hexProtoSendTelemetry(batch_seq, node_id, seq,
                              HEX_PROTO_MISSING_I16, HEX_PROTO_MISSING_I16,
                              value1, HEX_PROTO_MISSING_U16, 0, 0);
    } else {
        hexProtoSendTelemetry(batch_seq, node_id, seq,
                              (int16)value1, (int16)value2,
                              HEX_PROTO_MISSING_U16, HEX_PROTO_MISSING_U16, 0, 0);
    }
}
#endif

/*mesh_net.c在路由器收到一条远程节点数据上报时回调*/
void mesh_on_data(char *nodeId, uint8 nodeKind, uint16 seq, uint16 value1, uint16 value2)
{
    send_remote_report(nodeId, seq, nodeKind, value1, value2);
}
#endif

static uint16 rx_blink_ms = 0;

/*根据开关窗状态设置LED亮灭*/
static void sinkSetIndicatorState(uint8 isOpen)
{
    indicator_on = isOpen ? 1 : 0;
    if (isOpen) {
        D7 = 0;  // LED 常亮
    } else {
        D7 = 1;  // LED 熄灭
    }
}

static unsigned char ascii_upper(unsigned char ch)
{
    if (ch >= 'a' && ch <= 'z') {
        return (unsigned char)(ch - ('a' - 'A'));
    }
    return ch;
}

static uint8 str_eq_ci(char *left, char *right)
{
    while (*left != '\0' && *right != '\0') {
        if (ascii_upper((unsigned char)*left) != ascii_upper((unsigned char)*right)) {
            return 0;
        }
        left++;
        right++;
    }
    return *left == '\0' && *right == '\0';
}

static char *next_csv_field(char **cursor)
{
    char *start = *cursor;
    char *comma = strchr(start, ',');
    if (comma != NULL) {
        *comma = '\0';
        *cursor = comma + 1;
    } else {
        *cursor = start + strlen(start);
    }
    return start;
}

/*命令回执保留完整 command_id，便于后端将 ACK 对应到原控制记录。遥测仍可使用
  ~BZ1 十六进制帧；ACK 使用短文本帧，避免不可逆的 16 位哈希映射。*/
static void send_command_ack_for(char *command_id, char *node_id, char *result, char *state)
{
    Uart_Send_String("@ACK,");
    Uart_Send_String(command_id);
    Uart_Send_String(",");
    Uart_Send_String(node_id);
    Uart_Send_String(",");
    Uart_Send_String(result);
    Uart_Send_String(",");
    Uart_Send_String(state);
    Uart_Send_String("\r\n");
}

static void send_command_ack(char *command_id, char *result, char *state)
{
    send_command_ack_for(command_id, NODE_ID, result, state);
}

static void handle_legacy_command(char *frame)
{
    if (strstr(frame, "OD1=1") != NULL) {
        sinkSetIndicatorState(1);
        send_command_ack("legacy", "OK", "ON");
    } else if (strstr(frame, "CD1=1") != NULL) {
        sinkSetIndicatorState(0);
        send_command_ack("legacy", "OK", "OFF");
    }
}

/*设备/动作匹配与本地指示灯驱动，UART指令和RF转发指令共用这一处逻辑*/
static void process_command(char *device, char *action, char *result_out, char *state_out)
{
    if (!str_eq_ci(device, "LAMP") &&
        !str_eq_ci(device, "LED") &&
        !str_eq_ci(device, "WINDOW")) {
        strcpy(result_out, "ERROR");
        strcpy(state_out, "UNKNOWN_DEVICE");
        return;
    }

    if (str_eq_ci(action, "ON") || str_eq_ci(action, "OPEN")) {
        sinkSetIndicatorState(1);
    } else if (str_eq_ci(action, "OFF") || str_eq_ci(action, "CLOSE")) {
        sinkSetIndicatorState(0);
    } else if (str_eq_ci(action, "TOGGLE")) {
        sinkSetIndicatorState(indicator_on ? 0 : 1);
    } else {
        strcpy(result_out, "ERROR");
        strcpy(state_out, "UNKNOWN_ACTION");
        return;
    }

    strcpy(result_out, "OK");
    strcpy(state_out, indicator_on ? "ON" : "OFF");
}

static void handle_new_command(char *frame)
{
    char *cursor;
    char *command_id;
    char *target_node_id;
    char *device;
    char *action;
    char result[16];
    char state[16];

    if (strncmp(frame, "@CMD,", 5) != 0) {
        return;
    }

    cursor = frame + 5;
    command_id = next_csv_field(&cursor);
    target_node_id = next_csv_field(&cursor);
    device = next_csv_field(&cursor);
    action = next_csv_field(&cursor);

    if (str_eq_ci(target_node_id, NODE_ID) || str_eq_ci(target_node_id, "*")) {
        process_command(device, action, result, state);
        send_command_ack(command_id, result, state);
        return;
    }

#if defined(NODE_ROLE_ROUTER)
    /*目标不是本机：如果是已入网的远程节点，通过RF转发指令并等待（带重试）确认*/
    if (meshNetLookupNode(target_node_id)) {
        if (meshNetSendCommand(target_node_id, command_id, device, action, result, state)) {
            send_command_ack_for(command_id, target_node_id, result, state);
        } else {
            send_command_ack_for(command_id, target_node_id, "ERROR", "TIMEOUT");
        }
    }
#endif
}

static void handle_command_frame(char *frame)
{
    handle_legacy_command(frame);
    handle_new_command(frame);
}

#if defined(NODE_ROLE_SENSOR)
/*mesh_net.c在传感节点收到路由器转发来的指令时回调；D6借用原本的
  UART接收指示灯逻辑改为标示RF指令到达*/
void mesh_on_command(char *commandId, char *device, char *action, char *resultOut, char *stateOut)
{
    (void)commandId;
    D6 = 0;
    rx_blink_ms = 50;
    process_command(device, action, resultOut, stateOut);
}
#endif

/*根据串口接收情况闪烁D6*/
static void update_rx_blink(uint16 elapsed_ms)
{
    if (rx_blink_ms > 0) {
        if (rx_blink_ms > elapsed_ms) {
            rx_blink_ms -= elapsed_ms;
        } else {
            rx_blink_ms = 0;
        }
    }
    if (rx_blink_ms == 0) {
        D6 = 1;  // 熄灭
    }
}

/*单次轮询最多处理的字节数，避免RX线路噪声/持续误码导致该函数长时间不返回，
  从而饿死主循环中的周期性上报逻辑（例如调试器断开时相邻信号线悬空引入的噪声）。*/
#define RX_POLL_MAX_BYTES 32

/*轮询串口接收来自网关的控制指令*/
static void sinkPollGatewayCommand(void)
{
    static char cmdBuf[72];
    static uint8 idx = 0;
    unsigned char ch;
    uint8 processed = 0;

    while (processed < RX_POLL_MAX_BYTES && Uart_Recv_char_nonblock(&ch)) {
        processed++;
        // 任意串口字节到达时闪一下D6，便于确认RX是否正常。
        D6 = 0;
        rx_blink_ms = 50;

        if (idx == 0 && (ch == 'O' || ch == 'o')) {
            sinkSetIndicatorState(1);
            send_command_ack("legacy", "OK", "ON");
            continue;
        }
        if (idx == 0 && (ch == 'C' || ch == 'c')) {
            sinkSetIndicatorState(0);
            send_command_ack("legacy", "OK", "OFF");
            continue;
        }

        if (ch == '{' || ch == '@') {
            idx = 0;
            cmdBuf[idx++] = (char)ch;
            continue;
        }
        if (idx == 0) {
            continue;
        }
        if (idx < (sizeof(cmdBuf) - 1)) {
            cmdBuf[idx++] = (char)ch;
        } else {
            idx = 0;
            continue;
        }
        if (ch == '}' || ch == '\r' || ch == '\n') {
            if (ch == '\r' || ch == '\n') {
                cmdBuf[idx - 1] = '\0';
            } else {
                cmdBuf[idx] = '\0';
            }
            if (cmdBuf[0] == '\0') {
                idx = 0;
                continue;
            }
            handle_command_frame(cmdBuf);
            idx = 0;
        }
    }
}

/*单节点逻辑：周期采集并通过串口上报，支持串口控制指示灯*/
static void singleNode(void)
{
    uint16 elapsed = 0;

    sinkSetIndicatorState(0);
    while (1) {
        sinkPollGatewayCommand();
        update_rx_blink(RESPONSE_POLL_INTERVAL_MS);

        if (elapsed >= SAMPLE_INTERVAL_MS) {
            sinkPollGatewayCommand();
#if defined(NODE_KIND_ILLU)
            light_sensor_update();
            sinkPollGatewayCommand();
            uart_send_sensor(light_sensor_value());
#else
            dht11_update();
            sinkPollGatewayCommand();
            uart_send_sensor(dht11_humidity(), dht11_temp());
#endif
            elapsed = 0;
        }

        halWait(RESPONSE_POLL_INTERVAL_MS);
        elapsed += RESPONSE_POLL_INTERVAL_MS;
    }
}
#if defined(NODE_ROLE_ROUTER)
/*路由器节点：本机传感器数据+从RF收集来的远程节点数据都通过UART上报给网关，
  同时把网关下发的@CMD转发给对应的远程节点*/
static void routerNode(void)
{
    uint16 elapsed = 0;

    sinkSetIndicatorState(0);
    while (1) {
        sinkPollGatewayCommand();
        update_rx_blink(RESPONSE_POLL_INTERVAL_MS);
        meshNetPoll(RESPONSE_POLL_INTERVAL_MS);

        if (elapsed >= SAMPLE_INTERVAL_MS) {
            sinkPollGatewayCommand();
#if defined(NODE_KIND_ILLU)
            light_sensor_update();
            sinkPollGatewayCommand();
            uart_send_sensor(light_sensor_value());
#else
            dht11_update();
            sinkPollGatewayCommand();
            uart_send_sensor(dht11_humidity(), dht11_temp());
#endif
            elapsed = 0;
        }

        halWait(RESPONSE_POLL_INTERVAL_MS);
        elapsed += RESPONSE_POLL_INTERVAL_MS;
    }
}
#endif

#if defined(NODE_ROLE_SENSOR)
/*传感节点：只用电池/电源供电，不接串口。入网后周期性地把本地采集的数据通过
  RF上报给路由器，并响应路由器转发来的指令*/
static void sensorNode(void)
{
    uint16 elapsed = 0;

    sinkSetIndicatorState(0);
    while (1) {
        meshNetPoll(RESPONSE_POLL_INTERVAL_MS);
        update_rx_blink(RESPONSE_POLL_INTERVAL_MS);

        if (!meshNetIsJoined()) {
            elapsed = 0;
        } else if (elapsed >= SAMPLE_INTERVAL_MS) {
#if defined(NODE_KIND_ILLU)
            light_sensor_update();
            meshNetSendData(light_sensor_value(), 0);
#else
            dht11_update();
            meshNetSendData((uint16)dht11_temp() * 10u, (uint16)dht11_humidity() * 10u);
#endif
            elapsed = 0;
        }

        halWait(RESPONSE_POLL_INTERVAL_MS);
        elapsed += RESPONSE_POLL_INTERVAL_MS;
    }
}
#endif

/*主函数
-------------------------------------------------------*/
/*主入口，根据节点角色/种类运行路由器聚合、传感节点组网或单机传感逻辑*/
PLATFORM_MAIN
{
#if defined(BRINGUP_STAGE)
    unsigned char bringupCh;
#if BRINGUP_STAGE >= 2
    xtal_init();
#endif
    led_init();
#if BRINGUP_STAGE >= 3
    uart0_init(0x00, 0x00);
#endif
    while (1) {
        D7 = !D7;
#if BRINGUP_STAGE >= 3
        Uart_Send_String("HB\r\n");
#endif
#if BRINGUP_STAGE >= 4
        if (Uart_Recv_char_nonblock(&bringupCh)) {
            D6 = !D6;
        }
#endif
        halWait(200);
    }
#elif defined(NODE_ROLE_ROUTER) || defined(NODE_ROLE_SENSOR)
    led_init();
#if defined(NODE_KIND_ILLU)
    light_sensor_io_init();
#else
    dht11_io_init();
#endif
    uart0_init(0x00, 0x00);
    boot_trace("BT:UART");
#ifdef SPI_LCD
    lcd_dis();
#endif
#if defined(NODE_KIND_ILLU)
    boot_trace("BT:MESH0:ILLU");
    meshNetInit(MESH_KIND_ILLU);
#else
    boot_trace("BT:MESH0:TEMPHUMI");
    meshNetInit(MESH_KIND_TEMPHUMI);
#endif
    boot_trace("BT:MESH1");

#if defined(NODE_ROLE_ROUTER)
    boot_trace("BT:ROUTER");
    routerNode();
#else
    boot_trace("BT:SENSOR");
    sensorNode();
#endif

#else
    xtal_init();
    led_init();
#if defined(NODE_KIND_ILLU)
    light_sensor_io_init();
#else
    dht11_io_init();
#endif
    uart0_init(0x00, 0x00);
#ifdef SPI_LCD
    lcd_dis();
#endif

    singleNode();
#endif

#if defined(__SDCC)
    return 0;
#endif
}
