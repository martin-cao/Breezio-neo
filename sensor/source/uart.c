#include "platform.h"
#include "uart.h"

/*串口发送字节函数
-------------------------------------------------------*/
void Uart_Send_char(char ch)
{
  U0DBUF = ch;
  while(UTX0IF == 0);
  UTX0IF = 0;
}

/*串口发送字符串函数
-------------------------------------------------------*/
void Uart_Send_String(char *Data)
{  
  while (*Data != '\0')
  {
    Uart_Send_char(*Data++);
  }
}

/*串口接收字节函数
-------------------------------------------------------*/
int Uart_Recv_char(void)
{
  int ch;
    
  while (URX0IF == 0);
  ch = U0DBUF;
  URX0IF = 0;
  return ch;
}

/*串口非阻塞接收字节函数
-------------------------------------------------------*/
unsigned char Uart_Recv_char_nonblock(unsigned char *ch)
{
  if (URX0IF == 0) {
    return 0;
  }
  *ch = U0DBUF;
  URX0IF = 0;
  return 1;
}
