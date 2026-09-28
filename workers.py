"""Infraestructura común para hilos de trabajo (QThread) de la GUI.

- `BaseWorker`: QThread con manejo de errores uniforme. Las subclases
  implementan `work()`; cualquier excepción se registra y se emite por
  `failed(str)` en lugar de tumbar el hilo silenciosamente. Se destruye solo
  (`deleteLater`) al terminar, así los propietarios no tienen que hacerlo.
- `WorkerPool`: registro de workers vivos con `track()` y `shutdown()` para
  esperar/cancelarlos al cerrar la ventana o el widget propietario.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Iterable, List, Optional, Type

from PyQt6.QtCore import QObject, QThread, pyqtSignal

logger = logging.getLogger(__name__)


class BaseWorker(QThread):
    """QThread base: `work()` en vez de `run()`, errores → `failed`.

    Subclases:
        class MyWorker(BaseWorker):
            done = pyqtSignal(object)
            def work(self):
                self.done.emit(self.client.fetch())

    `format_error(exc)` permite personalizar el mensaje emitido.
    `cancel()` pone un flag cooperativo que `work()` puede consultar vía
    `is_cancelled`.
    """

    failed = pyqtSignal(str)

    def __init__(self, parent: Optional[QObject] = None, *, auto_delete: bool = True) -> None:
        super().__init__(parent)
        self._cancel = threading.Event()
        if auto_delete:
            self.finished.connect(self.deleteLater)

    # ---- API para subclases ----

    def work(self) -> None:  # pragma: no cover - abstracto
        raise NotImplementedError

    def format_error(self, exc: BaseException) -> str:
        return str(exc) or exc.__class__.__name__

    @property
    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> None:
        self._cancel.set()

    # ---- QThread ----

    def run(self) -> None:  # noqa: D401
        try:
            self.work()
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s falló: %s", self.__class__.__name__, exc, exc_info=True)
            try:
                self.failed.emit(self.format_error(exc))
            except RuntimeError:
                # El objeto Qt puede haber sido destruido durante el cierre.
                pass


class WorkerPool:
    """Mantiene referencias a los workers en ejecución y los cierra en orden."""

    def __init__(self) -> None:
        self._workers: List[QThread] = []

    def __len__(self) -> int:
        return len(self._workers)

    def __iter__(self):
        return iter(list(self._workers))

    def __contains__(self, w: object) -> bool:
        return w in self._workers

    def track(self, worker: QThread, *, start: bool = True) -> QThread:
        """Registra el worker, lo desregistra al acabar y (opcional) lo arranca."""
        self._workers.append(worker)
        worker.finished.connect(lambda: self.untrack(worker))
        if start:
            worker.start()
        return worker

    def untrack(self, worker: QThread) -> None:
        try:
            self._workers.remove(worker)
        except ValueError:
            pass

    def running(self, kind: Optional[Type[QThread]] = None) -> List[QThread]:
        out: List[QThread] = []
        for w in list(self._workers):
            try:
                if (kind is None or isinstance(w, kind)) and w.isRunning():
                    out.append(w)
            except RuntimeError:
                self.untrack(w)
        return out

    def any_running(self, kind: Optional[Type[QThread]] = None) -> bool:
        return bool(self.running(kind))

    def shutdown(self, timeout_ms: int = 1500,
                 cancel: Optional[Callable[[QThread], None]] = None) -> None:
        """Cancela (si procede) y espera a los workers vivos hasta `timeout_ms` cada uno."""
        for w in list(self._workers):
            try:
                if cancel is not None:
                    cancel(w)
                elif isinstance(w, BaseWorker):
                    w.cancel()
                if w.isRunning():
                    w.quit()
                    if not w.wait(timeout_ms):
                        logger.warning("%s no terminó en %d ms", w.__class__.__name__, timeout_ms)
            except RuntimeError:
                # Ya destruido por Qt
                pass
        self._workers.clear()


def connect_all(worker: QThread, **slots: Callable) -> QThread:
    """Azúcar: `connect_all(w, done=self._on_done, failed=self._on_fail)`."""
    for name, slot in slots.items():
        getattr(worker, name).connect(slot)
    return worker


__all__: Iterable[str] = ("BaseWorker", "WorkerPool", "connect_all")
