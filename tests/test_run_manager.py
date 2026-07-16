import sys
import time

import pytest

import run_manager


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    run_manager._reset_for_tests()
    # phase 1/2 用轻量真 subprocess;phase 3/4 打桩避免碰真实文件
    monkeypatch.setitem(
        run_manager.PHASE_COMMANDS, "signal_pipeline",
        lambda source: [sys.executable, "-c", "print('signal ok')"],
    )
    monkeypatch.setitem(
        run_manager.PHASE_COMMANDS, "regime_engine",
        lambda source: [sys.executable, "-c", "print('regime ok')"],
    )
    monkeypatch.setattr(run_manager, "_archive_and_diff",
                        lambda source: {"snapshot_id": "test-id", "headline_count": 0})
    yield


def wait_done(timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if run_manager.get_status()["state"] in ("succeeded", "failed"):
            return run_manager.get_status()
        time.sleep(0.02)
    raise AssertionError("run did not finish in time")


def test_successful_run_reaches_succeeded():
    assert run_manager.start_run("mock") is True
    status = wait_done()
    assert status["state"] == "succeeded"
    assert status["result"]["snapshot_id"] == "test-id"
    assert any("signal ok" in line for line in status["log_tail"])
    assert status["phase"] is None and status["finished_at"] is not None


def test_failure_propagates(monkeypatch):
    monkeypatch.setitem(
        run_manager.PHASE_COMMANDS, "regime_engine",
        lambda source: [sys.executable, "-c", "import sys; sys.exit(3)"],
    )
    run_manager.start_run("mock")
    status = wait_done()
    assert status["state"] == "failed"
    assert "regime_engine" in status["error"]


def test_concurrent_start_rejected(monkeypatch):
    monkeypatch.setitem(
        run_manager.PHASE_COMMANDS, "signal_pipeline",
        lambda source: [sys.executable, "-c", "import time; time.sleep(0.5)"],
    )
    assert run_manager.start_run("mock") is True
    assert run_manager.start_run("mock") is False  # 已在跑 -> 拒绝
    wait_done()
    assert run_manager.start_run("mock") is True   # 结束后可再跑
    wait_done()


def test_status_is_a_copy():
    run_manager.start_run("mock")
    status = run_manager.get_status()
    status["state"] = "hacked"
    assert run_manager.get_status()["state"] != "hacked"
    wait_done()
