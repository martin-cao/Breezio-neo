#include "platform.h"
#include "light_sensor.h"

static unsigned int sLight;

/*光敏电阻ADC初始化：P0.1配置为模拟输入*/
void light_sensor_io_init(void)
{
    P0SEL |= 0x02;
}

/*读取光敏电阻ADC值，AVDD5基准、12位分辨率*/
void light_sensor_update(void)
{
    unsigned int value;

    ADCCON3 = 0xB1;
    ADCCON1 |= 0x30;
    ADCCON1 |= 0x40;
    while (!(ADCCON1 & 0x80));

    value = ADCL >> 2;
    value |= (ADCH << 6);
    sLight = value >> 2;
}

unsigned int light_sensor_value(void)
{
    return sLight;
}
