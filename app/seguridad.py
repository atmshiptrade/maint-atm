"""Contraseñas, sesión, roles y protección CSRF."""
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request

from .db import get_db

# Un rol por módulo; los códigos de rol son únicos en todo el sistema (ver migración 010).
# El administrador (usuarios.es_admin) puede todo.
MODULOS = {
    "MANTENIMIENTO": ("Mantenimiento", {"MECANICO": "Mecánico", "SUPERVISOR": "Supervisor de Mantenimiento"}),
    "OPERACIONES": ("Operaciones", {"PATRON": "Patrón", "SUPERVISOR_OPS": "Supervisor de Operaciones"}),
}
ROLES = {"ADMIN": "Administrador"} | {rol: texto for _, roles in MODULOS.values() for rol, texto in roles.items()}
ROLES_MANTENIMIENTO = tuple(MODULOS["MANTENIMIENTO"][1])
ROLES_OPERACIONES = tuple(MODULOS["OPERACIONES"][1])

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verificar_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


class NoAutenticado(Exception):
    """Se convierte en una redirección al login."""


def usuario_actual(request: Request, conn=Depends(get_db)) -> dict:
    uid = request.session.get("uid")
    usuario = None
    if uid:
        usuario = conn.execute(
            """SELECT u.id, u.nombre, u.usuario, u.es_admin,
                      array(SELECT r.rol FROM usuarios_roles r WHERE r.usuario_id = u.id ORDER BY r.modulo) AS roles
                 FROM usuarios u WHERE u.id = %s AND u.activo""", (uid,)
        ).fetchone()
    if not usuario:
        request.session.pop("uid", None)
        raise NoAutenticado()
    request.state.usuario = usuario
    return usuario


def tiene_rol(usuario: dict | None, *roles: str) -> bool:
    """El administrador puede hacer todo."""
    return bool(usuario) and (usuario["es_admin"] or any(rol in roles for rol in usuario["roles"]))


def nombre_roles(usuario: dict) -> str:
    """Para mostrar: «Administrador» o los roles de cada módulo."""
    if usuario["es_admin"]:
        return ROLES["ADMIN"]
    return " · ".join(ROLES[rol] for rol in usuario["roles"]) or "Sin rol"


def requiere(*roles: str):
    def dependencia(usuario: dict = Depends(usuario_actual)) -> dict:
        if not tiene_rol(usuario, *roles):
            raise HTTPException(403, "No tienes permiso para esta acción.")
        return usuario

    return dependencia


def token_csrf(request: Request) -> str:
    if "csrf" not in request.session:
        request.session["csrf"] = secrets.token_urlsafe(32)
    return request.session["csrf"]


async def formulario(request: Request):
    """Lee el formulario de un POST y verifica el token CSRF."""
    form = await request.form()
    esperado = request.session.get("csrf")
    if not esperado or not secrets.compare_digest(str(form.get("csrf", "")), esperado):
        raise HTTPException(403, "El formulario venció. Recarga la página e intenta de nuevo.")
    return form
