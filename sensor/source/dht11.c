#include "dht11.h"
#include "sys_init.h"

unsigned char sTemp;
unsigned char sHumidity;

#define DHT11_WAIT_LIMIT 1000u

//void Delay10us(void)
//{
//  unsigned short i;
//  for(i=0;i<106;i++)
//  {
//    asm("NOP");
//  }
//}

static unsigned char dht11_wait_for(unsigned char level)
{
  uint16 guard = DHT11_WAIT_LIMIT;

  while (COM_R != level) {
    if (guard-- == 0) {
      return 0;
    }
  }
  return 1;
}

/*读取DHT11单个bit*/
static signed char dht11_read_bit(void)
{
  uint16 i = 0;

  if (!dht11_wait_for(1)) {
    return -1;
  }
  for (i=0; i<200; i++) {
    if (COM_R == 0) break;
  }
  if (i >= 200) {
    return -1;
  }
  /* SDCC (unlike the original IAR build) measures roughly 40-43 loop
     iterations for a real "0" bit and 116-121 for a "1" bit on this
     hardware (empirically measured via a temporary bit-count dump), so
     the 0/1 threshold must sit between those two clusters rather than
     at the IAR-tuned value of 30. */
  if (i<80) return 0;
  return 1;
}

/*读取DHT11单个字节*/
static unsigned char dht11_read_byte(unsigned char *value)
{
  unsigned char v = 0;
  signed char b;
  int i;
  for (i=7; i>=0; i--) {
    b = dht11_read_bit();
    if (b < 0) {
      return 0;
    }
    v |= b<<i;
  }
  *value = v;
  return 1;
}



/*初始化DHT11引脚*/
void dht11_io_init(void)
{
  P0SEL  &= ~0x20;          //P1为普通 I/O 口
  COM_OUT;
  COM_SET;  
}
/*读取缓存温度值*/
unsigned char dht11_temp(void)
{
  return sTemp;
}
/*读取缓存湿度值*/
unsigned char dht11_humidity(void)
{
  return sHumidity;
}

/*采集一次DHT11并更新缓存，同时串口输出*/
void dht11_update(void)
{
  unsigned char dat1, dat2, dat3, dat4, dat5, ck;

  //主机拉低18ms
  COM_CLR;
  halWait(18);
  COM_SET;

  if (!dht11_wait_for(0)) return;

  //总线由上拉电阻拉高 主机延时20us
  //主机设为输入 判断从机响应信号
  //判断从机是否有低电平响应信号 如不响应则跳出，响应则向下运行
  if (!dht11_wait_for(1)) return;
  if (!dht11_wait_for(0)) return;


  if (!dht11_read_byte(&dat1)) return;

  if (!dht11_read_byte(&dat2)) return;

  if (!dht11_read_byte(&dat3)) return;

  if (!dht11_read_byte(&dat4)) return;

  if (!dht11_read_byte(&dat5)) return;

  ck = dat1 + dat2 + dat3 + dat4;

  if (ck == dat5) {
    sTemp = dat3;
    sHumidity = dat1;
  }

  // 仅更新传感数据，串口输出由上层决定
}
