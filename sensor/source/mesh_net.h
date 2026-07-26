#ifndef BREEZIO_MESH_NET_H
#define BREEZIO_MESH_NET_H

#include "platform.h"

#define MESH_KIND_TEMPHUMI 0
#define MESH_KIND_ILLU     1

/*初始化RF网络层：派生本机短地址、配置PAN/信道；SENSOR角色会开始尝试入网*/
void meshNetInit(uint8 nodeKind);

/*主循环中每次迭代都应调用：轮询收发、处理入网/数据/指令消息、维护重试计时。
  elapsedMs为距上次调用经过的毫秒数，用于内部超时/退避计时（沿用项目里
  update_rx_blink()已有的“主循环传入经过时间”写法，不依赖任何硬件定时器）*/
void meshNetPoll(uint16 elapsedMs);

#if defined(NODE_ROLE_ROUTER)

/*由main.c实现：路由器收到远程节点的一条数据上报时被调用，用于转发到UART*/
void mesh_on_data(char *nodeId, uint8 nodeKind, uint16 seq, uint16 value1, uint16 value2);

/*路由器侧：把@CMD转发给node_id对应的远程节点，内部带重试地等待确认。
  返回1表示收到确认（resultOut/stateOut已填充），0表示超时未确认（未知节点也返回0）*/
uint8 meshNetSendCommand(char *targetNodeId, char *commandId, char *device, char *action,
                          char *resultOut, char *stateOut);

/*node_id是否是一个已入网的远程节点（不含路由器自身）*/
uint8 meshNetLookupNode(char *nodeId);

#endif

#if defined(NODE_ROLE_SENSOR)

/*是否已成功入网（收到过JOIN_ACCEPT且尚未判定路由器失联）*/
uint8 meshNetIsJoined(void);

/*周期性上报本地采集到的数据；内部会重试直至收到应用层确认或达到重试上限，
  连续多次上报都拿不到确认会重新触发入网*/
void meshNetSendData(uint16 value1, uint16 value2);

/*由main.c实现：传感节点收到路由器转发来的指令时被调用，用于驱动本地指示灯，
  并把结果写入resultOut/stateOut供mesh_net回发MSG_CMD_ACK*/
void mesh_on_command(char *commandId, char *device, char *action, char *resultOut, char *stateOut);

#endif

#endif
