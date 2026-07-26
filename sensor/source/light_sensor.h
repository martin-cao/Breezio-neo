#ifndef BREEZIO_LIGHT_SENSOR_H
#define BREEZIO_LIGHT_SENSOR_H

#include "platform.h"

void light_sensor_io_init(void);
void light_sensor_update(void);
unsigned int light_sensor_value(void);

#endif
