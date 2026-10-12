"""Wiring and link diagnostics that work below the Modbus layer.

``probe`` sends raw read requests and reports what actually came back, so a
silent bus, a wrong baud rate, and a corrupted-but-answering sensor can be told
apart. It also recognises one specific failure seen in the field: replies of
the right length in which only the 1->0 edges survive (each low level decays
back to idle). That pattern cannot be decoded in software and points at the
sensor's RS-485 transmitter or the line, never at the settings.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, List, Optional

from . import protocol as p
from .backends import Backend

PROBE_BAUDS = (460800, 921600, 230400, 115200, 57600, 38400, 19200, 9600)
PROBE_SLAVES = (1, 2, 3, 4, 5)


def falling_edges_only(byte: int) -> int:
    """What a UART reads when only 1->0 transitions survive on the line.

    Data bit ``i`` reads 0 only where the transmitted bit ``i-1`` was 1 and bit
    ``i`` is 0; everything else reads as idle 1.
    """
    out = 0xFF
    for i in range(1, 8):
        if (byte >> (i - 1)) & 1 and not (byte >> i) & 1:
            out &= ~(1 << i)
    return out


def matches_edge_corruption(received: bytes, slave: int, count: int) -> bool:
    """Whether a reply looks like a valid read reply reduced to falling edges.

    Only the header is known in advance, so this checks the first three bytes
    and the length.
    """
    if len(received) != p.read_reply_length(count):
        return False
    header = bytes([slave, p.FC_READ, 2 * count])
    return received[:3] == bytes(falling_edges_only(b) for b in header)


@dataclass(frozen=True)
class ProbeResult:
    baud: int
    slave: int
    reply: bytes
    verdict: str


def classify(reply: bytes, slave: int, count: int) -> str:
    """One-word verdict for a raw reply to an SN read."""
    if not reply:
        return "silent"
    try:
        p.check_reply(reply, slave, p.FC_READ, p.read_reply_length(count))
        return "ok"
    except p.ProtocolError:
        pass
    if matches_edge_corruption(reply, slave, count):
        return "edge-corruption"
    if len(reply) == p.read_reply_length(count):
        return "corrupt"
    return "garbage"


def probe(open_at: Callable[[int], Backend],
          bauds=PROBE_BAUDS, slaves=PROBE_SLAVES,
          log: Optional[Callable[[str], None]] = print,
          stop_on_ok: bool = True) -> List[ProbeResult]:
    """Sweep baud rates and slave ids with a raw SN read.

    Args:
        open_at: Opens a backend at the given baud rate.
        bauds: Baud rates to try, documented one first.
        slaves: Slave ids to try.
        log: Line sink for progress, or None.
        stop_on_ok: Stop at the first clean reply.
    """
    results = []
    for baud in bauds:
        with open_at(baud) as dev:
            for slave in slaves:
                dev.purge()
                dev.write(p.read_request(slave, p.REG_SN, p.REG_SN_COUNT))
                time.sleep(0.01)
                reply = dev.read_exact(64, 0.15)
                verdict = classify(reply, slave, p.REG_SN_COUNT)
                if reply:
                    results.append(ProbeResult(baud, slave, reply, verdict))
                    if log:
                        log(f"  {baud:7d} baud, slave {slave}: {verdict:15s} "
                            f"{len(reply):2d} B  {reply[:24].hex(' ')}")
                    if verdict == "ok" and stop_on_ok:
                        return results
    return results


VERDICT_ADVICE = {
    "ok": "Sensor answers cleanly.",
    "edge-corruption": (
        "The sensor receives and answers, but only the 1->0 edges of its reply "
        "reach the PC. Settings are right; the fault is electrical on the "
        "sensor->PC direction (sensor RS-485 transmitter, connector, or line). "
        "Try another sensor on the same cable to isolate it."),
    "corrupt": ("Replies have the right length but fail CRC: a signal-integrity "
                "problem (A/B, GND reference, termination, adapter)."),
    "garbage": "Bytes arrive but do not form a reply: likely the wrong baud rate.",
    "silent": ("Nothing came back at any setting: check 18-72 V power, A/B, and "
               "whether the sensor was switched to CAN mode."),
}
