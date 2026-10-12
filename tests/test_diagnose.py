from fakes import FakeSensorBackend

from qidian_ft import protocol as p
from qidian_ft.diagnose import classify, falling_edges_only, matches_edge_corruption, probe

# Reply to an SN read from a faulty unit, captured on hardware.
FAULTY_SN_REPLY = bytes.fromhex(
    "fd fb bf bd bf bd b7 bf bb b7 b5 bb bf bf bf bf bf bd bb"
    + " ff" * 16 + " f7 6b")


def test_falling_edge_model_matches_observed_bytes():
    observed = {0x01: 0xFD, 0x03: 0xFB, 0x20: 0xBF, 0x18: 0xDF, 0x08: 0xEF, 0x00: 0xFF}
    for sent, got in observed.items():
        assert falling_edges_only(sent) == got


def test_faulty_version_frame_is_exactly_reproduced():
    good = bytes.fromhex("01 03 08 01 00 00 02 01 00 00 01 ed e7")
    faulty = bytes.fromhex("fd fb ef fd ff ff fb fd ff ff fd ed f7")
    assert bytes(falling_edges_only(b) for b in good) == faulty


def test_classify_real_faulty_reply():
    assert matches_edge_corruption(FAULTY_SN_REPLY, 1, 16)
    assert classify(FAULTY_SN_REPLY, 1, 16) == "edge-corruption"
    assert classify(b"", 1, 16) == "silent"
    assert classify(b"\x00\x12", 1, 16) == "garbage"


def test_probe_finds_healthy_sensor():
    results = probe(lambda baud: FakeSensorBackend(), bauds=(460800,), log=None)
    assert results[-1].verdict == "ok"


def test_probe_flags_edge_corruption():
    results = probe(lambda baud: FakeSensorBackend(edge_corrupt=True),
                    bauds=(460800,), slaves=(1,), log=None)
    assert [r.verdict for r in results] == ["edge-corruption"]


def test_probe_reports_silence():
    assert probe(lambda baud: FakeSensorBackend(silent=True), bauds=(460800, 115200),
                 log=None) == []


def test_sn_request_is_what_probe_sends():
    assert p.read_request(1, p.REG_SN, 16).hex(" ").startswith("01 03 01 00 00 10")
