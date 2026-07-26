#include <string.h>
#include "platform.h"
#include "hal_defs.h"
#include "uart.h"
#include "hex_protocol.h"

/*足够容纳最坏情况的遥测/确认payload（帧头+最长node_id+最长state字符串）*/
#define HEX_PROTO_MAX_PAYLOAD 40

static const char HEX_PROTO_DIGITS[] = "0123456789ABCDEF";

/*CRC-16/CCITT-FALSE：poly=0x1021，init=0xFFFF，不反转、无异或输出，
  需与网关侧protocol.py的crc16_ccitt_false()保持逐字节一致*/
static uint16 hexProtoCrc16(uint8 *data, uint8 len)
{
    uint16 crc = 0xFFFF;
    uint8 i, bit;
    for (i = 0; i < len; i++) {
        crc = (uint16)(crc ^ ((uint16)data[i] << 8));
        for (bit = 0; bit < 8; bit++) {
            if (crc & 0x8000) {
                crc = (uint16)((crc << 1) ^ 0x1021);
            } else {
                crc = (uint16)(crc << 1);
            }
        }
    }
    return crc;
}

static void hexProtoPutHexByte(uint8 value)
{
    Uart_Send_char(HEX_PROTO_DIGITS[(value >> 4) & 0x0F]);
    Uart_Send_char(HEX_PROTO_DIGITS[value & 0x0F]);
}

static void hexProtoPutHexU16(uint16 value)
{
    hexProtoPutHexByte(HI_UINT16(value));
    hexProtoPutHexByte(LO_UINT16(value));
}

/*发送"~BZ1,<len_hex>,<payload_hex>,<crc16_hex>\r\n"一整帧*/
static void hexProtoSendFrame(uint8 *payload, uint8 len)
{
    uint16 crc = hexProtoCrc16(payload, len);
    uint8 i;

    Uart_Send_String("~BZ1,");
    hexProtoPutHexU16((uint16)len);
    Uart_Send_char(',');
    for (i = 0; i < len; i++) {
        hexProtoPutHexByte(payload[i]);
    }
    Uart_Send_char(',');
    hexProtoPutHexU16(crc);
    Uart_Send_String("\r\n");
}

void hexProtoSendTelemetry(uint16 batchId, char *nodeId, uint16 seq,
                           int16 temperatureX10, int16 humidityX10,
                           uint16 light, uint16 batteryMv,
                           int8 rssi, uint8 flags)
{
    uint8 payload[HEX_PROTO_MAX_PAYLOAD];
    uint8 idx = 0;
    uint8 nodeIdLen = (uint8)strlen(nodeId);

    payload[idx++] = 1;      /* version */
    payload[idx++] = 0x01;   /* MSG_TELEMETRY */
    payload[idx++] = LO_UINT16(batchId);
    payload[idx++] = HI_UINT16(batchId);
    payload[idx++] = 1;      /* record_count：每帧只携带一条记录 */

    payload[idx++] = nodeIdLen;
    memcpy(&payload[idx], nodeId, nodeIdLen);
    idx = (uint8)(idx + nodeIdLen);

    payload[idx++] = LO_UINT16(seq);
    payload[idx++] = HI_UINT16(seq);
    payload[idx++] = LO_UINT16((uint16)temperatureX10);
    payload[idx++] = HI_UINT16((uint16)temperatureX10);
    payload[idx++] = LO_UINT16((uint16)humidityX10);
    payload[idx++] = HI_UINT16((uint16)humidityX10);
    payload[idx++] = LO_UINT16(light);
    payload[idx++] = HI_UINT16(light);
    payload[idx++] = LO_UINT16(batteryMv);
    payload[idx++] = HI_UINT16(batteryMv);
    payload[idx++] = (uint8)rssi;
    payload[idx++] = flags;

    hexProtoSendFrame(payload, idx);
}
