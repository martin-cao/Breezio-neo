#include <stdio.h>
#include <string.h>
#include "platform.h"
#include "hal_defs.h"
#include "hal_mcu.h"
#include "hal_rf.h"
#include "basic_rf.h"
#include "mesh_net.h"

/*网络共享参数：所有节点固定使用同一PAN ID / 信道。信道26位于常见2400MHz
  WiFi信道(1/6/11)占用频段之外，实测比信道20（落在WiFi信道6正中间）干净得多*/
#define MESH_PAN_ID     0x2530
#define MESH_CHANNEL    26
#define MESH_BROADCAST  0xFFFF

/*消息类型（负载首字节）*/
#define MSG_JOIN_REQUEST 0x01
#define MSG_JOIN_ACCEPT  0x02
#define MSG_DATA         0x03
#define MSG_DATA_ACK     0x04
#define MSG_CMD          0x05
#define MSG_CMD_ACK      0x06

/*定长命令ID字段可完整保存36字符UUID及结尾NUL，并留出少量格式余量。
  最大CMD_ACK负载为75字节，低于Basic RF的103字节上限。*/
#define MESH_CMDID_LEN   40
#define MESH_STR_LEN     16
#define MESH_RECV_BUF    80

static uint16 sMyAddr;
static uint8  sMyKind;

/*根据出厂IEEE地址派生一个大概率唯一、跨复位稳定的16位短地址，
  避免落在保留值0x0000/0xFFFF上*/
static uint16 deriveShortAddr(unsigned char *ieee)
{
    uint16 addr = 0;
    uint8 i;
    for (i = 0; i < 8; i += 2) {
        addr ^= (uint16)(((uint16)ieee[i] << 8) | ieee[i + 1]);
    }
    if (addr == 0x0000 || addr == 0xFFFF) {
        addr ^= 0xA5A5;
    }
    return addr;
}

/*从size字节的定长字段里安全地取出一个以NUL结尾的字符串（打包/解包共用）*/
static void meshCopyStr(char *dest, char *src, uint8 size)
{
    uint8 i = 0;
    if (size == 0) {
        return;
    }
    while (i < (uint8)(size - 1) && src[i] != '\0') {
        dest[i] = src[i];
        i++;
    }
    while (i < size) {
        dest[i] = '\0';
        i++;
    }
}

static uint8 sendMsg(uint16 destAddr, uint8 *payload, uint8 len)
{
    return basicRfSendPacket(destAddr, payload, len);
}

static void handleMessage(uint8 *buf, uint8 len);

#if defined(NODE_ROLE_ROUTER)

#define MESH_MAX_NODES 8

typedef struct {
    uint16 addr;
    char nodeId[4];
    uint8 nodeKind;
    uint8 used;
} MeshNodeEntry;

static MeshNodeEntry sNodes[MESH_MAX_NODES];
static uint8 sNextNodeNumber;

static char sPendingCmdId[MESH_CMDID_LEN];
static char sPendingCmdResult[MESH_STR_LEN];
static char sPendingCmdState[MESH_STR_LEN];
static uint8 sPendingCmdAckReceived;

static MeshNodeEntry *findNodeByAddr(uint16 addr)
{
    uint8 i;
    for (i = 0; i < MESH_MAX_NODES; i++) {
        if (sNodes[i].used && sNodes[i].addr == addr) {
            return &sNodes[i];
        }
    }
    return NULL;
}

uint8 meshNetLookupNode(char *nodeId)
{
    uint8 i;
    for (i = 0; i < MESH_MAX_NODES; i++) {
        if (sNodes[i].used && strcmp(sNodes[i].nodeId, nodeId) == 0) {
            return 1;
        }
    }
    return 0;
}

static MeshNodeEntry *findNodeById(char *nodeId)
{
    uint8 i;
    for (i = 0; i < MESH_MAX_NODES; i++) {
        if (sNodes[i].used && strcmp(sNodes[i].nodeId, nodeId) == 0) {
            return &sNodes[i];
        }
    }
    return NULL;
}

/*登记一个新入网节点，或者刷新已存在节点的信息；表满时返回NULL（发送端会重试入网）*/
static MeshNodeEntry *allocNode(uint16 addr, uint8 nodeKind)
{
    uint8 i;
    MeshNodeEntry *existing = findNodeByAddr(addr);
    if (existing != NULL) {
        existing->nodeKind = nodeKind;
        return existing;
    }
    for (i = 0; i < MESH_MAX_NODES; i++) {
        if (!sNodes[i].used) {
            sNodes[i].used = 1;
            sNodes[i].addr = addr;
            sNodes[i].nodeKind = nodeKind;
            /*n1保留给路由器自身传感器，远程节点从n2开始编号*/
            sprintf(sNodes[i].nodeId, "n%u", (unsigned int)sNextNodeNumber);
            sNextNodeNumber++;
            return &sNodes[i];
        }
    }
    return NULL;
}

static void handleJoinRequest(uint8 *buf, uint8 len)
{
    uint16 senderAddr;
    uint8 nodeKind;
    uint8 payload[3];
    MeshNodeEntry *node;

    if (len < 12) {
        return;
    }
    senderAddr = (uint16)(((uint16)buf[1] << 8) | buf[2]);
    nodeKind = buf[11];

    node = allocNode(senderAddr, nodeKind);
    if (node == NULL) {
        return;
    }

    payload[0] = MSG_JOIN_ACCEPT;
    payload[1] = HI_UINT16(sMyAddr);
    payload[2] = LO_UINT16(sMyAddr);
    sendMsg(senderAddr, payload, 3);
}

static void handleData(uint8 *buf, uint8 len)
{
    uint16 senderAddr, seq, value1, value2;
    uint8 nodeKind;
    MeshNodeEntry *node;
    uint8 ackPayload[3];

    if (len < 10) {
        return;
    }
    senderAddr = (uint16)(((uint16)buf[1] << 8) | buf[2]);
    seq        = (uint16)(((uint16)buf[3] << 8) | buf[4]);
    nodeKind   = buf[5];
    value1     = (uint16)(((uint16)buf[6] << 8) | buf[7]);
    value2     = (uint16)(((uint16)buf[8] << 8) | buf[9]);

    node = findNodeByAddr(senderAddr);
    if (node == NULL) {
        /*未登记过（例如路由器重启丢失了表）：直接重新收编，避免节点一直重试入网*/
        node = allocNode(senderAddr, nodeKind);
        if (node == NULL) {
            return;
        }
    }

    ackPayload[0] = MSG_DATA_ACK;
    ackPayload[1] = HI_UINT16(seq);
    ackPayload[2] = LO_UINT16(seq);
    sendMsg(senderAddr, ackPayload, 3);

    mesh_on_data(node->nodeId, node->nodeKind, seq, value1, value2);
}

static void handleCmdAck(uint8 *buf, uint8 len)
{
    if (len < (uint8)(3 + MESH_CMDID_LEN + MESH_STR_LEN + MESH_STR_LEN)) {
        return;
    }
    if (strncmp((char *)&buf[3], sPendingCmdId, MESH_CMDID_LEN) != 0) {
        return;
    }
    meshCopyStr(sPendingCmdResult, (char *)&buf[3 + MESH_CMDID_LEN], MESH_STR_LEN);
    meshCopyStr(sPendingCmdState, (char *)&buf[3 + MESH_CMDID_LEN + MESH_STR_LEN], MESH_STR_LEN);
    sPendingCmdAckReceived = 1;
}

uint8 meshNetSendCommand(char *targetNodeId, char *commandId, char *device, char *action,
                          char *resultOut, char *stateOut)
{
    MeshNodeEntry *node = findNodeById(targetNodeId);
    uint8 payload[1 + MESH_CMDID_LEN + MESH_STR_LEN + MESH_STR_LEN];
    uint8 attempt;

    if (node == NULL) {
        return 0;
    }

    payload[0] = MSG_CMD;
    meshCopyStr((char *)&payload[1], commandId, MESH_CMDID_LEN);
    meshCopyStr((char *)&payload[1 + MESH_CMDID_LEN], device, MESH_STR_LEN);
    meshCopyStr((char *)&payload[1 + MESH_CMDID_LEN + MESH_STR_LEN], action, MESH_STR_LEN);

    meshCopyStr(sPendingCmdId, commandId, MESH_CMDID_LEN);
    sPendingCmdAckReceived = 0;

    for (attempt = 0; attempt < 3 && !sPendingCmdAckReceived; attempt++) {
        uint16 waitIter;
        sendMsg(node->addr, payload, sizeof(payload));
        for (waitIter = 0; waitIter < 40 && !sPendingCmdAckReceived; waitIter++) {
            basicRfPoll();
            if (basicRfPacketIsReady()) {
                uint8 buf[MESH_RECV_BUF];
                uint8 rlen = basicRfReceive(buf, sizeof(buf), NULL);
                if (rlen > 0) {
                    handleMessage(buf, rlen);
                }
            }
            halMcuWaitMs(25);
        }
    }

    if (!sPendingCmdAckReceived) {
        return 0;
    }
    meshCopyStr(resultOut, sPendingCmdResult, MESH_STR_LEN);
    meshCopyStr(stateOut, sPendingCmdState, MESH_STR_LEN);
    return 1;
}

#endif /* NODE_ROLE_ROUTER */

#if defined(NODE_ROLE_SENSOR)

static unsigned char sMyIeee[8];
static uint8  sJoined;
static uint16 sRouterAddr;
static uint16 sJoinRetryMs;
static uint8  sJoinAttempts;
static uint16 sDataSeq;
static uint8  sDataFailStreak;
static uint8  sPendingDataAckReceived;

static void sendJoinRequest(void)
{
    uint8 payload[12];
    payload[0] = MSG_JOIN_REQUEST;
    payload[1] = HI_UINT16(sMyAddr);
    payload[2] = LO_UINT16(sMyAddr);
    memcpy(&payload[3], sMyIeee, 8);
    payload[11] = sMyKind;
    sendMsg(MESH_BROADCAST, payload, 12);
}

static void handleJoinAccept(uint8 *buf, uint8 len)
{
    if (len < 3) {
        return;
    }
    sRouterAddr = (uint16)(((uint16)buf[1] << 8) | buf[2]);
    sJoined = 1;
    sJoinAttempts = 0;
    sDataFailStreak = 0;
}

static void handleDataAck(uint8 *buf, uint8 len)
{
    uint16 ackedSeq;
    if (len < 3) {
        return;
    }
    ackedSeq = (uint16)(((uint16)buf[1] << 8) | buf[2]);
    if (ackedSeq == sDataSeq) {
        sPendingDataAckReceived = 1;
    }
}

static void handleCmd(uint8 *buf, uint8 len)
{
    char commandId[MESH_CMDID_LEN];
    char device[MESH_STR_LEN];
    char action[MESH_STR_LEN];
    char result[MESH_STR_LEN];
    char state[MESH_STR_LEN];
    uint8 ackPayload[3 + MESH_CMDID_LEN + MESH_STR_LEN + MESH_STR_LEN];

    if (len < (uint8)(1 + MESH_CMDID_LEN + MESH_STR_LEN + MESH_STR_LEN)) {
        return;
    }

    meshCopyStr(commandId, (char *)&buf[1], MESH_CMDID_LEN);
    meshCopyStr(device, (char *)&buf[1 + MESH_CMDID_LEN], MESH_STR_LEN);
    meshCopyStr(action, (char *)&buf[1 + MESH_CMDID_LEN + MESH_STR_LEN], MESH_STR_LEN);

    mesh_on_command(commandId, device, action, result, state);

    ackPayload[0] = MSG_CMD_ACK;
    ackPayload[1] = HI_UINT16(sMyAddr);
    ackPayload[2] = LO_UINT16(sMyAddr);
    meshCopyStr((char *)&ackPayload[3], commandId, MESH_CMDID_LEN);
    meshCopyStr((char *)&ackPayload[3 + MESH_CMDID_LEN], result, MESH_STR_LEN);
    meshCopyStr((char *)&ackPayload[3 + MESH_CMDID_LEN + MESH_STR_LEN], state, MESH_STR_LEN);
    sendMsg(sRouterAddr, ackPayload, sizeof(ackPayload));
}

uint8 meshNetIsJoined(void)
{
    return sJoined;
}

void meshNetSendData(uint16 value1, uint16 value2)
{
    uint8 payload[10];
    uint8 attempt;

    if (!sJoined) {
        return;
    }

    sDataSeq++;
    payload[0] = MSG_DATA;
    payload[1] = HI_UINT16(sMyAddr);
    payload[2] = LO_UINT16(sMyAddr);
    payload[3] = HI_UINT16(sDataSeq);
    payload[4] = LO_UINT16(sDataSeq);
    payload[5] = sMyKind;
    payload[6] = HI_UINT16(value1);
    payload[7] = LO_UINT16(value1);
    payload[8] = HI_UINT16(value2);
    payload[9] = LO_UINT16(value2);

    sPendingDataAckReceived = 0;
    for (attempt = 0; attempt < 3 && !sPendingDataAckReceived; attempt++) {
        uint16 waitIter;
        sendMsg(sRouterAddr, payload, sizeof(payload));
        for (waitIter = 0; waitIter < 40 && !sPendingDataAckReceived; waitIter++) {
            basicRfPoll();
            if (basicRfPacketIsReady()) {
                uint8 buf[MESH_RECV_BUF];
                uint8 rlen = basicRfReceive(buf, sizeof(buf), NULL);
                if (rlen > 0) {
                    handleMessage(buf, rlen);
                }
            }
            halMcuWaitMs(25);
        }
    }

    if (sPendingDataAckReceived) {
        sDataFailStreak = 0;
    } else {
        sDataFailStreak++;
        if (sDataFailStreak >= 3) {
            /*连续多次都收不到确认，判定路由器已失联，重新走一遍入网流程*/
            sJoined = 0;
            sJoinAttempts = 0;
            sJoinRetryMs = 0;
        }
    }
}

#endif /* NODE_ROLE_SENSOR */

static void handleMessage(uint8 *buf, uint8 len)
{
    if (len < 1) {
        return;
    }
    switch (buf[0]) {
#if defined(NODE_ROLE_ROUTER)
    case MSG_JOIN_REQUEST:
        handleJoinRequest(buf, len);
        break;
    case MSG_DATA:
        handleData(buf, len);
        break;
    case MSG_CMD_ACK:
        handleCmdAck(buf, len);
        break;
#endif
#if defined(NODE_ROLE_SENSOR)
    case MSG_JOIN_ACCEPT:
        handleJoinAccept(buf, len);
        break;
    case MSG_DATA_ACK:
        handleDataAck(buf, len);
        break;
    case MSG_CMD:
        handleCmd(buf, len);
        break;
#endif
    default:
        break;
    }
}

void meshNetInit(uint8 nodeKind)
{
    basicRfCfg_t cfg;
    unsigned char ieee[8];

    halMcuInit();
    platform_read_primary_ieee_address(ieee);
    sMyAddr = deriveShortAddr(ieee);
    sMyKind = nodeKind;

    cfg.panId = MESH_PAN_ID;
    cfg.channel = MESH_CHANNEL;
    /*不依赖硬件MAC层自动ACK：关闭帧过滤时不可靠，开启帧过滤又会导致收不到
      任何帧（已实测确认）。可靠性改由上面的应用层重试机制自己保证*/
    cfg.ackRequest = FALSE;
    cfg.myAddr = sMyAddr;
    basicRfInit(&cfg);
    /*刻意不打开全局中断EA：SDCC为RF_VECTOR生成的中断向量表条目未对齐，真正
      触发该中断会让CPU跳入程序启动代码而不是rfIsr（表现为收到包后整机复位）。
      改为在basicRfPoll()中轮询RFIRQF0代替中断*/
    FRMFILT0 = 0x00;
    basicRfReceiveOn();

#if defined(NODE_ROLE_ROUTER)
    {
        uint8 i;
        for (i = 0; i < MESH_MAX_NODES; i++) {
            sNodes[i].used = 0;
        }
        sNextNodeNumber = 2;
        sPendingCmdAckReceived = 0;
    }
#endif
#if defined(NODE_ROLE_SENSOR)
    memcpy(sMyIeee, ieee, 8);
    sJoined = 0;
    sRouterAddr = 0;
    sJoinRetryMs = 0;
    sJoinAttempts = 0;
    sDataSeq = 0;
    sDataFailStreak = 0;
#endif
}

void meshNetPoll(uint16 elapsedMs)
{
    basicRfPoll();
    if (basicRfPacketIsReady()) {
        uint8 buf[MESH_RECV_BUF];
        uint8 len = basicRfReceive(buf, sizeof(buf), NULL);
        if (len > 0) {
            handleMessage(buf, len);
        }
    }

#if defined(NODE_ROLE_SENSOR)
    if (!sJoined) {
        if (sJoinRetryMs > elapsedMs) {
            sJoinRetryMs -= elapsedMs;
        } else {
            sendJoinRequest();
            sJoinAttempts++;
            /*用本机地址+尝试次数做退避抖动：没有可用的随机数源
              （halRfGetRandomByte()未实现，会直接触发HAL_ASSERT），
              但足以让不同节点、不同轮次的重试时间错开，避免多节点同时上电时
              持续撞包*/
            sJoinRetryMs = 2000 + (uint16)(((sMyAddr + sJoinAttempts) & 0x0F) * 100);
        }
    }
#endif
}
