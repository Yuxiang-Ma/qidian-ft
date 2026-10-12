"""Failure paths found in code review: closing mid-poll, dead sensors, CSV time base."""
import time

import numpy as np
import pytest
from fakes import FakeSensorBackend

from qidian_ft import (
    BackendError,
    FTSensor,
    ProtocolError,
    Recorder,
    SensorConfig,
    create_horizon,
)
from qidian_ft import protocol as p
from qidian_ft.trace import Trace


def connected(**kwargs):
    backend = FakeSensorBackend(**kwargs)
    ft = FTSensor(SensorConfig(timeout_s=0.001), backend=backend)
    ft.connect()
    return ft, backend


def test_close_while_recording_surfaces_error_instead_of_dying_silently():
    ft, _ = connected(io_delay_s=0.001)
    rec = Recorder(ft)
    rec.start()
    time.sleep(0.02)
    ft.close()
    assert rec.wait(timeout=1.0)
    assert isinstance(rec.error, BackendError)


def test_stream_gives_up_when_sensor_goes_silent():
    ft, backend = connected()
    backend.silent = True
    t0 = time.perf_counter()
    with pytest.raises(ProtocolError):
        list(ft.stream(max_samples=10, max_consecutive_errors=5))
    assert time.perf_counter() - t0 < 1.0


def test_stream_zero_duration_is_empty_not_infinite():
    ft, _ = connected()
    assert list(ft.stream(duration_s=0.0)) == []


def test_clear_during_csv_keeps_time_monotonic(tmp_path):
    ft, _ = connected()
    path = tmp_path / "c.csv"
    with Recorder(ft) as rec:
        rec.start_csv(str(path))
        time.sleep(0.02)
        rec.clear()
        time.sleep(0.02)
        rec.stop_csv()
    t = Trace.load_csv(str(path)).t
    assert len(t) > 2 and np.all(np.diff(t) >= 0)


def test_failed_mode_change_does_not_update_state():
    ft, backend = connected()
    ft.set_high_resolution(True)
    backend.silent = True
    with pytest.raises(ProtocolError):
        ft.set_high_resolution(False)
    assert ft.high_res is True


def test_tare_without_mode_records_high_rate():
    ft, backend = connected()
    assert ft.high_res is None
    ft.tare()
    assert backend.control_writes == [p.CTRL_TARE] and ft.high_res is False


def test_connect_failure_after_open_releases_port():
    backend = FakeSensorBackend()
    original_write = backend.write

    def refuse_control(data: bytes) -> int:
        if data[1] == p.FC_WRITE:
            return len(data)  # swallow: the mode write times out
        return original_write(data)

    backend.write = refuse_control
    ft = FTSensor(SensorConfig(resolution="high_res", timeout_s=0.001), backend=backend)
    with pytest.raises(ProtocolError):
        ft.connect()
    assert backend.closed and not ft.is_connected


def test_recorder_restart_after_fixed_horizon_records_again():
    ft, _ = connected()
    rec = Recorder(ft, create_horizon("fixed", duration_s=0.02))
    rec.start()
    assert rec.wait(1.0)
    first = rec.n_samples
    rec.start()
    assert rec.wait(1.0)
    assert rec.n_samples > 1 and first > 1


def test_exception_reply_from_other_slave_is_not_attributed():
    stray = p.with_crc(bytes([7, 0x83, 0x02]))
    with pytest.raises(p.ProtocolError, match="short reply"):
        p.check_reply(stray, 1, p.FC_READ, 13)
