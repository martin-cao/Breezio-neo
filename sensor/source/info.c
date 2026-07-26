#include <stdio.h>

#include "info.h"
#include "platform.h"
#include "sys_init.h"
#include "uart.h"

static void send_twice(char *text)
{
    unsigned char attempt;
    for (attempt = 0; attempt < 2; attempt++) {
        Uart_Send_String(text);
        halWait(500);
    }
}

void lcd_dis(void)
{
    unsigned char ieee[CC2530_PRIMARY_IEEE_LENGTH];
    char identity[32];

    send_twice("{TYPE=01002}");
    halWait(500);

    platform_read_primary_ieee_address(ieee);
    sprintf(
        identity,
        "{MAC=%02X:%02X:%02X:%02X:%02X:%02X:%02X:%02X}",
        ieee[7],
        ieee[6],
        ieee[5],
        ieee[4],
        ieee[3],
        ieee[2],
        ieee[1],
        ieee[0]
    );
    send_twice(identity);
}
