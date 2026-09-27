# DockerLabs GUI

Cliente de escritorio PyQt6 para [DockerLabs](https://dockerlabs.es).
Sustituye al GUI Tkinter del proyecto original con una interfaz profesional,
sobria y user-friendly, e incorpora descargas directas desde MEGA.

## Novedades v0.4

- **Iconos SVG embebidos** — se ven igual en cualquier SO (no dependen de
  emojis del sistema).
- **Renombrado a `DockerLabs GUI`**.
- **Sin pestaña de exportar CSV**: el catálogo se actualiza en background al
  arrancar (si hay internet) y, si no hay conexión, se carga el último CSV
  cacheado.
- **Menú contextual** (clic derecho) en la tabla de máquinas con dos acciones:
  - Marcar / desmarcar como completada.
  - Descargar desde MEGA (usando el enlace del CSV).
- **Página `Descargas`** con barra de progreso por máquina (porcentaje,
  velocidad, ETA, archivo destino) y soporte de cancelación.
- **Cliente MEGA propio** (`mega_downloader.py`) — descarga streaming con
  descifrado AES-CTR y verificación de integridad CBC-MAC; no necesita
  cuenta y reproduce el protocolo de enlace público.

## Apartados del menú

| Apartado | Para qué sirve |
|---|---|
| Dashboard | Tarjetas resumen (total, completadas, descargas, sesión) |
| Máquinas | Catálogo público con búsqueda + clic derecho |
| Descargas | Progreso en tiempo real de las descargas desde MEGA |
| Completadas | Lista sincronizada con tu cuenta |
| Sesión | Login JSON contra `/api/auth/login` con CSRF |
| Ajustes | Información de paths y comportamiento offline |
| Acerca de | Créditos y stack |

## Instalación

```bash
pip install PyQt6 requests pycryptodome
cd dockerlabs-gui
python3 main.py
```

## Estructura de carpetas

```
~/.dockerlabs-gui/
├── csv/                ← Catálogo (dockerlabs_maquinas.csv, *_por_autor.csv, resumen.json)
└── downloads/          ← Archivos .zip de máquinas descargadas desde MEGA
```

Si vienes de v0.3 (`~/.dockerlabs-qt/csv/`), ese CSV se usa como fallback offline.

## Endpoints utilizados

| Endpoint | Uso |
|---|---|
| `GET /api` | Catálogo público |
| `GET /login` + `POST /api/auth/login` | Login con CSRF |
| `GET /api/completed_machines/<nombre>` | Estado completada |
| `POST /api/toggle_completed_machine` | Marcar / desmarcar |
| `GET /api/author_profile?nombre=<u>` | Avatar |
| `GET /` (autenticado) | Lista de completadas (parseo HTML) |
| `POST g.api.mega.co.nz/cs` `{a:'g',g:1,p:<id>}` | Metadata del archivo en MEGA |
| `GET <download_url>` (stream) | Bytes cifrados AES-CTR |
