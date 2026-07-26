#ifndef BREEZIO_DHT11_H
#define BREEZIO_DHT11_H

#include "platform.h"
#include <stdio.h>

#define       PIN_OUT       (P0DIR |= 0x20)
#define       PIN_IN        (P0DIR &= ~0x20)
#define       PIN_CLR       (P0_5 = 0)
#define       PIN_SET       (P0_5 = 1)
#define       PIN_R         (P0_5)

#define       COM_IN          PIN_IN  
#define       COM_OUT         PIN_OUT
#define       COM_CLR         PIN_CLR
#define       COM_SET         PIN_SET
#define       COM_R           PIN_R


void dht11_io_init(void);
unsigned char dht11_temp(void);
unsigned char dht11_humidity(void);
void dht11_update(void);

#endif
