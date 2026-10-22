"""Restablece la contraseña de un usuario desde la terminal del servidor.

Sirve cuando se olvidó la contraseña del administrador y nadie puede entrar:

    docker compose exec app python -m app.restablecer_password admin
"""
import getpass
import sys

import psycopg

from . import config
from .seguridad import hash_password


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Uso: python -m app.restablecer_password <usuario>")
    usuario = sys.argv[1].strip().lower()

    nueva = getpass.getpass("Nueva contraseña (mínimo 8 caracteres): ")
    if len(nueva) < 8:
        sys.exit("La contraseña debe tener al menos 8 caracteres.")
    if nueva != getpass.getpass("Repítela: "):
        sys.exit("Las contraseñas no coinciden.")

    with psycopg.connect(config.DATABASE_URL) as conn:
        fila = conn.execute(
            "UPDATE usuarios SET password_hash = %s WHERE usuario = %s RETURNING id",
            (hash_password(nueva), usuario),
        ).fetchone()
    if not fila:
        sys.exit(f"No existe el usuario «{usuario}».")
    print(f"Contraseña de «{usuario}» actualizada.")


if __name__ == "__main__":
    main()
