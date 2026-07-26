#ifndef BREEZIO_UART_H
#define BREEZIO_UART_H

#include "platform.h"

/*发送单字节*/
void Uart_Send_char(char ch);
/*发送字符串*/
void Uart_Send_String(char *Data);
/*阻塞接收单字节*/
int Uart_Recv_char(void);
/*非阻塞接收单字节*/
unsigned char Uart_Recv_char_nonblock(unsigned char *ch);

#endif
