<p align="center"><img src="assets/logo_256.png" width="128" alt="DockerLabs GUI"></p>

# DockerLabs GUI

Cliente de escritorio (PyQt6) para [DockerLabs](https://dockerlabs.es): explora el
catálogo de máquinas, descárgalas, **despliégalas con Docker desde la propia app** y
sincroniza tus máquinas completadas con tu cuenta.

Funciona en **Linux, Windows y macOS**.

## Qué hace

| Apartado | Para qué sirve |
|---|---|
| Dashboard | Resumen: total de máquinas, completadas, descargas, laboratorios en marcha, estado de Docker |
| Máquinas | Catálogo público con búsqueda y filtros. Clic derecho: descargar, lanzar laboratorio, marcar completada |
| Descargas | Cola de descargas HTTP con progreso, velocidad, ETA y cancelación (N simultáneas) |
| Laboratorio | Máquinas desplegadas con Docker: IP, puertos, iniciar/detener/reiniciar/eliminar, abrir shell |
| Completadas | Lista sincronizada con tu cuenta de dockerlabs.es |
| Sesión | Login contra `/api/auth/login` (CSRF) con sesión persistente |
| Ajustes | Carpeta de descargas, descargas simultáneas, notificaciones, red Docker, tema oscuro/claro |

### Descargas
Las máquinas se descargan directamente desde
`https://gestion-maquinas.dockerlabs.es/dl/<máquina>.zip` (ya **no** se usa MEGA).
El descargador reintenta ante errores temporales del servidor (5xx), escribe en
`.part` y verifica el zip antes de darlo por bueno.

### Lanzar un CTF
Cada zip contiene una imagen Docker (`docker save`) y el `auto_deploy.sh` oficial.
La app reproduce ese script sin depender de bash:

1. Extrae el zip a `~/.dockerlabs-gui/labs/<máquina>/`.
2. Lee `manifest.json` (tag) y `ExposedPorts` de la imagen.
3. `docker load` (solo la primera vez) y `docker run -d --name dockerlabs_<máquina>`.
4. Muestra la IP del contenedor y los puertos.

**Labs de varias máquinas (pivoting)**: si el zip trae varios `.tar` (p. ej. *Grandma*),
la app reproduce lo que hace el `auto_deploy.sh` oficial: crea las redes `pivotingN`
(la primera `bridge`, el resto `macvlan --internal`), levanta cada contenedor en su red y
lo conecta a la siguiente. A diferencia del script oficial (que al salir **borra todos los
contenedores del sistema**), aquí redes y contenedores llevan el prefijo `dockerlabs_<máquina>`
y *Eliminar* sólo toca los de ese lab.

La extracción del zip ocurre únicamente al lanzar la máquina, nunca al descargar.

Red según plataforma:
- **Linux (Docker Engine)**: `bridge`, la IP del contenedor es accesible directamente
  (igual que `auto_deploy.sh`). Opción "host" en Ajustes.
- **Windows / macOS (Docker Desktop)**: la IP interna no es alcanzable desde el host, así
  que los puertos `EXPOSE` se publican en `127.0.0.1` (1:1 si están libres; si no, `20000+puerto`).

### Permisos de Docker en Linux (sin sudo)
Al arrancar, la app comprueba si tu usuario puede usar el socket de Docker. Si no puede
y la app **no** se ejecuta como root, en la página *Laboratorio* aparece
**«Permitir acceso a Docker»** con dos opciones (solo esta sesión / grupo docker), ambas con el
diálogo de autenticación del sistema — ver [Permisos de Docker en Linux](#permisos-de-docker-en-linux).
Si el servicio está parado se ofrece **«Iniciar servicio»**.

> **Velocidad de descarga.** El servidor oficial (`gestion-maquinas.dockerlabs.es`) limita
> cada conexión a ~0,5 MB/s y no soporta `Range`, por lo que no es posible acelerar una
> descarga partiéndola en trozos ni reanudarla. La app descarga en streaming sin
> ningún límite propio; para aprovechar mejor el ancho de banda, sube el número de
> «Descargas simultáneas» en Ajustes (cada conexión recibe su propio ~0,5 MB/s).

## Requisitos

- Python **3.9+**
- `PyQt6`, `requests` (ver `requirements.txt`)
- **Docker** (solo para lanzar laboratorios):
  - Linux: Docker Engine (`sudo apt install docker.io` en Debian/Ubuntu/Kali) o Docker Desktop.
  - Windows: Docker Desktop (WSL2).
  - macOS: Docker Desktop.

## Instalación y ejecución

```bash
git clone https://github.com/correo415415/dockerlabs-gui.git
cd dockerlabs-gui
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

O como paquete: `pip install .` y luego `dockerlabs-gui`.

## Datos locales

Todo vive en `~/.dockerlabs-gui/`:

```
settings.json      ajustes
.env               sesión persistente
completed.json     completadas (caché local)
catalog.json       catálogo cacheado (funciona sin conexión)
cache/             imágenes y valoraciones de máquinas (caché en disco, TTL 6 h)
downloads/         zips descargados
labs/<máquina>/    imágenes extraídas
logs/app.log       log rotativo (DOCKERLABS_DEBUG=1 para más detalle)
```

Los errores inesperados se registran en el log y se muestran en un diálogo con el
traceback (no cierran la app).

## Desarrollo

```bash
pip install pytest
pytest tests -q            # tests sin red ni Docker (servidor HTTP local, runner falso)
QT_QPA_PLATFORM=offscreen pytest tests -q   # en CI / sin pantalla
```

Estructura:

```
main.py               ventana principal: sólo cableado de señales ↔ páginas
session_controller.py login / sesión persistente / completadas / sync (sin UI)
catalog_controller.py caché del catálogo + refresco en segundo plano (sin UI)
catalog.py            modelo del catálogo (/api → Machine, Writeup, Catalog)
workers.py            BaseWorker (QThread con errores uniformes) y WorkerPool
http_downloader.py    descargador HTTP (sin Qt)
download_manager.py   cola de descargas (Qt)
lab_manager.py        zip → imagen(es) → Docker CLI, redes pivoting, permisos (sin Qt)
lab_controller.py     workers Qt del laboratorio
dockerlabs_api*.py    cliente de la API / web (parser html.parser de completadas)
theme.py              paletas dark/light y QSS (`apply_theme`)
app_logging.py        logging + excepthook
widgets/              páginas, sidebar, iconos SVG, toasts, skeleton de carga
tests/                pytest
TODO.md               análisis y hoja de ruta
```

## Créditos

DockerLabs es un proyecto de [El Pingüino de Mario](https://dockerlabs.es). Esta GUI es un
cliente no oficial.

### Permisos de Docker en Linux

Si tu usuario no puede usar el socket de Docker, la app ofrece dos opciones al pulsar
**«Permitir acceso a Docker»** (o al lanzar una máquina). En ambas la contraseña se pide con el
**diálogo de autenticación del sistema** (`pkexec`/polkit, o `sudo -A` con askpass gráfico): la app
nunca ve tu contraseña.

1. **Solo esta sesión (recomendado)** — se aplica una ACL (`setfacl`) sobre el socket de Docker para
   tu usuario. No se modifica ningún grupo ni la configuración; el permiso desaparece al reiniciar
   el servicio o el equipo. No hace falta cerrar sesión.
2. **Añadir mi usuario al grupo docker** — permanente. Se ejecuta `usermod -aG docker` más la misma
   ACL sobre el socket para que funcione sin cerrar sesión. Ten en cuenta que pertenecer al grupo
   `docker` equivale a tener root sin contraseña.

Si no hay `pkexec` ni askpass gráfico pero sí `sudo`, se ofrece un *fallback*: introducir la
contraseña en la app (se comprueba con `sudo -v`, `docker` se ejecuta con `sudo -S` y la contraseña
solo vive en memoria; «Dejar de usar sudo» la olvida).
