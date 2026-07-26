SDCC ?= sdcc
PYTHON ?= python3

TARGET := breezio-sensor
BUILD_DIR := build
SOURCE_DIR := source

NODE_KIND ?= TEMPHUMI
NODE_ID ?= n1
NODE_ROLE ?=
BRINGUP ?=

ifeq ($(NODE_KIND),ILLU)
SENSOR_SOURCE := $(SOURCE_DIR)/light_sensor.c
else
SENSOR_SOURCE := $(SOURCE_DIR)/dht11.c
endif

RF_SOURCES :=
ifneq ($(NODE_ROLE)$(RFTEST),)
RF_SOURCES := \
	$(SOURCE_DIR)/basic_rf.c \
	$(SOURCE_DIR)/hal_rf.c \
	$(SOURCE_DIR)/clock.c \
	$(SOURCE_DIR)/hal_mcu.c \
	$(SOURCE_DIR)/hal_int.c \
	$(SOURCE_DIR)/hal_assert.c
endif

MESH_SOURCE :=
ifneq ($(NODE_ROLE),)
MESH_SOURCE := $(SOURCE_DIR)/mesh_net.c
endif

SOURCES := \
	$(SOURCE_DIR)/main.c \
	$(SENSOR_SOURCE) \
	$(SOURCE_DIR)/info.c \
	$(SOURCE_DIR)/sys_init.c \
	$(SOURCE_DIR)/uart.c \
	$(SOURCE_DIR)/platform.c \
	$(SOURCE_DIR)/hex_protocol.c \
	$(RF_SOURCES) \
	$(MESH_SOURCE)

OBJECTS := $(patsubst $(SOURCE_DIR)/%.c,$(BUILD_DIR)/%.rel,$(SOURCES))

COMMON_FLAGS := \
	-mmcs51 \
	--model-large \
	--std-sdcc11 \
	--code-size 0x10000 \
	--xram-size 0x2000 \
	-DSPI_LCD \
	-DNODE_ID=\"$(NODE_ID)\" \
	-I$(SOURCE_DIR)

ifeq ($(NODE_KIND),ILLU)
COMMON_FLAGS += -DNODE_KIND_ILLU
endif

ifneq ($(NODE_ROLE),)
COMMON_FLAGS += -DNODE_ROLE_$(NODE_ROLE)
endif

ifneq ($(RFTEST),)
COMMON_FLAGS += -DRFTEST_$(RFTEST)
endif

ifeq ($(BOOT_TRACE),1)
COMMON_FLAGS += -DBOOT_TRACE
endif

ifneq ($(BRINGUP),)
COMMON_FLAGS += -DBRINGUP_STAGE=$(BRINGUP)
endif

# LEGACY_TEXT_PROTOCOL=1 falls back to the original @BATCH/@NODE/@END/@ACK text
# emitter; by default the firmware emits the ~BZ1, hex-framed protocol instead
# (see docs/sensor-network-protocol.md), which survives a noisy/contended UART
# line far better than free-form text.
ifeq ($(LEGACY_TEXT_PROTOCOL),1)
COMMON_FLAGS += -DLEGACY_TEXT_PROTOCOL
endif

CFLAGS := $(COMMON_FLAGS) --opt-code-size
NO_OPT_CFLAGS := $(COMMON_FLAGS)
LDFLAGS := $(COMMON_FLAGS) --out-fmt-ihx

.PHONY: all clean check monitor package

all: $(BUILD_DIR)/$(TARGET).hex

$(BUILD_DIR):
	mkdir -p $@

$(BUILD_DIR)/dht11.rel: $(SOURCE_DIR)/dht11.c | $(BUILD_DIR)
	$(SDCC) $(NO_OPT_CFLAGS) -c $< -o $@

$(BUILD_DIR)/hal_mcu.rel: $(SOURCE_DIR)/hal_mcu.c | $(BUILD_DIR)
	$(SDCC) $(NO_OPT_CFLAGS) -c $< -o $@

$(BUILD_DIR)/%.rel: $(SOURCE_DIR)/%.c | $(BUILD_DIR)
	$(SDCC) $(CFLAGS) -c $< -o $@

$(BUILD_DIR)/$(TARGET).ihx: $(OBJECTS)
	$(SDCC) $(LDFLAGS) $(OBJECTS) -o $@

$(BUILD_DIR)/$(TARGET).hex: $(BUILD_DIR)/$(TARGET).ihx
	cp $< $@
	$(PYTHON) tools/check_port.py --hex $@

check:
	sh tests/test_port.sh

package: all
	$(PYTHON) tools/create_flash_package.py \
		--hex $(BUILD_DIR)/$(TARGET).hex \
		--memory $(BUILD_DIR)/$(TARGET).mem \
		--output dist/$(TARGET)-sdcc.zip

monitor:
	@test -n "$(PORT)" || { echo "usage: make monitor PORT=/dev/cu.PL2303G-USBtoUART1410"; exit 2; }
	$(PYTHON) tools/serial_monitor.py "$(PORT)" --baud "$(or $(BAUD),38400)"

clean:
	rm -rf $(BUILD_DIR) dist
