#ifndef BREEZIO_PLATFORM_H
#define BREEZIO_PLATFORM_H

typedef unsigned char uint8;
typedef signed char int8;
typedef unsigned short uint16;
typedef signed short int16;
typedef unsigned long uint32;
typedef signed long int32;

#define PLATFORM_STRINGIFY_INNER(value) #value
#define PLATFORM_STRINGIFY(value) PLATFORM_STRINGIFY_INNER(value)

#if defined(__SDCC)
#include <cc2530.h>

#define PLATFORM_CODE __code
#define PLATFORM_XDATA __xdata
#define PLATFORM_NOP() __asm nop __endasm
#define PLATFORM_ISR(function_name, vector_number) \
    void function_name(void) __interrupt(vector_number)
#define PLATFORM_MAIN int main(void)

#elif defined(__IAR_SYSTEMS_ICC__) && defined(__ICC8051__)
#include <ioCC2530.h>

#define PLATFORM_CODE __code
#define PLATFORM_XDATA __xdata
#define PLATFORM_NOP() asm("NOP")
#define PLATFORM_ISR(function_name, vector_number) \
    _Pragma(PLATFORM_STRINGIFY(vector=vector_number)) \
    __near_func __interrupt void function_name(void)
#define PLATFORM_MAIN void main(void)

#else
#error "Unsupported compiler: use SDCC mcs51 or IAR 8051"
#endif

#define CC2530_INFO_PAGE_BASE 0x7800u
#define CC2530_PRIMARY_IEEE_OFFSET 0x000Cu
#define CC2530_PRIMARY_IEEE_LENGTH 8u

void platform_read_primary_ieee_address(
    unsigned char address[CC2530_PRIMARY_IEEE_LENGTH]);

#endif
