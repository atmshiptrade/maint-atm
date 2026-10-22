"""Aplica las migraciones pendientes de db/migrations y crea el administrador inicial.

Se ejecuta al arrancar el contenedor, antes de la aplicación:  python -m app.migrate
"""
import os
from pathlib import Path

import psycopg

from . import config
from .seguridad import hash_password

CARPETA = Path(__file__).resolve().parent.parent / "db" / "migrations"
CARPETA_DEMO = CARPETA.parent / "demo"


def aplicar_migraciones(conn: psycopg.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               version      text PRIMARY KEY,
               aplicada_en  timestamptz NOT NULL DEFAULT now())"""
    )
    aplicadas = {fila[0] for fila in conn.execute("SELECT version FROM schema_migrations")}
    for archivo in sorted(CARPETA.glob("*.sql")):
        if archivo.name in aplicadas:
            continue
        print(f"Aplicando migración {archivo.name}")
        with conn.transaction():
            conn.execute(archivo.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (archivo.name,))


def crear_admin_inicial(conn: psycopg.Connection) -> None:
    if conn.execute("SELECT EXISTS (SELECT 1 FROM usuarios)").fetchone()[0]:
        return
    usuario = os.environ.get("ADMIN_USUARIO", "").strip().lower()
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not usuario or len(password) < 8:
        print("AVISO: no hay usuarios. Define ADMIN_USUARIO y ADMIN_PASSWORD (8+ caracteres) en .env")
        return
    conn.execute(
        "INSERT INTO usuarios (nombre, usuario, password_hash, es_admin) VALUES (%s, %s, %s, true)",
        ("Administrador", usuario, hash_password(password)),
    )
    print(f"Administrador inicial creado: {usuario}")


def cargar_demo(conn: psycopg.Connection) -> None:
    """Solo para la demo (Render): usuarios de prueba y datos FICTICIOS, una sola vez."""
    if os.environ.get("CARGAR_DEMO", "").lower() != "true":
        return
    if conn.execute("SELECT EXISTS (SELECT 1 FROM lanchas)").fetchone()[0]:
        return
    password = os.environ.get("DEMO_PASSWORD", "")
    if len(password) < 8:
        print("AVISO: CARGAR_DEMO=true necesita DEMO_PASSWORD (8+ caracteres); no se cargó la demo")
        return
    with conn.transaction():
        ids = {}
        for usuario, nombre, rol in (("supervisor", "Supervisor Demo", "SUPERVISOR"),
                                     ("mecanico", "Mecánico Demo", "MECANICO")):
            fila = conn.execute(
                """INSERT INTO usuarios (nombre, usuario, password_hash) VALUES (%s, %s, %s)
                   ON CONFLICT (usuario) DO UPDATE SET nombre = EXCLUDED.nombre RETURNING id""",
                (nombre, usuario, hash_password(password)),
            ).fetchone()
            ids[rol] = fila[0]
            conn.execute(
                """INSERT INTO usuarios_roles (usuario_id, modulo, rol) VALUES (%s, 'MANTENIMIENTO', %s)
                   ON CONFLICT (usuario_id, modulo) DO NOTHING""",
                (fila[0], rol),
            )
        # Las bitácoras y reportes quedan a nombre del mecánico de la demo
        conn.execute("SELECT set_config('app.usuario_id', %s, true)", (str(ids["MECANICO"]),))
        conn.execute(CARPETA_DEMO.joinpath("datos_demo.sql").read_text(encoding="utf-8"))
    print("Datos de demo cargados (usuarios: supervisor, mecanico)")


def main() -> None:
    with psycopg.connect(config.DATABASE_URL, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(727274)")   # evita dos migraciones a la vez
        aplicar_migraciones(conn)
        crear_admin_inicial(conn)
        cargar_demo(conn)


if __name__ == "__main__":
    main()
