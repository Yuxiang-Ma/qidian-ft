"""A software sensor that answers Modbus-RTU like the real QS-Z4-4530Ti-1."""
from __future__ import annotations

import struct
import time
from typing import List, Optional, Tuple

from qidian_ft import protocol as p
from qidian_ft.backends.base import Backend
from qidian_ft.diagnose import falling_edges_only

SN = "1014024530000015"


class FakeSensorBackend(Backend):
    """Answers reads of SN, versions and wrench; records control writes."""

    name = "fake"

    def __init__(self, wrench: Tuple[float, ...] = (1.0, -2.0, 3.0, 0.1, -0.2, 0.3),
                 slave: int = 1, edge_corrupt: bool = False, silent: bool = False,
                 io_delay_s: float = 0.0) -> None:
        self.wrench = wrench
        self.slave = slave
        self.edge_corrupt = edge_corrupt
        self.silent = silent
        self.io_delay_s = io_delay_s  # >0 mimics a blocking read that drops the GIL
        self.control_writes: List[int] = []
        self._rx = b""
        self.closed = False

    def _registers(self, address: int, count: int) -> Optional[bytes]:
        if address == p.REG_SN and count == p.REG_SN_COUNT:
            return SN.encode().ljust(32, b"\x00")
        if address == p.REG_SW_VERSION and count == 4:
            return struct.pack(">II", 0x01000002, 0x01000001)
        if address == p.REG_WRENCH and count == p.REG_WRENCH_COUNT:
            return struct.pack(">6f", *self.wrench)
        return None

    def write(self, data: bytes) -> int:
        if self.silent or data[0] != self.slave:
            return len(data)
        fc = data[1]
        if fc == p.FC_READ:
            address, count = struct.unpack(">HH", data[2:6])
            payload = self._registers(address, count)
            if payload is None:
                reply = p.with_crc(bytes([self.slave, 0x83, 0x02]))
            else:
                reply = p.with_crc(bytes([self.slave, fc, len(payload)]) + payload)
        elif fc == p.FC_WRITE:
            address, count = struct.unpack(">HH", data[2:6])
            if address == p.REG_CONTROL:
                self.control_writes.append(struct.unpack(">H", data[7:9])[0])
            reply = p.with_crc(data[:6])
        else:
            reply = p.with_crc(bytes([self.slave, fc | 0x80, 0x01]))
        if self.edge_corrupt:
            reply = bytes(falling_edges_only(b) for b in reply)
        self._rx += reply
        return len(data)

    def read_available(self) -> bytes:
        out, self._rx = self._rx, b""
        return out

    def read_exact(self, n: int, timeout_s: float) -> bytes:
        if self.io_delay_s:
            time.sleep(self.io_delay_s)
        out, self._rx = self._rx[:n], self._rx[n:]
        return out

    def purge(self) -> None:
        self._rx = b""

    def get_latency(self) -> Optional[int]:
        return 1

    def close(self) -> None:
        self.closed = True
