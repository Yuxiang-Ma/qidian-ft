import pytest
from fakes import SN, FakeSensorBackend

from qidian_ft import FTSensor, ProtocolError, SensorConfig
from qidian_ft import protocol as p


def connect(**kwargs):
    backend = FakeSensorBackend(**kwargs)
    ft = FTSensor(SensorConfig(), backend=backend)
    ft.connect()
    return ft, backend


def test_connect_reads_identity():
    ft, _ = connect()
    assert ft.info.serial_number == SN
    assert "sw 1.0.0.2" in ft.info.describe()


def test_read_returns_wrench_and_timestamps():
    ft, _ = connect(wrench=(1.5, -2.0, 3.0, 0.1, -0.2, 0.3))
    s = ft.read()
    assert s.force == pytest.approx((1.5, -2.0, 3.0))
    assert s.torque == pytest.approx((0.1, -0.2, 0.3))
    assert s.t_recv >= s.t_send
    assert set(s.as_dict()) == set(p.CHANNELS)


def test_stream_bounds():
    ft, _ = connect()
    assert len(list(ft.stream(max_samples=7))) == 7
    with pytest.raises(ValueError):
        next(ft.stream())


def test_tare_preserves_resolution_bit():
    ft, backend = connect()
    ft.tare()
    ft.set_high_resolution(True)
    ft.tare()
    ft.set_high_resolution(False)
    assert backend.control_writes == [
        p.CTRL_TARE,
        p.CTRL_HIGH_RES,
        p.CTRL_TARE | p.CTRL_HIGH_RES,
        0,
    ]
    assert not any(w & p.CTRL_SWITCH_TO_CAN for w in backend.control_writes)


def test_resolution_applied_on_connect():
    backend = FakeSensorBackend()
    FTSensor(SensorConfig(resolution="high_res"), backend=backend).connect()
    assert backend.control_writes == [p.CTRL_HIGH_RES]


def test_wrong_slave_is_silent():
    backend = FakeSensorBackend(slave=2)
    with pytest.raises(ProtocolError, match="did not answer"):
        FTSensor(SensorConfig(slave=1), backend=backend).connect()
    assert backend.closed


def test_edge_corrupted_sensor_fails_with_crc_error():
    backend = FakeSensorBackend(edge_corrupt=True)
    with pytest.raises(ProtocolError, match="bad CRC"):
        FTSensor(SensorConfig(), backend=backend).connect()


def test_config_validation():
    with pytest.raises(ValueError):
        SensorConfig(latency_ms=0)
    with pytest.raises(ValueError):
        SensorConfig(resolution="fast")
