import time

import numpy as np
import pytest
from fakes import FakeSensorBackend

from qidian_ft import (
    FTSensor,
    Recorder,
    RecordingConfig,
    SensorConfig,
    create_horizon,
    record,
)
from qidian_ft.trace import Trace


@pytest.fixture
def sensor():
    ft = FTSensor(SensorConfig(), backend=FakeSensorBackend())
    ft.connect()
    return ft


def test_fixed_record_and_csv_roundtrip(sensor, tmp_path):
    path = tmp_path / "run.csv"
    trace = record(sensor, create_horizon("fixed", duration_s=0.05),
                   RecordingConfig(csv_path=str(path), tare_on_start=True))
    assert len(trace) > 5
    assert trace.t[-1] >= 0.05
    np.testing.assert_allclose(trace.wrench[0], [1, -2, 3, 0.1, -0.2, 0.3], rtol=1e-6)
    loaded = Trace.load_csv(str(path))
    assert len(loaded) == len(trace)
    assert "1014024530000015" in loaded.metadata["sensor"]
    np.testing.assert_allclose(loaded["fz"], 3.0)


def test_csv_toggle_while_running(sensor, tmp_path):
    rec = Recorder(sensor)
    rec.start()
    time.sleep(0.02)
    path = tmp_path / "part.csv"
    rec.start_csv(str(path))
    time.sleep(0.03)
    assert rec.stop_csv() == str(path)
    rows = rec.csv_rows
    time.sleep(0.02)
    assert rec.csv_rows == rows  # nothing written after stop
    rec.stop()
    assert len(Trace.load_csv(str(path))) == rows > 0


def test_tare_gets_bus_while_recorder_polls():
    # A GUI-thread command must get the bus between polls. The fake blocks
    # with the GIL released, like a real read.
    sensor = FTSensor(SensorConfig(), backend=FakeSensorBackend(io_delay_s=0.002))
    sensor.connect()
    with Recorder(sensor):
        time.sleep(0.02)
        for _ in range(10):
            t0 = time.perf_counter()
            sensor.tare()
            assert time.perf_counter() - t0 < 0.05


def test_snapshot_window_and_clear(sensor):
    with Recorder(sensor) as rec:
        time.sleep(0.05)
        full = rec.snapshot()
        recent = rec.snapshot(window_s=0.01)
        assert 0 < len(recent) <= len(full)
        assert recent[-1][0] - recent[0][0] <= 0.01 + 1e-9
        rec.clear()
        assert rec.n_samples < len(full)


def test_horizons():
    fixed = create_horizon("fixed", duration_s=5)
    assert fixed.xlim(1.0) == (0.0, 5.0) and not fixed.should_continue(5.0)
    inf = create_horizon("infinite", window_s=10)
    assert inf.xlim(3.0) == (0.0, 10.0) and inf.xlim(25.0) == (15.0, 25.0)
    with pytest.raises(KeyError):
        create_horizon("nope")


def test_plot_trace_writes_png(sensor, tmp_path):
    from qidian_ft.plotting import plot_trace
    trace = record(sensor, create_horizon("fixed", duration_s=0.02))
    out = tmp_path / "t.png"
    plot_trace(trace, save_path=str(out))
    assert out.stat().st_size > 1000
