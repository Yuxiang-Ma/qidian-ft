import struct

import pytest

from qidian_ft import protocol as p


def test_crc_matches_modbus_reference_frame():
    # Canonical Modbus example: read 10 registers from slave 1 -> CRC C5 CD.
    assert p.read_request(1, 0x0000, 10) == bytes.fromhex("01 03 00 00 00 0A C5 CD")


def test_sn_request_bytes():
    req = p.read_request(1, p.REG_SN, 16)
    assert req[:6] == bytes.fromhex("01 03 01 00 00 10")
    assert p.crc16(req) == 0  # CRC over a frame including its CRC is zero


def test_write_request_layout():
    req = p.write_request(1, p.REG_CONTROL, [p.CTRL_TARE | p.CTRL_HIGH_RES])
    assert req[:9] == bytes.fromhex("01 10 04 00 00 01 02 00 05")
    assert p.crc16(req) == 0


def test_check_reply_returns_payload():
    payload = struct.pack(">6f", 1, 2, 3, 4, 5, 6)
    reply = p.with_crc(bytes([1, 3, 24]) + payload)
    assert p.check_reply(reply, 1, p.FC_READ, p.read_reply_length(12)) == payload
    assert p.decode_wrench(payload) == (1, 2, 3, 4, 5, 6)


def test_version_reply_from_hardware():
    # Captured from SN 1014024530000015.
    reply = bytes.fromhex("01 03 08 01 00 00 02 01 00 00 01 ed e7")
    payload = p.check_reply(reply, 1, p.FC_READ, 13)
    sw, hw = p.decode_versions(payload)
    assert (p.format_version(sw), p.format_version(hw)) == ("1.0.0.2", "1.0.0.1")


@pytest.mark.parametrize("reply, match", [
    (b"", "timeout"),
    (bytes.fromhex("01 03 08 01 00"), "short reply"),
    (bytes.fromhex("01 03 08 01 00 00 02 01 00 00 01 ed e8"), "bad CRC"),
    (p.with_crc(bytes([1, 0x83, 0x02])), "illegal data address"),
    (p.with_crc(bytes([2, 3, 2, 0, 0])), "unexpected header"),
])
def test_check_reply_errors(reply, match):
    with pytest.raises(p.ProtocolError, match=match):
        p.check_reply(reply, 1, p.FC_READ, len(reply) if match == "unexpected header" else 13)


def test_decode_sn_strips_padding():
    assert p.decode_sn(b"1014024530000015" + bytes(16)) == "1014024530000015"
