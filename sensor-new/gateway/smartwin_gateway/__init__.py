"""Host-side gateway for the Breezio sensor-new ESP32-S3 window node.

Reads the `$BRZ2` USB-serial protocol and turns it into structured JSON, and sends
window/buzzer commands back to the node.
"""

from .protocol import (
    Ack,
    Hello,
    Message,
    Telemetry,
    encode_command,
    parse_line,
)

__all__ = [
    "Ack",
    "Hello",
    "Message",
    "Telemetry",
    "encode_command",
    "parse_line",
]
