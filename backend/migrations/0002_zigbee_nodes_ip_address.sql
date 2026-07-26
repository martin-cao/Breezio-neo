-- Wi-Fi transport: retain an optional node LAN address for inventory and the
-- legacy gateway-dials-sensor mode. The preferred ESP32 path now has sensors
-- actively connect to the gateway listener.
ALTER TABLE zigbee_nodes ADD COLUMN IF NOT EXISTS ip_address VARCHAR(64);
