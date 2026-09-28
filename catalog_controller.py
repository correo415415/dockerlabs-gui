"""Controlador del catálogo de máquinas (caché JSON + refresco en background).

Señales para la UI:
    catalog_changed(Catalog)     catálogo listo (de caché o recién descargado)
    loading(bool, str)           estado de carga para la tabla
    refresh_failed(str, bool)    error, `has_cache` (True si seguimos con caché)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from catalog import Catalog, CatalogStore
from workers import BaseWorker, WorkerPool

logger = logging.getLogger(__name__)


class CatalogWorker(BaseWorker):
    """Descarga /api en segundo plano y actualiza la caché JSON."""
    done = pyqtSignal(object)   # Catalog

    def __init__(self, store: CatalogStore, fetch: Callable[[], dict], parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.fetch = fetch

    def work(self) -> None:
        self.done.emit(self.store.refresh(self.fetch))


class CatalogController(QObject):
    catalog_changed = pyqtSignal(object)      # Catalog
    loading = pyqtSignal(bool, str)           # activo, mensaje
    refresh_failed = pyqtSignal(str, bool)    # error, has_cache

    def __init__(self, cache_path: Path, fetch: Callable[[], dict],
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.store = CatalogStore(Path(cache_path))
        self._fetch = fetch
        self.catalog: Optional[Catalog] = None
        self._workers = WorkerPool()

    # ---- consultas ----

    @property
    def refreshing(self) -> bool:
        return self._workers.any_running(CatalogWorker)

    def set_fetch(self, fetch: Callable[[], dict]) -> None:
        """Cambia la función de descarga (p. ej. tras crear un cliente nuevo)."""
        self._fetch = fetch

    # ---- acciones ----

    def load_cached(self) -> bool:
        """Carga la caché local si existe. Devuelve True si había algo."""
        cat = self.store.load_cached()
        if cat is None:
            self.loading.emit(True, "Descargando catálogo…")
            return False
        self._apply(cat)
        return True

    def refresh(self) -> bool:
        """Lanza la descarga en background (una sola a la vez)."""
        if self.refreshing:
            return False
        self.loading.emit(True, "")
        w = CatalogWorker(self.store, self._fetch, parent=self)
        w.done.connect(self._on_refreshed)
        w.failed.connect(self._on_failed)
        w.finished.connect(lambda: self.loading.emit(False, ""))
        self._workers.track(w)
        return True

    def shutdown(self) -> None:
        self._workers.shutdown(1500)

    # ---- internos ----

    def _apply(self, cat: Catalog) -> None:
        self.catalog = cat
        self.catalog_changed.emit(cat)

    def _on_refreshed(self, cat) -> None:
        self._apply(cat)
        logger.info("catálogo actualizado: %d máquinas", len(cat.machines))

    def _on_failed(self, err: str) -> None:
        logger.warning("catálogo: %s", err)
        self.refresh_failed.emit(err, self.catalog is not None)
