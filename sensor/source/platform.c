#include "platform.h"

void platform_read_primary_ieee_address(
    unsigned char address[CC2530_PRIMARY_IEEE_LENGTH])
{
    unsigned char i;
    const PLATFORM_XDATA unsigned char *source =
        (const PLATFORM_XDATA unsigned char *)
        (CC2530_INFO_PAGE_BASE + CC2530_PRIMARY_IEEE_OFFSET);

    for (i = 0; i < CC2530_PRIMARY_IEEE_LENGTH; ++i) {
        address[i] = source[i];
    }
}
