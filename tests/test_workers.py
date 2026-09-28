"""Tests de BaseWorker / WorkerPool (hilos con manejo de errores uniforme)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication, pyqtSignal  # noqa: E402
from PyQt6.QtTest import QSignalSpy  # noqa: E402

from workers import BaseWorker, WorkerPool, connect_all  # noqa: E402

_app = QCoreApplication.instance() or QCoreApplication([])  # noqa: F841


class _OkWorker(BaseWorker):
    done = pyqtSignal(int)

    def work(self) -> None:
        self.done.emit(42)


class _BoomWorker(BaseWorker):
    def work(self) -> None:
        raise ValueError("kaboom")


class _EmptyErrorWorker(BaseWorker):
    def work(self) -> None:
        raise RuntimeError()


class _SlowCancellable(BaseWorker):
    ticks = pyqtSignal(int)

    def work(self) -> None:
        n = 0
        while not self.is_cancelled and n < 10_000:
            n += 1
            self.msleep(1)
        self.ticks.emit(n)


def _wait(worker, ms: int = 5000) -> None:
    assert worker.wait(ms)
    QCoreApplication.processEvents()


def test_base_worker_emits_done():
    w = _OkWorker(auto_delete=False)
    spy_done = QSignalSpy(w.done)
    spy_fail = QSignalSpy(w.failed)
    w.start(); _wait(w)
    assert len(spy_done) == 1 and spy_done[0][0] == 42
    assert len(spy_fail) == 0


def test_base_worker_exception_becomes_failed():
    w = _BoomWorker(auto_delete=False)
    spy_fail = QSignalSpy(w.failed)
    w.start(); _wait(w)
    assert len(spy_fail) == 1
    assert spy_fail[0][0] == "kaboom"


def test_base_worker_empty_error_uses_class_name():
    w = _EmptyErrorWorker(auto_delete=False)
    spy_fail = QSignalSpy(w.failed)
    w.start(); _wait(w)
    assert spy_fail[0][0] == "RuntimeError"


def test_worker_pool_tracks_and_untracks():
    pool = WorkerPool()
    w = _OkWorker(auto_delete=False)
    pool.track(w)
    assert w in pool and len(pool) == 1
    _wait(w)
    # el desregistro llega por la señal finished (cola de eventos)
    for _ in range(50):
        if w not in pool:
            break
        QCoreApplication.processEvents()
    assert w not in pool and len(pool) == 0


def test_worker_pool_shutdown_cancels_running():
    pool = WorkerPool()
    w = _SlowCancellable(auto_delete=False)
    spy = QSignalSpy(w.ticks)
    pool.track(w)
    assert pool.any_running(_SlowCancellable)
    pool.shutdown(timeout_ms=5000)
    assert not w.isRunning()
    QCoreApplication.processEvents()
    assert len(spy) == 1
    assert spy[0][0] < 10_000, "debería haberse cancelado antes de agotar el bucle"
    assert len(pool) == 0


def test_connect_all_wires_slots():
    got: list[int] = []
    w = connect_all(_OkWorker(auto_delete=False), done=got.append)
    w.start(); _wait(w)
    assert got == [42]
