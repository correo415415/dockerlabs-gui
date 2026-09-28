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
| Ajustes | Carpeta de descargas, descargas simultáneas, notificaciones, red Docker |

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

Red según plataforma:
- **Linux (Docker Engine)**: `bridge`, la IP del contenedor es accesible directamente
  (igual que `auto_deploy.sh`). Opción "host" en Ajustes.
- **Windows / macOS (Docker Desktop)**: la IP interna no es alcanzable desde el host, así
  que los puertos `EXPOSE` se publican en `127.0.0.1` (1:1 si están libres; si no, `20000+puerto`).

### Permisos de Docker en Linux (sin sudo)
Al arrancar, la app comprueba si tu usuario puede usar el socket de Docker. Si no puede
y la app **no** se ejecuta como root, en la página *Laboratorio* aparece
**«Conceder acceso»**: se abre el **diálogo de autenticación del sistema** (`pkexec`/polkit;
si no existe, `sudo -A` con un askpass gráfico) y se ejecuta, como root:

- `usermod -aG docker <tu usuario>` (permanente tras reiniciar sesión),
- arranque/habilitación del servicio `docker`,
- `setfacl -m u:<tu usuario>:rw /var/run/docker.sock` para que funcione **ahora mismo**.

Si el servicio está parado se ofrece **«Iniciar servicio»**. Si prefieres hacerlo a mano:
`sudo usermod -aG docker $USER` y vuelve a iniciar sesión.

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
csv/               catálogo cacheado (funciona sin conexión)
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
main.py               ventana principal y cableado
http_downloader.py    descargador HTTP (sin Qt)
download_manager.py   cola de descargas (Qt)
lab_manager.py        zip → imagen → Docker CLI, red y permisos (sin Qt)
lab_controller.py     workers Qt del laboratorio
dockerlabs_api*.py    cliente de la API / web
app_logging.py        logging + excepthook
widgets/              páginas, sidebar, iconos SVG, toasts
tests/                pytest
TODO.md               análisis y hoja de ruta
```

## Créditos

DockerLabs es un proyecto de [El Pingüino de Mario](https://dockerlabs.es). Esta GUI es un
cliente no oficial.
