# TODO — DockerLabs GUI

Hoja de ruta tras el análisis del código v8 (`dockerlabs-guiv8.zip`), de la web
`https://dockerlabs.es` y de su API pública `https://dockerlabs.es/api`.

Leyenda: `[ ]` pendiente · `[~]` en curso · `[x]` hecho

---

## 0. Análisis realizado (resumen)

### 0.1 Código original (v8 / pyproject 0.5.0)

| Módulo | Estado | Observaciones |
|---|---|---|
| `main.py` (972 líneas) | OK | `MainWindow` con demasiadas responsabilidades; workers `QThread` ad-hoc; `statusBar()` sobreescrito con stub. |
| `dockerlabs_api.py` | OK | Cliente `urllib` + cookiejar. Login con CSRF. `verify_session()` es un placeholder que siempre devuelve `True`. |
| `dockerlabs_api_ext.py` | OK | `completed_machines_from_home()` parsea HTML con regex frágil (depende del orden exacto de atributos). |
| `dockerlabs_csv.py` | OK | Exporta catálogo a CSV. Funciona pero es un paso intermedio innecesario (JSON → CSV → tabla). |
| `mega_downloader.py` (859 líneas) | **OBSOLETO** | Cliente MEGA completo (AES-CTR + CBC-MAC). Ya **ninguna** máquina usa MEGA. |
| `download_manager.py` | Acoplado a MEGA | Importa excepciones `Mega*`; señal `quota_exceeded` específica de MEGA. |
| `widgets/pages.py` (1192 líneas) | OK | Todas las páginas en un solo fichero. Tabla se re-renderiza completa en cada cambio (`_render`). |
| `widgets/sidebar.py` | OK | Animación colapso bien resuelta. |
| `widgets/toast.py` | OK | El `QGraphicsOpacityEffect` se crea pero no se usa (conflicto con drop-shadow). |
| `notifier.py`, `settings_store.py`, `completed_store.py` | OK | Sencillos y correctos. |
| `README.md` | Desactualizado | Habla de MEGA, v0.4. |
| Tests | **No hay** | Cero cobertura. |

Problemas de estabilidad detectados:
- `main.py`: `self.sidebar.user_pill.mouseReleaseEvent = lambda ...` (monkey-patch de un método de instancia).
- Múltiples `except Exception: pass` que ocultan errores.
- `MachinesPage._render()` reconstruye toda la tabla (205 filas × 6 celdas + iconos SVG) en cada keypress del buscador y en cada cambio de estado → lag perceptible.
- `DownloadItemWidget` importa `human_size` dentro del método (import en caliente en cada tick de progreso).
- `SessionRestoreWorker` toca `client._csrf_token` (atributo privado).
- Workers guardados en `self._workers` con `lambda` de limpieza que captura el worker → posibles referencias colgantes al cerrar.
- Sin `logging` configurado → imposible depurar en producción.
- Sin manejo de excepciones global (`sys.excepthook`) → un fallo en un slot cierra la app en silencio.

### 0.2 Web / API (`https://dockerlabs.es`)

`GET /api` devuelve JSON con estas colecciones:

| Clave | Tipo | Contenido |
|---|---|---|
| `info_maquinas` | list[205] | `id, nombre, dificultad, clase, color, autor, enlace_autor, fecha, imagen, descripcion, link_descarga, imagen_url` |
| `maquinas` | list[str] | Solo nombres. |
| `metadata` | dict | `total_creadores, total_puntos, total_writeups`. |
| `ranking_creadores` | list | `id, nombre, maquinas`. |
| `ranking_writeups` | list | `id, nombre, puntos`. |
| `writeups` | dict | `textos` (3926) y `videos` (674): `id, maquina, autor, url, tipo, created_at`. |

**Cambio clave**: `link_descarga` ya NO es MEGA. Las 205 máquinas apuntan a
`https://gestion-maquinas.dockerlabs.es/dl/<slug>.zip`:
- Solo `GET` (HEAD → 405). Devuelve `Content-Length` y
  `Content-Disposition: attachment; filename="<slug>.zip"`.
- **No soporta `Range`** (devuelve 200 completo) → no hay reanudación ni paralelo.
- Devuelve **500 intermitente** (observado en ~7/40 peticiones; reintentando funciona).
  Hay que implementar reintentos con backoff.
- 404 → `{"detail":"No encontrado"}`.
- La web usa `/maquinas/<id>/descargar` (página HTML con instrucciones) que enlaza al mismo zip.

Otros endpoints públicos descubiertos (JS del frontend):
- `GET /api/get_machine_rating/<nombre>` → `{average, count, details{dificultad,aprendizaje,recomendaria,diversion}, user_rating}`
- `GET /api/writeups/<nombre>` → lista `{id, name, url, type, es_usuario_registrado}`
- `GET /api/author_profile?nombre=<u>` → perfil + `profile_image_url` + máquinas del autor.
- `GET /api/ranking_autores`, `GET /api/ranking_writeups`
- `GET /img/maquina/<id>` (webp) · `GET /img/perfil/<id>` (webp)
- Autenticados: `GET /api/completed_machines/<nombre>`, `POST /api/toggle_completed_machine`,
  `POST /api/rate_machine`, `POST /api/submit_writeup`, `GET /api/certificado/<nombre>/disponible`.
- Login: `GET /login` (meta `csrf-token`) + `POST /api/auth/login` JSON + header `X-CSRFToken`.
- Home autenticada: `<div ... class="maquina-item medio ... completada" data-id="N">` → marcador `completada`.

### 0.3 Formato del CTF (analizado `breakmyssh.zip`)

```
breakmyssh.zip
├── breakmyssh.tar     ← imagen Docker (docker save), RepoTags: breakmyssh:latest
└── auto_deploy.sh     ← script bash: docker load + docker run -d + inspect IP; Ctrl+C limpia
```
- El `.tar` es un `docker save` (manifest.json, repositories, layers). `amd64/linux`.
- El script oficial hace: `docker load -i X.tar` → `docker run -d --name X_container X` →
  `docker inspect` para sacar la IP → espera; al `Ctrl+C` hace `stop`/`rm`/`rmi`.
- La imagen expone puertos (`ExposedPorts`) y el atacante accede por la IP interna del
  contenedor (red bridge). **En Windows/macOS (Docker Desktop) la IP del bridge no es
  alcanzable** desde el host → hay que publicar puertos (`-p`) o usar `host` networking (solo Linux).

---

## 1. Migrar descargas de MEGA a HTTP directo  `[x]`

- [x] Nuevo `http_downloader.py`: streaming `requests`, `Content-Disposition` → nombre de fichero,
      progreso (bytes/velocidad/ETA), cancelación, `.part` + rename atómico.
- [x] Reintentos con backoff exponencial ante 5xx / errores de red (el servidor da 500 intermitentes).
- [x] Verificación básica del zip al terminar (`zipfile.testzip` / firma `PK`).
- [x] `download_manager.py`: desacoplar de MEGA; job genérico basado en URL. Cola con
      límite de descargas simultáneas (configurable).
- [x] Eliminar `mega_downloader.py` y `pycryptodome` de dependencias (dejar compat: si un
      link es de MEGA, mostrar aviso "formato no soportado").
- [x] Textos de UI: "Descargar desde MEGA" → "Descargar".

## 2. Lanzar CTFs (multiplataforma)  `[x]`

Implementado en `lab_manager.py` (núcleo sin Qt, testeable), `lab_controller.py`
(workers Qt) y `widgets/lab_page.py` (página **Laboratorio**).

- [x] Detección de Docker (`docker version --format json`), estado del daemon, plataforma
      (Linux nativo / Docker Desktop Win-mac / WSL2) y estado del servicio systemd.
- [x] Extracción del zip a `~/.dockerlabs-gui/labs/<slug>/` (idempotente, con verificación y
      protección contra path traversal).
- [x] Lectura del `manifest.json` del tar para obtener `RepoTags` sin cargar la imagen, y del
      config para `ExposedPorts`.
- [x] `docker load -i` con progreso (stream de stdout) en un `QThread`, cancelable.
- [x] `docker run -d --name dockerlabs_<slug>` (con labels) y estrategia de red según plataforma:
      - Linux: bridge por defecto (IP interna accesible) — idéntico a `auto_deploy.sh`.
      - Windows/macOS: se publican los `ExposedPorts` en `127.0.0.1` (1:1 si está libre; si no,
        `20000+puerto`) y se muestra la tabla de mapeos.
      - Opción "modo host" (Linux) configurable en Ajustes.
- [x] IP del contenedor, puertos, estado y acciones **Iniciar / Detener / Reiniciar / Eliminar
      (contenedor + imagen) / Abrir shell (terminal del sistema) / Copiar IP**.
- [x] Página **Laboratorio** en el sidebar; lanzamiento desde el menú contextual de Máquinas.
- [x] Al cerrar la app: preguntar si se detienen los labs en ejecución.
- [x] Docker no instalado → mensaje con instrucciones de instalación por SO.
- [x] **Permisos en Linux**: se detecta si el usuario puede usar el socket sin `sudo`
      (`os.access` sobre `/var/run/docker.sock` / `DOCKER_HOST` / contexto, y `permission denied`
      en stderr). Si no puede y la app **no** se inició como root, aparece el botón
      **«Conceder acceso»**, que abre el **diálogo nativo del sistema** (`pkexec`/polkit; fallback
      `sudo -A` con askpass gráfico, `lxqt-sudo`, `kdesu`) y ejecuta: `usermod -aG docker`,
      arranque del servicio y `setfacl -m u:$USER:rw` sobre el socket para que funcione **sin
      cerrar sesión**. Si el servicio está parado se ofrece **«Iniciar servicio»** (elevado).
      Si la app corre como root no se pide nada.
- [ ] (Opcional) Verificación E2E con Docker real (`docker load` + `run`) — pendiente de entorno.

## 3. Mejoras de GUI (apariencia)  `[ ]`

- [ ] Panel de **detalle de máquina** (clic en fila): imagen `/img/maquina/<id>`, descripción,
      autor con avatar, rating (`get_machine_rating`), writeups (`/api/writeups/<n>`), botones
      Descargar / Lanzar / Marcar completada / Abrir en web.
- [ ] Tabla de máquinas: `QTableView` + `QAbstractTableModel` + `QSortFilterProxyModel`
      (rendimiento con 200+ filas, filtro instantáneo, orden estable).
- [ ] Badges de dificultad con color de fondo (como la web) en lugar de solo texto.
- [ ] Dashboard con progreso real (barra completadas/total, desglose por dificultad, últimas
      máquinas añadidas, ranking de creadores desde `ranking_creadores`).
- [ ] Página Descargas: scroll cuando hay muchas, botón "Limpiar terminadas".
- [ ] Página Completadas: buscador + agrupación por dificultad + botón desmarcar.
- [ ] Ajustes: descargas simultáneas, tema (oscuro/claro), estrategia de red Docker,
      auto-extraer tras descargar.
- [ ] Iconos nuevos: `play`, `stop`, `docker`, `terminal`, `star`, `external-link`, `flask`.
- [ ] Atajos de teclado: `Ctrl+F` buscar, `Ctrl+1..7` navegación, `F5` refrescar catálogo.
- [ ] Estado vacío/loading con skeleton o spinner en la tabla (no texto plano).

## 4. Estabilidad / calidad  `[ ]`

- [ ] `logging` a fichero rotativo `~/.dockerlabs-gui/logs/app.log` + `sys.excepthook` que
      muestra un diálogo en lugar de morir en silencio.
- [ ] Sustituir todos los `except Exception: pass` por logging con contexto.
- [ ] Refactor `MainWindow`: extraer `SessionController`, `CatalogController`.
- [ ] Catálogo directamente desde JSON cacheado (`catalog.json`) — el CSV pasa a ser export opcional.
- [ ] `completed_machines_from_home()` parseo robusto con `html.parser` (no regex).
- [ ] Eliminar monkey-patch de `mouseReleaseEvent` (usar `clicked`/`eventFilter`).
- [ ] Workers: clase base `BaseWorker(QThread)` con `finished` → auto-limpieza sin lambdas.
- [ ] Reintentos en catálogo (`/api`) con timeout corto y fallback a caché.
- [ ] Tests `pytest` (sin GUI): parser de API, planificador de puertos, extractor de manifest,
      downloader con servidor HTTP local, normalización de nombres. Tests de UI con
      `QT_QPA_PLATFORM=offscreen`.
- [ ] `pyproject.toml`: bump versión, dependencias, `ruff` config; `requirements.txt`.
- [ ] README actualizado (sin MEGA, con Docker, capturas, requisitos por SO).
- [ ] CI GitHub Actions: lint + tests en ubuntu/windows/macos.

## 5. Ideas futuras (no bloqueantes)  `[ ]`

- [ ] Writeups: abrir lista y enviar writeup (`/api/submit_writeup`) desde la app.
- [ ] Puntuar máquina (`/api/rate_machine`) tras marcarla como completada.
- [ ] Empaquetado: PyInstaller (Win/mac/Linux) + AppImage.
- [ ] Comprobación de nuevas versiones de la app desde GitHub Releases.
- [ ] Soporte Podman como alternativa a Docker.
- [ ] Modo "pentest": crear red dedicada `dockerlabs-net` y abrir terminal con la IP exportada.
