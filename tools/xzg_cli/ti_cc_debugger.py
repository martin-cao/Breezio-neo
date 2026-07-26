from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import ctypes
import ctypes.util
import time

from .intelhex import IntelHexImage


VID_CC_DEBUGGER = 0x0451
PID_CC_DEBUGGER = 0x16A2
VID_SMARTRF04EB = 0x11A0
PID_SMARTRF04EB = 0xEB20

LIBUSB_ENDPOINT_IN = 0x80
LIBUSB_TRANSFER_TYPE_BULK = 2
LIBUSB_REQUEST_TYPE_VENDOR = 0x40
LIBUSB_RECIPIENT_DEVICE = 0x00

REQ_GET_STATE = 0xC0
REQ_SET_SPEED = 0xCF
REQ_PREPARE_DEBUG_MODE = 0xC5
REQ_SET_CHIP_INFO = 0xC8
REQ_RESET = 0xC9

CMD_EXEC_3BYTE = 0xBE
CMD_EXEC_2BYTE = 0x8E
CMD_EXEC_2BYTE_READ = 0x8F
CMD_EXEC_1BYTE = 0x5E
CMD_EXEC_1BYTE_READ = 0x4E

ASM_MOV_DPTR_IMM16 = 0x90
ASM_MOV_A_IMM8 = 0x74
ASM_MOVX_A_AT_DPTR = 0xE0
ASM_MOVX_AT_DPTR_A = 0xF0
ASM_INC_DPTR = 0xA3
ASM_MOV_DIRECT_IMM = 0x75
ASM_MOV_A_DIRECT = 0xE5

XREG_FWDATA = 0x6273
XREG_FCTL = 0x6270
XREG_FADDRL = 0x6271
XREG_FADDRH = 0x6272
XREG_DMA0CFGH = 0x70D5
XREG_DMA0CFGL = 0x70D4
XREG_DMAARM = 0x70D6
XREG_DMAREQ = 0x70D7
XREG_DMAIRQ = 0x70D1
XREG_RNDL = 0x70BC
XREG_MEMCTR = 0x70C7
XREG_FMAP = 0x709F

ADDR_DMA_DESC = 0x0F00
ADDR_BUFFER = 0x0000
ADDR_INFO_PAGE = 0x7800
ADDR_IEEE_PRIMARY = 0x780C

DEBUG_CMD_CHIP_ERASE = 0x14
DEBUG_CMD_WR_CONFIG = 0x1D
DEBUG_CMD_READ_STATUS = 0x34
DEBUG_CMD_DEBUG_INSTR_1 = 0x55
DEBUG_CMD_DEBUG_INSTR_2 = 0x56
DEBUG_CMD_DEBUG_INSTR_3 = 0x57

WRAPPER_DEBUG_EXEC = 0x1C
WRAPPER_DEBUG_EXEC_READ = 0x1F
WRAPPER_DEBUG_EXEC_ARG = 0x4C

FLASH_BANK_SIZE = 0x8000
XBANK_OFFSET = 0x8000
PROG_BLOCK_SIZE = 1024

CMD_HEADER = bytes(
    [
        0x40,
        0x55,
        0x00,
        0x72,
        0x56,
        0xE5,
        0x92,
        0xBE,
        0x57,
        0x75,
        0x92,
        0x00,
        0x74,
        0x56,
        0xE5,
        0x83,
        0x76,
        0x56,
        0xE5,
        0x82,
    ]
)

CMD_FOOTER = bytes([0xD4, 0x57, 0x90, 0xC2, 0x57, 0x75, 0x92, 0x90, 0x56, 0x74])


class UsbError(RuntimeError):
    pass


class libusb_device_descriptor(ctypes.Structure):
    _fields_ = [
        ("bLength", ctypes.c_uint8),
        ("bDescriptorType", ctypes.c_uint8),
        ("bcdUSB", ctypes.c_uint16),
        ("bDeviceClass", ctypes.c_uint8),
        ("bDeviceSubClass", ctypes.c_uint8),
        ("bDeviceProtocol", ctypes.c_uint8),
        ("bMaxPacketSize0", ctypes.c_uint8),
        ("idVendor", ctypes.c_uint16),
        ("idProduct", ctypes.c_uint16),
        ("bcdDevice", ctypes.c_uint16),
        ("iManufacturer", ctypes.c_uint8),
        ("iProduct", ctypes.c_uint8),
        ("iSerialNumber", ctypes.c_uint8),
        ("bNumConfigurations", ctypes.c_uint8),
    ]


class libusb_endpoint_descriptor(ctypes.Structure):
    _fields_ = [
        ("bLength", ctypes.c_uint8),
        ("bDescriptorType", ctypes.c_uint8),
        ("bEndpointAddress", ctypes.c_uint8),
        ("bmAttributes", ctypes.c_uint8),
        ("wMaxPacketSize", ctypes.c_uint16),
        ("bInterval", ctypes.c_uint8),
        ("bRefresh", ctypes.c_uint8),
        ("bSynchAddress", ctypes.c_uint8),
        ("extra", ctypes.POINTER(ctypes.c_ubyte)),
        ("extra_length", ctypes.c_int),
    ]


class libusb_interface_descriptor(ctypes.Structure):
    _fields_ = [
        ("bLength", ctypes.c_uint8),
        ("bDescriptorType", ctypes.c_uint8),
        ("bInterfaceNumber", ctypes.c_uint8),
        ("bAlternateSetting", ctypes.c_uint8),
        ("bNumEndpoints", ctypes.c_uint8),
        ("bInterfaceClass", ctypes.c_uint8),
        ("bInterfaceSubClass", ctypes.c_uint8),
        ("bInterfaceProtocol", ctypes.c_uint8),
        ("iInterface", ctypes.c_uint8),
        ("endpoint", ctypes.POINTER(libusb_endpoint_descriptor)),
        ("extra", ctypes.POINTER(ctypes.c_ubyte)),
        ("extra_length", ctypes.c_int),
    ]


class libusb_interface(ctypes.Structure):
    _fields_ = [
        ("altsetting", ctypes.POINTER(libusb_interface_descriptor)),
        ("num_altsetting", ctypes.c_int),
    ]


class libusb_config_descriptor(ctypes.Structure):
    _fields_ = [
        ("bLength", ctypes.c_uint8),
        ("bDescriptorType", ctypes.c_uint8),
        ("wTotalLength", ctypes.c_uint16),
        ("bNumInterfaces", ctypes.c_uint8),
        ("bConfigurationValue", ctypes.c_uint8),
        ("iConfiguration", ctypes.c_uint8),
        ("bmAttributes", ctypes.c_uint8),
        ("MaxPower", ctypes.c_uint8),
        ("interface", ctypes.POINTER(libusb_interface)),
        ("extra", ctypes.POINTER(ctypes.c_ubyte)),
        ("extra_length", ctypes.c_int),
    ]


@dataclass(frozen=True)
class TiProbeInfo:
    programmer: str
    chip_id: int
    fw_version: int
    fw_revision: int
    ieee_address: str | None = None


def image_from_hex(image: IntelHexImage, block_size: int = PROG_BLOCK_SIZE) -> bytes:
    image.validate_range(0xFFFF)
    max_address = image.highest_address + 1
    padded_length = ((max_address + block_size - 1) // block_size) * block_size
    data = bytearray([0xFF] * padded_length)
    for section in image.sections:
        data[section.address : section.address + len(section.data)] = section.data
    return bytes(data)


class LibUsb:
    def __init__(self) -> None:
        path = ctypes.util.find_library("usb-1.0") or "/opt/homebrew/lib/libusb-1.0.dylib"
        self.lib = ctypes.CDLL(path)
        self.ctx = ctypes.c_void_p()
        self._bind()
        self._check(self.lib.libusb_init(ctypes.byref(self.ctx)), "libusb_init")

    def _bind(self) -> None:
        self.lib.libusb_init.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        self.lib.libusb_init.restype = ctypes.c_int
        self.lib.libusb_exit.argtypes = [ctypes.c_void_p]
        self.lib.libusb_open_device_with_vid_pid.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16]
        self.lib.libusb_open_device_with_vid_pid.restype = ctypes.c_void_p
        self.lib.libusb_close.argtypes = [ctypes.c_void_p]
        self.lib.libusb_get_device.argtypes = [ctypes.c_void_p]
        self.lib.libusb_get_device.restype = ctypes.c_void_p
        self.lib.libusb_get_device_descriptor.argtypes = [ctypes.c_void_p, ctypes.POINTER(libusb_device_descriptor)]
        self.lib.libusb_get_device_descriptor.restype = ctypes.c_int
        self.lib.libusb_set_configuration.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib.libusb_set_configuration.restype = ctypes.c_int
        self.lib.libusb_claim_interface.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib.libusb_claim_interface.restype = ctypes.c_int
        self.lib.libusb_release_interface.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib.libusb_release_interface.restype = ctypes.c_int
        self.lib.libusb_get_active_config_descriptor.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.POINTER(libusb_config_descriptor)),
        ]
        self.lib.libusb_get_active_config_descriptor.restype = ctypes.c_int
        self.lib.libusb_free_config_descriptor.argtypes = [ctypes.POINTER(libusb_config_descriptor)]
        self.lib.libusb_control_transfer.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint8,
            ctypes.c_uint8,
            ctypes.c_uint16,
            ctypes.c_uint16,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_uint16,
            ctypes.c_uint,
        ]
        self.lib.libusb_control_transfer.restype = ctypes.c_int
        self.lib.libusb_bulk_transfer.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ubyte,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_uint,
        ]
        self.lib.libusb_bulk_transfer.restype = ctypes.c_int
        self.lib.libusb_error_name.argtypes = [ctypes.c_int]
        self.lib.libusb_error_name.restype = ctypes.c_char_p

    def close(self) -> None:
        if self.ctx:
            self.lib.libusb_exit(self.ctx)
            self.ctx = ctypes.c_void_p()

    def error_name(self, code: int) -> str:
        raw = self.lib.libusb_error_name(code)
        return raw.decode("ascii", errors="replace") if raw else str(code)

    def _check(self, code: int, operation: str) -> int:
        if code < 0:
            raise UsbError(f"{operation} failed: {self.error_name(code)} ({code})")
        return code


class XzgTiCcDebugger:
    def __init__(self, *, timeout_ms: int = 10000, logger=print) -> None:
        self.usb = LibUsb()
        self.timeout_ms = timeout_ms
        self.logger = logger
        self.handle: ctypes.c_void_p | None = None
        self.interface_number = 0
        self.endpoint_in: int | None = None
        self.endpoint_out: int | None = None
        self.programmer = ""
        self.initialized = False

    def close(self) -> None:
        if self.handle:
            try:
                self.usb.lib.libusb_release_interface(self.handle, self.interface_number)
            except Exception:
                pass
            self.usb.lib.libusb_close(self.handle)
            self.handle = None
        self.usb.close()

    def __enter__(self) -> "XzgTiCcDebugger":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def connect(self) -> None:
        for vid, pid, name in (
            (VID_CC_DEBUGGER, PID_CC_DEBUGGER, "CC Debugger"),
            (VID_SMARTRF04EB, PID_SMARTRF04EB, "SmartRF04EB"),
        ):
            handle = self.usb.lib.libusb_open_device_with_vid_pid(self.usb.ctx, vid, pid)
            if handle:
                self.handle = ctypes.c_void_p(handle)
                self.programmer = name
                break
        if not self.handle:
            raise UsbError("no CC Debugger or SmartRF04EB found via libusb")

        cfg_result = self.usb.lib.libusb_set_configuration(self.handle, 1)
        if cfg_result < 0 and cfg_result != -6:  # LIBUSB_ERROR_BUSY
            self.usb._check(cfg_result, "libusb_set_configuration")

        self._discover_endpoints()
        self.usb._check(self.usb.lib.libusb_claim_interface(self.handle, self.interface_number), "libusb_claim_interface")
        self.logger(f"Connected to {self.programmer}; bulk IN=0x{self.endpoint_in:02x}, OUT=0x{self.endpoint_out:02x}")

    def _discover_endpoints(self) -> None:
        if not self.handle:
            raise UsbError("device is not open")
        device = self.usb.lib.libusb_get_device(self.handle)
        cfg_ptr = ctypes.POINTER(libusb_config_descriptor)()
        self.usb._check(self.usb.lib.libusb_get_active_config_descriptor(device, ctypes.byref(cfg_ptr)), "get_config")
        try:
            cfg = cfg_ptr.contents
            if cfg.bNumInterfaces < 1:
                raise UsbError("USB device exposes no interfaces")
            interface = cfg.interface[0]
            alt = interface.altsetting[0]
            self.interface_number = alt.bInterfaceNumber
            for i in range(alt.bNumEndpoints):
                ep = alt.endpoint[i]
                if (ep.bmAttributes & 0x03) != LIBUSB_TRANSFER_TYPE_BULK:
                    continue
                if ep.bEndpointAddress & LIBUSB_ENDPOINT_IN:
                    self.endpoint_in = ep.bEndpointAddress
                else:
                    self.endpoint_out = ep.bEndpointAddress
            if self.endpoint_in is None or self.endpoint_out is None:
                raise UsbError("bulk IN/OUT endpoints not found")
        finally:
            self.usb.lib.libusb_free_config_descriptor(cfg_ptr)

    def control_out(self, request: int, value: int, index: int, data: bytes = b"") -> None:
        if not self.handle:
            raise UsbError("device is not open")
        buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data) if data else None
        ptr = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)) if buffer is not None else None
        result = self.usb.lib.libusb_control_transfer(
            self.handle,
            LIBUSB_REQUEST_TYPE_VENDOR | LIBUSB_RECIPIENT_DEVICE,
            request,
            value,
            index,
            ptr,
            len(data),
            self.timeout_ms,
        )
        self.usb._check(result, f"control OUT 0x{request:02x}")

    def control_in(self, request: int, value: int, index: int, length: int) -> bytes:
        if not self.handle:
            raise UsbError("device is not open")
        buffer = (ctypes.c_ubyte * length)()
        result = self.usb.lib.libusb_control_transfer(
            self.handle,
            LIBUSB_ENDPOINT_IN | LIBUSB_REQUEST_TYPE_VENDOR | LIBUSB_RECIPIENT_DEVICE,
            request,
            value,
            index,
            buffer,
            length,
            self.timeout_ms,
        )
        self.usb._check(result, f"control IN 0x{request:02x}")
        return bytes(buffer[:result])

    def bulk_out(self, data: bytes) -> None:
        if not self.handle or self.endpoint_out is None:
            raise UsbError("bulk OUT endpoint is not open")
        buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        transferred = ctypes.c_int()
        result = self.usb.lib.libusb_bulk_transfer(
            self.handle,
            self.endpoint_out,
            buffer,
            len(data),
            ctypes.byref(transferred),
            self.timeout_ms,
        )
        self.usb._check(result, f"bulk OUT 0x{self.endpoint_out:02x}")
        if transferred.value != len(data):
            raise UsbError(f"bulk OUT short write: {transferred.value}/{len(data)}")

    def bulk_in(self, length: int) -> bytes:
        if not self.handle or self.endpoint_in is None:
            raise UsbError("bulk IN endpoint is not open")
        buffer = (ctypes.c_ubyte * length)()
        transferred = ctypes.c_int()
        result = self.usb.lib.libusb_bulk_transfer(
            self.handle,
            self.endpoint_in,
            buffer,
            length,
            ctypes.byref(transferred),
            self.timeout_ms,
        )
        self.usb._check(result, f"bulk IN 0x{self.endpoint_in:02x}")
        return bytes(buffer[: transferred.value])

    def get_device_info(self) -> TiProbeInfo:
        data = self.control_in(REQ_GET_STATE, 0, 0, 8)
        if len(data) < 6:
            raise UsbError(f"short GET_STATE response: {data.hex()}")
        chip_id = int.from_bytes(data[0:2], "little")
        fw_version = int.from_bytes(data[2:4], "little")
        fw_revision = int.from_bytes(data[4:6], "little")
        return TiProbeInfo(self.programmer, chip_id, fw_version, fw_revision)

    def prepare_debug_mode(self) -> None:
        self.control_out(REQ_PREPARE_DEBUG_MODE, 0, 0)
        info = self.get_device_info()
        command = bytearray(b" " * 0x30)
        chip_name = f"CC{info.chip_id:04X}".encode("ascii")
        command[0 : len(chip_name)] = chip_name
        command[0x10:0x14] = b"DID:"
        did = f"{info.fw_version:04X}".encode("ascii")
        command[0x15 : 0x15 + len(did)] = did
        self.control_out(REQ_SET_CHIP_INFO, 1, 0, bytes(command))

    def reset(self, debug_mode: bool = True) -> None:
        self.control_out(REQ_RESET, 0, 1 if debug_mode else 0)
        time.sleep(0.1)
        self.initialized = False

    def init_debug_interface(self) -> None:
        if self.initialized:
            return
        self.logger("Initializing debug interface")
        self.prepare_debug_mode()
        self.reset(True)
        time.sleep(1.0)
        self.send_debug_instructions(bytes([WRAPPER_DEBUG_EXEC_READ, DEBUG_CMD_READ_STATUS]))
        status = self.bulk_in(1)
        self.logger(f"Debug status: 0x{status[0]:02x}")
        self.send_debug_instructions(bytes([WRAPPER_DEBUG_EXEC_ARG, DEBUG_CMD_WR_CONFIG, 0x22]))
        self.initialized = True

    def send_debug_instructions(self, instructions: bytes) -> None:
        self.bulk_out(instructions)

    def send_raw_data(self, data: bytes) -> None:
        self.bulk_out(data)

    def read_xdata(self, address: int, length: int) -> bytes:
        self.init_debug_interface()
        result = bytearray()
        chunk_size = 512
        for offset in range(0, length, chunk_size):
            current = min(chunk_size, length - offset)
            current_address = address + offset
            command = bytearray(CMD_HEADER)
            command.extend(
                [
                    CMD_EXEC_3BYTE,
                    DEBUG_CMD_DEBUG_INSTR_3,
                    ASM_MOV_DPTR_IMM16,
                    (current_address >> 8) & 0xFF,
                    current_address & 0xFF,
                ]
            )
            for i in range(current):
                cmd = CMD_EXEC_1BYTE_READ
                if i == current - 1 or (i + 1) % 64 == 0:
                    cmd |= 1
                command.extend([cmd, DEBUG_CMD_DEBUG_INSTR_1, ASM_MOVX_A_AT_DPTR])
                command.extend([CMD_EXEC_1BYTE, DEBUG_CMD_DEBUG_INSTR_1, ASM_INC_DPTR])
            command.extend(CMD_FOOTER)
            self.send_debug_instructions(bytes(command))
            data = self.bulk_in(current)
            if len(data) != current:
                raise UsbError(f"short XDATA read: {len(data)}/{current}")
            result.extend(data)
        return bytes(result)

    def write_xdata(self, address: int, data: bytes) -> None:
        self.init_debug_interface()
        chunk_size = 512
        for offset in range(0, len(data), chunk_size):
            chunk = data[offset : offset + chunk_size]
            current_address = address + offset
            command = bytearray(CMD_HEADER)
            command.extend(
                [
                    CMD_EXEC_3BYTE,
                    DEBUG_CMD_DEBUG_INSTR_3,
                    ASM_MOV_DPTR_IMM16,
                    (current_address >> 8) & 0xFF,
                    current_address & 0xFF,
                ]
            )
            for byte in chunk:
                command.extend([CMD_EXEC_2BYTE, DEBUG_CMD_DEBUG_INSTR_2, ASM_MOV_A_IMM8, byte])
                command.extend([CMD_EXEC_1BYTE, DEBUG_CMD_DEBUG_INSTR_1, ASM_MOVX_AT_DPTR_A])
                command.extend([CMD_EXEC_1BYTE, DEBUG_CMD_DEBUG_INSTR_1, ASM_INC_DPTR])
            command.extend(CMD_FOOTER)
            self.send_debug_instructions(bytes(command))

    def read_ieee_address(self) -> str:
        raw = self.read_xdata(ADDR_IEEE_PRIMARY, 8)
        return ":".join(f"{byte:02X}" for byte in reversed(raw))

    def probe(self, *, read_ieee: bool = True) -> TiProbeInfo:
        info = self.get_device_info()
        ieee = None
        if read_ieee:
            ieee = self.read_ieee_address()
            # read_ieee_address() reads xdata, which puts the target into halted
            # debug mode via init_debug_interface(). Unlike flash_ti_debugger(),
            # a plain probe has no other reason to leave the target halted, so
            # resume normal execution before returning.
            self.reset(False)
        return TiProbeInfo(info.programmer, info.chip_id, info.fw_version, info.fw_revision, ieee)

    def chip_erase(self) -> None:
        self.init_debug_interface()
        self.logger("Erasing chip")
        self.send_debug_instructions(bytes([WRAPPER_DEBUG_EXEC, DEBUG_CMD_CHIP_ERASE]))
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            self.send_debug_instructions(bytes([WRAPPER_DEBUG_EXEC_READ, DEBUG_CMD_READ_STATUS]))
            status = self.bulk_in(1)[0]
            if not (status & 0x80):
                self.logger("Erase complete")
                self.initialized = False
                return
            time.sleep(0.1)
        raise UsbError("chip erase timed out")

    def write_flash_fast(self, image: bytes) -> None:
        self.init_debug_interface()
        addr_buf0 = 0x0000
        addr_buf1 = 0x0400
        addr_dma_desc = 0x0800
        ch_dbg_to_buf0 = 0x02
        ch_dbg_to_buf1 = 0x04
        ch_buf0_to_flash = 0x08
        ch_buf1_to_flash = 0x10
        dma_desc = bytes(
            [
                0x62,
                0x60,
                0x00,
                0x00,
                0x04,
                0x00,
                31,
                0x11,
                0x62,
                0x60,
                0x04,
                0x00,
                0x04,
                0x00,
                31,
                0x11,
                0x00,
                0x00,
                0x62,
                0x73,
                0x04,
                0x00,
                18,
                0x42,
                0x04,
                0x00,
                0x62,
                0x73,
                0x04,
                0x00,
                18,
                0x42,
            ]
        )
        self.write_xdata(addr_dma_desc, dma_desc)
        self.write_xdata(0x70D2, bytes([addr_dma_desc & 0xFF]))
        self.write_xdata(0x70D3, bytes([(addr_dma_desc >> 8) & 0xFF]))
        self.write_xdata(XREG_FADDRL, b"\x00")
        self.write_xdata(XREG_FADDRH, b"\x00")

        if len(image) % PROG_BLOCK_SIZE:
            image += b"\xff" * (PROG_BLOCK_SIZE - (len(image) % PROG_BLOCK_SIZE))

        total_blocks = len(image) // PROG_BLOCK_SIZE
        for block in range(total_blocks):
            if block & 1:
                dbg_arm = ch_dbg_to_buf1
                flash_arm = ch_buf1_to_flash
            else:
                dbg_arm = ch_dbg_to_buf0
                flash_arm = ch_buf0_to_flash
            self.write_xdata(XREG_DMAARM, bytes([dbg_arm]))
            chunk = image[block * PROG_BLOCK_SIZE : (block + 1) * PROG_BLOCK_SIZE]
            self.send_raw_data(bytes([0xEE, 0x84, 0x00]) + chunk)
            self._wait_flash_idle()
            self.write_xdata(XREG_DMAARM, bytes([flash_arm]))
            self.write_xdata(XREG_FCTL, b"\x06")
            pct = int(((block + 1) / total_blocks) * 100)
            self.logger(f"Write {pct}% ({block + 1}/{total_blocks} blocks)")
        self._wait_flash_idle()

    def _wait_flash_idle(self) -> None:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            fctl = self.read_xdata(XREG_FCTL, 1)[0]
            if not (fctl & 0x80):
                return
            time.sleep(0.01)
        raise UsbError("flash controller stayed busy")

    def flash_read_start(self) -> None:
        self.control_in(0xC6, 0, 0, 1)
        header = bytes(
            [
                0x40,
                0x55,
                0x00,
                0x72,
                0x56,
                0xE5,
                0xD0,
                0x74,
                0x56,
                0xE5,
                0x92,
                0xBE,
                0x57,
                0x75,
                0x92,
                0x00,
                0x76,
                0x56,
                0xE5,
                0x83,
                0x78,
                0x56,
                0xE5,
                0x82,
                0x7A,
                0x56,
                0xE5,
                0x9F,
            ]
        )
        self.send_raw_data(header)

    def flash_read_end(self) -> None:
        self.send_raw_data(
            bytes([0xCA, 0x57, 0x75, 0x9F, 0xD6, 0x57, 0x90, 0xC4, 0x57, 0x75, 0x92, 0xC2, 0x57, 0x75, 0xD0, 0x90, 0x56, 0x74])
        )

    def create_read_proc(self, count: int) -> bytes:
        proc = bytearray()
        for i in range(count):
            proc.extend([0x5E, 0x55, 0xE4])
            read_cmd = 0x4E
            if (i + 1) % 64 == 0 or i == count - 1:
                read_cmd |= 1
            proc.extend([read_cmd, 0x55, 0x93])
            proc.extend([0x5E, 0x55, 0xA3])
        return bytes(proc)

    def flash_read_near(self, address: int, size: int) -> bytes:
        data = bytearray()
        self.send_raw_data(bytes([0xBE, 0x57, 0x90, (address >> 8) & 0xFF, address & 0xFF]))
        offset = 0
        while offset < size:
            count = min(128, size - offset)
            self.send_raw_data(self.create_read_proc(count))
            chunk = self.bulk_in(count)
            if len(chunk) != count:
                raise UsbError(f"short flash read: {len(chunk)}/{count}")
            data.extend(chunk)
            offset += count
        return bytes(data)

    def flash_read(self, offset: int, size: int) -> bytes:
        all_data = bytearray()
        flash_bank = 0xFF
        while size > 0:
            bank_offset = offset % FLASH_BANK_SIZE
            count = min(size, FLASH_BANK_SIZE)
            flash_bank0 = offset // FLASH_BANK_SIZE
            flash_bank1 = (offset + count) // FLASH_BANK_SIZE
            if flash_bank0 != flash_bank1:
                count = FLASH_BANK_SIZE - bank_offset
            if flash_bank != flash_bank0:
                flash_bank = flash_bank0
                self.write_xdata(XREG_FMAP, bytes([flash_bank]))
            all_data.extend(self.flash_read_near(bank_offset + XBANK_OFFSET, count))
            size -= count
            offset += count
        return bytes(all_data)

    def verify_image(self, image: bytes) -> bool:
        self.init_debug_interface()
        self.logger(f"Verifying {len(image)} bytes by readback")
        self.flash_read_start()
        try:
            offset = 0
            while offset < len(image):
                count = min(4096, len(image) - offset)
                actual = self.flash_read(offset, count)
                expected = image[offset : offset + count]
                if actual != expected:
                    for i, (a, e) in enumerate(zip(actual, expected)):
                        if a != e:
                            raise UsbError(
                                f"verify failed at 0x{offset + i:04X}: expected 0x{e:02X}, got 0x{a:02X}"
                            )
                    raise UsbError(f"verify failed at chunk 0x{offset:04X}")
                offset += count
                self.logger(f"Verify {int((offset / len(image)) * 100)}%")
        finally:
            self.flash_read_end()
        return True


def probe_ti_debugger(*, read_ieee: bool = True) -> TiProbeInfo:
    with XzgTiCcDebugger() as debugger:
        return debugger.probe(read_ieee=read_ieee)


def flash_ti_debugger(firmware: Path, *, erase: bool = True, write: bool = True, verify: bool = True) -> None:
    hex_image = IntelHexImage.from_file(firmware)
    image = image_from_hex(hex_image)
    with XzgTiCcDebugger() as debugger:
        info = debugger.probe(read_ieee=False)
        debugger.logger(f"Target CC{info.chip_id:04X}, debugger fw=0x{info.fw_version:04X}/0x{info.fw_revision:04X}")
        if erase:
            debugger.chip_erase()
        if write:
            debugger.write_flash_fast(image)
        if verify:
            debugger.verify_image(image)
        debugger.reset(False)
        debugger.logger("Target reset to normal mode")
