#ifndef BREEZIO_HEX_PROTOCOL_H
#define BREEZIO_HEX_PROTOCOL_H

#include "platform.h"

/*字段缺失时使用的哨兵值，需与网关侧protocol.py的HEX_MISSING_*保持一致*/
#define HEX_PROTO_MISSING_I16 0x7FFF
#define HEX_PROTO_MISSING_U16 0xFFFF
#define HEX_PROTO_MISSING_I8  (-128)

/*通过"~BZ1,<len_hex>,<payload_hex>,<crc16_hex>\r\n"成帧协议上报一条遥测记录
  （record_count固定为1，多节点各自独立成帧，不合并批次，语义与原文本
  @BATCH/@NODE/@END协议一致）。temperatureX10/humidityX10为
  HEX_PROTO_MISSING_I16、light/batteryMv为HEX_PROTO_MISSING_U16、rssi为
  HEX_PROTO_MISSING_I8表示该字段缺失*/
void hexProtoSendTelemetry(uint16 batchId, char *nodeId, uint16 seq,
                           int16 temperatureX10, int16 humidityX10,
                           uint16 light, uint16 batteryMv,
                           int8 rssi, uint8 flags);

#endif
