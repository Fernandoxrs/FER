# Portal de actualizaciones

Portal web para subir archivos de actualización y que tu personal los descargue
mientras su membresía esté vigente.

## Qué hace

- **Administrador:** sube uno o varios archivos con versión y descripción, crea
  usuarios con membresía de 1, 2, 3, 6 o 12 meses, extiende membresías, fija una
  fecha de vencimiento exacta, suspende o activa cuentas, restablece contraseñas
  y ve quién descargó qué.
- **Personal:** entra con su usuario, ve los archivos y cuántos días le quedan.
  Si la membresía venció o la cuenta está suspendida, no puede descargar.

Extender suma los meses al vencimiento actual si todavía está vigente, o a partir
de hoy si ya venció.

## Iniciar en Windows

1. Instala [Python 3](https://www.python.org/downloads/) y marca *Add python.exe to PATH*.
2. Haz doble clic en `iniciar.bat`. La primera vez instala lo necesario y te pide
   crear el usuario administrador.
3. Abre `http://localhost:8000` en el navegador.

Tu personal entra con `http://IP-DE-TU-PC:8000` si está en tu misma red.
**Para personal en otros países**, sigue [DESPLIEGUE.md](DESPLIEGUE.md): servidor
VPS con HTTPS automático (recomendado) o tu PC con Cloudflare Tunnel.

## Iniciar en Linux / Mac

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py crear-admin
.venv/bin/python app.py
```

Para restablecer la contraseña del administrador, ejecuta otra vez `crear-admin`
con el mismo usuario.

## Configuración (variables de entorno)

| Variable | Por defecto | Para qué |
|---|---|---|
| `PORTAL_PORT` | `8000` | Puerto |
| `PORTAL_HOST` | `0.0.0.0` | Interfaz donde escucha |
| `PORTAL_MAX_MB` | `4096` | Tamaño máximo por subida |
| `PORTAL_DATA_DIR` | `./datos` | Dónde se guardan la base de datos y los archivos |
| `PORTAL_HTTPS` | — | Ponlo en `1` si lo sirves detrás de HTTPS |
| `PORTAL_PROXY` | — | Ponlo en `1` detrás de Caddy, Nginx o Cloudflare Tunnel |

## Datos y copias de seguridad

Todo queda en la carpeta `datos/`: `portal.db` (usuarios, archivos, descargas),
`archivos/` (lo que subes) y `secret.key` (firma de sesiones). Copia esa carpeta
para tener un respaldo. No la subas a GitHub; ya está en `.gitignore`.

## Seguridad incluida

Contraseñas cifradas, protección CSRF en todos los formularios, bloqueo de 5 minutos
tras 5 intentos fallidos y archivos guardados con nombres únicos fuera de la web
pública. Las descargas solo pasan por el control de membresía.
