# Publicar el portal en internet

Para que tu personal entre desde cualquier país necesitas una dirección pública
con HTTPS. Hay dos caminos.

| | A. Servidor (VPS) — recomendado | B. Tu PC + Cloudflare Tunnel |
|---|---|---|
| Costo | ~5 USD/mes + dominio (~10 USD/año) | Gratis (solo el dominio) |
| Disponible | 24/7 | Solo mientras tu PC esté prendida |
| Subidas grandes | Sin límite | Máx. 100 MB por archivo (plan gratis de Cloudflare) |
| Velocidad de descarga | La del servidor | Depende de tu subida de internet |

---

## A. Servidor VPS con HTTPS automático

Sirve cualquier VPS Linux (Hetzner, DigitalOcean, Vultr, Contabo…). Elige una
región cerca de la mayoría de tu personal.

1. **Compra un dominio** y crea un registro **A** (por ejemplo
   `descargas.tudominio.com`) que apunte a la IP del servidor.
2. **Instala Docker** en el servidor:
   ```bash
   curl -fsSL https://get.docker.com | sh
   ```
3. **Copia la carpeta `portal/`** al servidor (con `git clone` o `scp`) y entra en ella.
4. **Configura el dominio y la zona horaria:**
   ```bash
   cp .env.ejemplo .env
   nano .env        # cambia DOMINIO y ZONA_HORARIA
   ```
5. **Crea el administrador** y arranca:
   ```bash
   docker compose run --rm portal python app.py crear-admin
   docker compose up -d --build
   ```
6. Abre `https://descargas.tudominio.com`. El certificado HTTPS se genera solo
   la primera vez (puede tardar un minuto).

Comandos útiles:

```bash
docker compose logs -f portal     # ver registros
docker compose restart            # reiniciar
docker compose up -d --build      # aplicar una versión nueva del código
tar czf respaldo.tgz datos/       # copia de seguridad
```

El firewall del servidor debe permitir los puertos **80** y **443**. No abras el 8000.

---

## B. Desde tu PC con Cloudflare Tunnel

No abre puertos en tu router y da HTTPS gratis. Necesitas un dominio gestionado
en Cloudflare (cuenta gratuita).

1. Inicia el portal en tu PC con `iniciar.bat` (queda en `http://localhost:8000`).
   Antes, define estas variables para que funcione bien detrás del túnel: en
   `iniciar.bat`, justo después de `cd /d "%~dp0"`, agrega:
   ```bat
   set PORTAL_PROXY=1
   set PORTAL_HTTPS=1
   set PORTAL_HOST=127.0.0.1
   ```
2. En el panel de Cloudflare: **Zero Trust → Networks → Tunnels → Create a tunnel**
   (tipo *Cloudflared*). Sigue las instrucciones para instalar `cloudflared` en Windows.
3. En **Public Hostname**, agrega `descargas.tudominio.com` → servicio
   `HTTP` → `localhost:8000`.
4. Tu personal entra con `https://descargas.tudominio.com`.

Límite: con el plan gratis, cada archivo que **subas** puede pesar como máximo
100 MB. Si tus actualizaciones son más grandes, divídelas (por ejemplo en un
`.zip` por partes) o usa la opción A.

---

## Zona horaria

Los vencimientos se calculan con la hora del servidor. Con la opción A se usa
`ZONA_HORARIA` del archivo `.env`; con la opción B, la hora de tu PC. Una
membresía vence a la misma hora para todos, sin importar el país de cada usuario.
