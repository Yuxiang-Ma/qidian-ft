"""Modbus-RTU framing and register map for the QS-Z4-4530Ti-1 F/T sensor.

Everything here is pure: bytes in, bytes or numbers out. No I/O, no third-party
imports, so it is trivially testable and safe to reuse elsewhere.

Register map (vendor document "六维传感器ModBUS_RTU协议"). All multi-byte values
are big-endian, including the IEEE-754 floats (high register first):

    0x0100  16 regs  char    serial number (rw)
    0x0110   2 regs  uint32  software version
    0x0112   2 regs  uint32  hardware version
    0x0300  12 regs  float   Fx Fy Fz [N]  Mx My Mz [Nm]
    0x0400   1 reg   uint16  control word (write only)
    0x0602   1 reg   uint16  Modbus slave id 1..247

Only function codes 0x03 (read holding registers) and 0x10 (write multiple
registers) are supported by the sensor.
"""
from __future__ import annotations

import struct
from typing import Sequence, Tuple

DEFAULT_BAUD = 460800
DEFAULT_SLAVE = 0x01

FC_READ = 0x03
FC_WRITE = 0x10

REG_SN = 0x0100
REG_SN_COUNT = 16
REG_SW_VERSION = 0x0110
REG_HW_VERSION = 0x0112
REG_WRENCH = 0x0300
REG_WRENCH_COUNT = 12
REG_CONTROL = 0x0400
REG_SLAVE_ID = 0x0602

# Control word bits (register 0x0400).
CTRL_TARE = 1 << 0
CTRL_SWITCH_TO_CAN = 1 << 1
CTRL_HIGH_RES = 1 << 2

CHANNELS = ("fx", "fy", "fz", "mx", "my", "mz")
UNITS = ("N", "N", "N", "Nm", "Nm", "Nm")

_EXCEPTIONS = {
    0x01: "illegal function",
    0x02: "illegal data address",
    0x03: "illegal data value",
    0x04: "slave device failure",
}


class ProtocolError(RuntimeError):
    """A reply was missing, truncated, mis-addressed, or failed its CRC."""


def crc16(data: bytes) -> int:
    """Modbus CRC-16 (reflected poly 0xA001, init 0xFFFF)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def with_crc(body: bytes) -> bytes:
    """Append the little-endian CRC that Modbus-RTU puts on every frame."""
    return body + struct.pack("<H", crc16(body))


def read_request(slave: int, address: int, count: int) -> bytes:
    """Build a 0x03 read-holding-registers request."""
    return with_crc(struct.pack(">BBHH", slave, FC_READ, address, count))


def write_request(slave: int, address: int, values: Sequence[int]) -> bytes:
    """Build a 0x10 write-multiple-registers request."""
    body = struct.pack(">BBHHB", slave, FC_WRITE, address, len(values), 2 * len(values))
    body += b"".join(struct.pack(">H", v & 0xFFFF) for v in values)
    return with_crc(body)


def read_reply_length(count: int) -> int:
    """Bytes in a successful 0x03 reply: addr, fc, byte count, data, CRC."""
    return 5 + 2 * count


WRITE_REPLY_LENGTH = 8
EXCEPTION_REPLY_LENGTH = 5


def check_reply(reply: bytes, slave: int, function: int, expected_len: int) -> bytes:
    """Validate a reply frame and return its payload.

    For a read the payload is the register data; for a write it is the echoed
    address and count.

    Raises:
        ProtocolError: On timeout, exception response, wrong slave/function,
            truncation, or CRC mismatch. The message includes the raw bytes,
            which is what you want when debugging wiring.
    """
    if not reply:
        raise ProtocolError("no reply (timeout)")
    if (len(reply) >= EXCEPTION_REPLY_LENGTH and reply[0] == slave
            and reply[1] == (function | 0x80)):
        frame = reply[:EXCEPTION_REPLY_LENGTH]
        if crc16(frame[:-2]) == struct.unpack("<H", frame[-2:])[0]:
            code = frame[2]
            raise ProtocolError(
                f"exception 0x{code:02X} ({_EXCEPTIONS.get(code, 'unknown')})")
    if len(reply) < expected_len:
        raise ProtocolError(
            f"short reply: {len(reply)}/{expected_len} bytes: {reply.hex(' ')}")
    frame = reply[:expected_len]
    if crc16(frame[:-2]) != struct.unpack("<H", frame[-2:])[0]:
        raise ProtocolError(f"bad CRC: {frame.hex(' ')}")
    if frame[0] != slave or frame[1] != function:
        raise ProtocolError(
            f"unexpected header {frame[:2].hex(' ')} (want {slave:02x} {function:02x})")
    if function == FC_READ:
        return frame[3:-2]
    return frame[2:-2]


def decode_wrench(payload: bytes) -> Tuple[float, float, float, float, float, float]:
    """Six big-endian float32 values: Fx Fy Fz Mx My Mz."""
    if len(payload) != 24:
        raise ProtocolError(f"wrench payload must be 24 bytes, got {len(payload)}")
    return struct.unpack(">6f", payload)


def decode_sn(payload: bytes) -> str:
    """Serial number: ASCII, NUL-padded to 32 bytes."""
    return payload.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()


def decode_versions(payload: bytes) -> Tuple[int, int]:
    """(software, hardware) version words."""
    return struct.unpack(">II", payload)


def format_version(word: int) -> str:
    """0x01000002 -> '1.0.0.2'."""
    return ".".join(str((word >> s) & 0xFF) for s in (24, 16, 8, 0))
