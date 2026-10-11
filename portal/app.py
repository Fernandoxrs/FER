"""Portal de actualizaciones con control de membresía.

El administrador sube archivos y crea cuentas con una membresía de N meses.
El personal entra con su usuario y descarga los archivos mientras su
membresía esté vigente.

Uso:
    python app.py crear-admin   # crea (o restablece) la cuenta de administrador
    python app.py               # inicia el servidor en http://0.0.0.0:8000
"""

import calendar
import re
import getpass
import os
import secrets
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("PORTAL_DATA_DIR", BASE_DIR / "datos"))
UPLOAD_DIR = DATA_DIR / "archivos"
DB_PATH = DATA_DIR / "portal.db"
SECRET_PATH = DATA_DIR / "secret.key"

# Tamaño máximo por subida (por defecto 4 GB). Ajustable con PORTAL_MAX_MB.
MAX_UPLOAD_MB = int(os.environ.get("PORTAL_MAX_MB", "4096"))

# Bloqueo de inicio de sesión: 5 intentos fallidos -> 5 minutos de espera.
MAX_LOGIN_FAILS = 5
LOCKOUT_SECONDS = 300

PLANES_MESES = [1, 2, 3, 6, 12]

# Registro público: máximo de cuentas nuevas por IP y por hora.
MAX_REGISTROS_POR_HORA = 5
USUARIO_VALIDO = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
CORREO_VALIDO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_registros_por_ip = {}

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _load_secret_key():
    env_key = os.environ.get("PORTAL_SECRET_KEY")
    if env_key:
        return env_key
    if SECRET_PATH.exists():
        return SECRET_PATH.read_text().strip()
    key = secrets.token_hex(32)
    SECRET_PATH.write_text(key)
    return key


app = Flask(__name__)
app.config.update(
    SECRET_KEY=_load_secret_key(),
    MAX_CONTENT_LENGTH=MAX_UPLOAD_MB * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("PORTAL_HTTPS") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)

# Detrás de Caddy, Nginx o Cloudflare Tunnel: confiar en sus cabeceras X-Forwarded-*.
if os.environ.get("PORTAL_PROXY") == "1":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


# --------------------------------------------------------------------------
# Base de datos
# --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario       TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    nombre        TEXT    NOT NULL DEFAULT '',
    clave_hash    TEXT    NOT NULL,
    es_admin      INTEGER NOT NULL DEFAULT 0,
    activo        INTEGER NOT NULL DEFAULT 1,
    vence         TEXT,
    creado        TEXT    NOT NULL,
    fallos        INTEGER NOT NULL DEFAULT 0,
    bloqueado_hasta REAL  NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS archivos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre_disco  TEXT    NOT NULL UNIQUE,
    nombre        TEXT    NOT NULL,
    descripcion   TEXT    NOT NULL DEFAULT '',
    version       TEXT    NOT NULL DEFAULT '',
    tamano        INTEGER NOT NULL,
    subido        TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS ajustes (
    clave         TEXT PRIMARY KEY,
    valor         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS descargas (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id    INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    archivo_id    INTEGER NOT NULL REFERENCES archivos(id) ON DELETE CASCADE,
    fecha         TEXT    NOT NULL
);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.executescript(SCHEMA)
        columnas = {fila[1] for fila in db.execute("PRAGMA table_info(usuarios)")}
        if "correo" not in columnas:
            db.execute("ALTER TABLE usuarios ADD COLUMN correo TEXT NOT NULL DEFAULT ''")


def leer_ajuste(clave, defecto=""):
    fila = get_db().execute("SELECT valor FROM ajustes WHERE clave = ?", (clave,)).fetchone()
    return fila["valor"] if fila else defecto


def guardar_ajuste(clave, valor):
    db = get_db()
    db.execute(
        "INSERT INTO ajustes (clave, valor) VALUES (?, ?) "
        "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
        (clave, valor),
    )
    db.commit()


def registro_abierto():
    return leer_ajuste("registro_abierto", "0") == "1"


def ahora():
    return datetime.now().replace(microsecond=0)


def sumar_meses(fecha, meses):
    """Suma meses de calendario (31-ene + 1 mes = 28/29-feb)."""
    mes = fecha.month - 1 + meses
    anio = fecha.year + mes // 12
    mes = mes % 12 + 1
    dia = min(fecha.day, calendar.monthrange(anio, mes)[1])
    return fecha.replace(year=anio, month=mes, day=dia)


def parse_fecha(texto):
    return datetime.fromisoformat(texto) if texto else None


def membresia_vigente(usuario):
    if usuario["es_admin"]:
        return True
    if not usuario["activo"]:
        return False
    vence = parse_fecha(usuario["vence"])
    return vence is not None and vence > ahora()


# --------------------------------------------------------------------------
# Sesión, CSRF y permisos
# --------------------------------------------------------------------------

def usuario_actual():
    uid = session.get("uid")
    if uid is None:
        return None
    if "usuario" not in g:
        g.usuario = get_db().execute(
            "SELECT * FROM usuarios WHERE id = ?", (uid,)
        ).fetchone()
    return g.usuario


def login_requerido(vista):
    @wraps(vista)
    def envoltura(*args, **kwargs):
        u = usuario_actual()
        if u is None:
            session.clear()
            return redirect(url_for("login"))
        if not u["activo"] and not u["es_admin"]:
            session.clear()
            flash("Tu cuenta está suspendida. Contacta al administrador.", "error")
            return redirect(url_for("login"))
        return vista(*args, **kwargs)

    return envoltura


def admin_requerido(vista):
    @wraps(vista)
    @login_requerido
    def envoltura(*args, **kwargs):
        if not usuario_actual()["es_admin"]:
            abort(403)
        return vista(*args, **kwargs)

    return envoltura


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


@app.before_request
def verificar_csrf():
    if request.method == "POST":
        enviado = request.form.get("csrf", "")
        if not enviado or not secrets.compare_digest(enviado, session.get("csrf", "")):
            abort(400, "Token de seguridad inválido. Recarga la página.")


@app.context_processor
def contexto_global():
    return {
        "csrf_token": csrf_token,
        "usuario": usuario_actual(),
        "ahora": ahora(),
        "parse_fecha": parse_fecha,
        "membresia_vigente": membresia_vigente,
        "tamano_legible": tamano_legible,
        "planes": PLANES_MESES,
        "registro_abierto": registro_abierto,
    }


def tamano_legible(n):
    for unidad in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unidad}" if unidad == "B" else f"{n:.1f} {unidad}"
        n /= 1024
    return f"{n:.1f} TB"


# --------------------------------------------------------------------------
# Autenticación
# --------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        nombre = request.form.get("usuario", "").strip()
        clave = request.form.get("clave", "")
        db = get_db()
        u = db.execute("SELECT * FROM usuarios WHERE usuario = ?", (nombre,)).fetchone()

        if u and u["bloqueado_hasta"] > time.time():
            minutos = int((u["bloqueado_hasta"] - time.time()) // 60) + 1
            flash(f"Demasiados intentos. Prueba de nuevo en {minutos} min.", "error")
            return render_template("login.html"), 429

        if u and check_password_hash(u["clave_hash"], clave):
            db.execute(
                "UPDATE usuarios SET fallos = 0, bloqueado_hasta = 0 WHERE id = ?",
                (u["id"],),
            )
            db.commit()
            if not u["activo"] and not u["es_admin"]:
                flash("Tu cuenta está suspendida. Contacta al administrador.", "error")
                return render_template("login.html"), 403
            session.clear()
            session.permanent = True
            session["uid"] = u["id"]
            return redirect(url_for("admin" if u["es_admin"] else "inicio"))

        if u:
            fallos = u["fallos"] + 1
            bloqueo = time.time() + LOCKOUT_SECONDS if fallos >= MAX_LOGIN_FAILS else 0
            db.execute(
                "UPDATE usuarios SET fallos = ?, bloqueado_hasta = ? WHERE id = ?",
                (0 if bloqueo else fallos, bloqueo, u["id"]),
            )
            db.commit()
        flash("Usuario o contraseña incorrectos.", "error")
        return render_template("login.html"), 401

    if usuario_actual():
        return redirect(url_for("inicio"))
    return render_template("login.html")


def _limite_registro_superado(ip):
    limite = time.time() - 3600
    recientes = [t for t in _registros_por_ip.get(ip, []) if t > limite]
    _registros_por_ip[ip] = recientes
    return len(recientes) >= MAX_REGISTROS_POR_HORA


@app.route("/registro", methods=["GET", "POST"])
def registro():
    if not registro_abierto():
        flash("El registro está cerrado. Pide una cuenta al administrador.", "error")
        return redirect(url_for("login"))
    if usuario_actual():
        return redirect(url_for("inicio"))
    if request.method == "GET":
        return render_template("registro.html", datos={})

    datos = {
        "usuario": request.form.get("usuario", "").strip(),
        "nombre": request.form.get("nombre", "").strip()[:80],
        "correo": request.form.get("correo", "").strip()[:120],
    }
    clave = request.form.get("clave", "")
    ip = request.remote_addr or "?"

    # Campo trampa invisible: solo lo rellenan los bots.
    if request.form.get("sitio_web"):
        abort(400)
    if _limite_registro_superado(ip):
        flash("Demasiados registros desde tu conexión. Intenta en una hora.", "error")
        return render_template("registro.html", datos=datos), 429

    error = None
    if not USUARIO_VALIDO.match(datos["usuario"]):
        error = "El usuario debe tener de 3 a 32 caracteres: letras, números, punto, guion o guion bajo."
    elif not CORREO_VALIDO.match(datos["correo"]):
        error = "Escribe un correo válido."
    elif len(clave) < 8:
        error = "La contraseña debe tener al menos 8 caracteres."
    elif clave != request.form.get("confirmar", ""):
        error = "Las contraseñas no coinciden."
    if error:
        flash(error, "error")
        return render_template("registro.html", datos=datos), 400

    db = get_db()
    try:
        cur = db.execute(
            """INSERT INTO usuarios (usuario, nombre, correo, clave_hash, vence, creado)
               VALUES (?, ?, ?, ?, NULL, ?)""",
            (datos["usuario"], datos["nombre"], datos["correo"],
             generate_password_hash(clave), ahora().isoformat()),
        )
        db.commit()
    except sqlite3.IntegrityError:
        flash("Ese usuario ya existe. Elige otro.", "error")
        return render_template("registro.html", datos=datos), 409

    _registros_por_ip.setdefault(ip, []).append(time.time())
    session.clear()
    session.permanent = True
    session["uid"] = cur.lastrowid
    flash("Cuenta creada. El administrador debe activar tu membresía para que puedas descargar.", "ok")
    return redirect(url_for("inicio"))


@app.route("/salir", methods=["POST"])
def salir():
    session.clear()
    return redirect(url_for("login"))


@app.route("/cambiar-clave", methods=["GET", "POST"])
@login_requerido
def cambiar_clave():
    if request.method == "POST":
        u = usuario_actual()
        actual = request.form.get("actual", "")
        nueva = request.form.get("nueva", "")
        if not check_password_hash(u["clave_hash"], actual):
            flash("La contraseña actual no es correcta.", "error")
        elif len(nueva) < 8:
            flash("La nueva contraseña debe tener al menos 8 caracteres.", "error")
        elif nueva != request.form.get("confirmar", ""):
            flash("Las contraseñas no coinciden.", "error")
        else:
            db = get_db()
            db.execute(
                "UPDATE usuarios SET clave_hash = ? WHERE id = ?",
                (generate_password_hash(nueva), u["id"]),
            )
            db.commit()
            flash("Contraseña actualizada.", "ok")
            return redirect(url_for("inicio"))
    return render_template("cambiar_clave.html")


# --------------------------------------------------------------------------
# Personal: lista y descarga de archivos
# --------------------------------------------------------------------------

@app.route("/")
@login_requerido
def inicio():
    u = usuario_actual()
    archivos = get_db().execute("SELECT * FROM archivos ORDER BY subido DESC").fetchall()
    return render_template("inicio.html", archivos=archivos, vigente=membresia_vigente(u))


@app.route("/descargar/<int:archivo_id>")
@login_requerido
def descargar(archivo_id):
    u = usuario_actual()
    if not membresia_vigente(u):
        flash("Tu membresía venció. Contacta al administrador para renovarla.", "error")
        return redirect(url_for("inicio"))
    db = get_db()
    a = db.execute("SELECT * FROM archivos WHERE id = ?", (archivo_id,)).fetchone()
    if a is None:
        abort(404)
    db.execute(
        "INSERT INTO descargas (usuario_id, archivo_id, fecha) VALUES (?, ?, ?)",
        (u["id"], a["id"], ahora().isoformat()),
    )
    db.commit()
    return send_from_directory(
        UPLOAD_DIR, a["nombre_disco"], as_attachment=True, download_name=a["nombre"]
    )


# --------------------------------------------------------------------------
# Administración
# --------------------------------------------------------------------------

@app.route("/admin")
@admin_requerido
def admin():
    db = get_db()
    usuarios = db.execute(
        "SELECT * FROM usuarios WHERE es_admin = 0 AND vence IS NOT NULL ORDER BY usuario"
    ).fetchall()
    pendientes = db.execute(
        "SELECT * FROM usuarios WHERE es_admin = 0 AND vence IS NULL ORDER BY creado"
    ).fetchall()
    archivos = db.execute(
        """SELECT a.*, COUNT(d.id) AS descargas
           FROM archivos a LEFT JOIN descargas d ON d.archivo_id = a.id
           GROUP BY a.id ORDER BY a.subido DESC"""
    ).fetchall()
    recientes = db.execute(
        """SELECT d.fecha, u.usuario, a.nombre
           FROM descargas d
           JOIN usuarios u ON u.id = d.usuario_id
           JOIN archivos a ON a.id = d.archivo_id
           ORDER BY d.id DESC LIMIT 30"""
    ).fetchall()
    return render_template(
        "admin.html", usuarios=usuarios, pendientes=pendientes,
        archivos=archivos, recientes=recientes,
    )


@app.route("/admin/registro", methods=["POST"])
@admin_requerido
def admin_cambiar_registro():
    abrir = not registro_abierto()
    guardar_ajuste("registro_abierto", "1" if abrir else "0")
    flash("Registro público " + ("abierto." if abrir else "cerrado."), "ok")
    return redirect(url_for("admin"))


def _meses_del_form():
    try:
        meses = int(request.form.get("meses", "0"))
    except ValueError:
        meses = 0
    if not 1 <= meses <= 120:
        abort(400, "Cantidad de meses inválida (1 a 120).")
    return meses


@app.route("/admin/usuarios/nuevo", methods=["POST"])
@admin_requerido
def admin_crear_usuario():
    nombre_usuario = request.form.get("usuario", "").strip()
    nombre = request.form.get("nombre", "").strip()
    clave = request.form.get("clave", "")
    meses = _meses_del_form()
    if not nombre_usuario or len(clave) < 8:
        flash("Indica un usuario y una contraseña de al menos 8 caracteres.", "error")
        return redirect(url_for("admin"))
    vence = sumar_meses(ahora(), meses)
    try:
        db = get_db()
        db.execute(
            """INSERT INTO usuarios (usuario, nombre, clave_hash, vence, creado)
               VALUES (?, ?, ?, ?, ?)""",
            (nombre_usuario, nombre, generate_password_hash(clave),
             vence.isoformat(), ahora().isoformat()),
        )
        db.commit()
    except sqlite3.IntegrityError:
        flash(f"El usuario «{nombre_usuario}» ya existe.", "error")
        return redirect(url_for("admin"))
    flash(f"Usuario «{nombre_usuario}» creado. Vence el {vence:%d/%m/%Y}.", "ok")
    return redirect(url_for("admin"))


def _usuario_o_404(uid):
    u = get_db().execute(
        "SELECT * FROM usuarios WHERE id = ? AND es_admin = 0", (uid,)
    ).fetchone()
    if u is None:
        abort(404)
    return u


@app.route("/admin/usuarios/<int:uid>/extender", methods=["POST"])
@admin_requerido
def admin_extender(uid):
    u = _usuario_o_404(uid)
    meses = _meses_del_form()
    # Si aún está vigente se suma al vencimiento actual; si ya venció, desde hoy.
    base = max(parse_fecha(u["vence"]) or ahora(), ahora())
    vence = sumar_meses(base, meses)
    db = get_db()
    db.execute("UPDATE usuarios SET vence = ?, activo = 1 WHERE id = ?",
               (vence.isoformat(), uid))
    db.commit()
    flash(f"Membresía de «{u['usuario']}» extendida hasta el {vence:%d/%m/%Y}.", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/usuarios/<int:uid>/vencimiento", methods=["POST"])
@admin_requerido
def admin_fijar_vencimiento(uid):
    u = _usuario_o_404(uid)
    try:
        fecha = datetime.strptime(request.form.get("fecha", ""), "%Y-%m-%d")
    except ValueError:
        abort(400, "Fecha inválida.")
    vence = fecha.replace(hour=23, minute=59, second=59)
    db = get_db()
    db.execute("UPDATE usuarios SET vence = ? WHERE id = ?", (vence.isoformat(), uid))
    db.commit()
    flash(f"Vencimiento de «{u['usuario']}» fijado al {vence:%d/%m/%Y}.", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/usuarios/<int:uid>/estado", methods=["POST"])
@admin_requerido
def admin_cambiar_estado(uid):
    u = _usuario_o_404(uid)
    nuevo = 0 if u["activo"] else 1
    db = get_db()
    db.execute("UPDATE usuarios SET activo = ? WHERE id = ?", (nuevo, uid))
    db.commit()
    flash(f"«{u['usuario']}» {'activado' if nuevo else 'suspendido'}.", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/usuarios/<int:uid>/clave", methods=["POST"])
@admin_requerido
def admin_restablecer_clave(uid):
    u = _usuario_o_404(uid)
    clave = request.form.get("clave", "")
    if len(clave) < 8:
        flash("La contraseña debe tener al menos 8 caracteres.", "error")
        return redirect(url_for("admin"))
    db = get_db()
    db.execute(
        "UPDATE usuarios SET clave_hash = ?, fallos = 0, bloqueado_hasta = 0 WHERE id = ?",
        (generate_password_hash(clave), uid),
    )
    db.commit()
    flash(f"Contraseña de «{u['usuario']}» restablecida.", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/usuarios/<int:uid>/eliminar", methods=["POST"])
@admin_requerido
def admin_eliminar_usuario(uid):
    u = _usuario_o_404(uid)
    db = get_db()
    db.execute("DELETE FROM usuarios WHERE id = ?", (uid,))
    db.commit()
    flash(f"Usuario «{u['usuario']}» eliminado.", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/archivos/subir", methods=["POST"])
@admin_requerido
def admin_subir():
    subidos = [f for f in request.files.getlist("archivos") if f and f.filename]
    if not subidos:
        flash("Selecciona al menos un archivo.", "error")
        return redirect(url_for("admin"))
    descripcion = request.form.get("descripcion", "").strip()
    version = request.form.get("version", "").strip()
    db = get_db()
    for f in subidos:
        original = secure_filename(f.filename) or "archivo"
        en_disco = f"{uuid.uuid4().hex}_{original}"
        destino = UPLOAD_DIR / en_disco
        f.save(destino)
        db.execute(
            """INSERT INTO archivos (nombre_disco, nombre, descripcion, version, tamano, subido)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (en_disco, original, descripcion, version,
             destino.stat().st_size, ahora().isoformat()),
        )
    db.commit()
    flash(f"{len(subidos)} archivo(s) subido(s).", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/archivos/<int:archivo_id>/eliminar", methods=["POST"])
@admin_requerido
def admin_eliminar_archivo(archivo_id):
    db = get_db()
    a = db.execute("SELECT * FROM archivos WHERE id = ?", (archivo_id,)).fetchone()
    if a is None:
        abort(404)
    (UPLOAD_DIR / a["nombre_disco"]).unlink(missing_ok=True)
    db.execute("DELETE FROM archivos WHERE id = ?", (archivo_id,))
    db.commit()
    flash(f"Archivo «{a['nombre']}» eliminado.", "ok")
    return redirect(url_for("admin"))


@app.errorhandler(413)
def demasiado_grande(_e):
    flash(f"El archivo supera el límite de {MAX_UPLOAD_MB} MB.", "error")
    return redirect(url_for("admin"))


# --------------------------------------------------------------------------
# Línea de comandos
# --------------------------------------------------------------------------

def crear_admin():
    init_db()
    nombre = input("Usuario administrador: ").strip()
    if not nombre:
        sys.exit("El usuario no puede estar vacío.")
    clave = getpass.getpass("Contraseña (mín. 8 caracteres): ")
    if len(clave) < 8:
        sys.exit("La contraseña debe tener al menos 8 caracteres.")
    if clave != getpass.getpass("Repite la contraseña: "):
        sys.exit("Las contraseñas no coinciden.")
    with sqlite3.connect(DB_PATH) as db:
        existe = db.execute("SELECT id FROM usuarios WHERE usuario = ?", (nombre,)).fetchone()
        if existe:
            db.execute(
                "UPDATE usuarios SET clave_hash = ?, es_admin = 1, activo = 1, "
                "fallos = 0, bloqueado_hasta = 0 WHERE id = ?",
                (generate_password_hash(clave), existe[0]),
            )
            print(f"Administrador «{nombre}» actualizado.")
        else:
            db.execute(
                "INSERT INTO usuarios (usuario, nombre, clave_hash, es_admin, creado) "
                "VALUES (?, ?, ?, 1, ?)",
                (nombre, "Administrador", generate_password_hash(clave), ahora().isoformat()),
            )
            print(f"Administrador «{nombre}» creado.")


def servir():
    init_db()
    host = os.environ.get("PORTAL_HOST", "0.0.0.0")
    port = int(os.environ.get("PORTAL_PORT", "8000"))
    print(f"Portal en http://{host}:{port}  (Ctrl+C para detener)")
    try:
        from waitress import serve
    except ImportError:
        app.run(host=host, port=port)
    else:
        serve(app, host=host, port=port, threads=8)


init_db()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "crear-admin":
        crear_admin()
    else:
        servir()
