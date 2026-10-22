from fastapi import APIRouter, Depends, HTTPException, Request

from ..db import get_db
from ..seguridad import MODULOS, formulario, hash_password, requiere
from ..web import (ERRORES, ErrorFormulario, avisar, campo_casilla, campo_texto, redirigir, render,
                   texto_error)

router = APIRouter(prefix="/usuarios")

# Rol de cada módulo como lo guarda la base: {"MANTENIMIENTO": "MECANICO", ...}
_ROLES_DE = """(SELECT coalesce(jsonb_object_agg(r.modulo, r.rol), '{}') FROM usuarios_roles r
                 WHERE r.usuario_id = u.id) AS roles"""


def _password(form, requerido: bool) -> str | None:
    password = campo_texto(form, "password", "Contraseña", requerido)
    if password and len(password) < 8:
        raise ErrorFormulario("La contraseña debe tener al menos 8 caracteres.")
    return password


def _roles(form) -> dict:
    """Un rol (o ninguno) por módulo, desde los campos rol_<MODULO> del formulario."""
    roles = {}
    for modulo, (nombre, opciones) in MODULOS.items():
        rol = form.get(f"rol_{modulo}") or None
        if rol and rol not in opciones:
            raise ErrorFormulario(f"Rol inválido en {nombre}.")
        if rol:
            roles[modulo] = rol
    return roles


def _guardar_roles(conn, usuario_id: int, roles: dict) -> None:
    conn.execute("DELETE FROM usuarios_roles WHERE usuario_id = %s", (usuario_id,))
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO usuarios_roles (usuario_id, modulo, rol) VALUES (%s, %s, %s)",
                        [(usuario_id, modulo, rol) for modulo, rol in roles.items()])


def _editado_desde_form(form, extra: dict) -> dict:
    """Para volver a mostrar el formulario con lo que se había escrito."""
    return dict(form) | {"activo": campo_casilla(form, "activo"), "es_admin": campo_casilla(form, "es_admin"),
                         "roles": {m: form.get(f"rol_{m}") for m in MODULOS}} | extra


@router.get("")
def lista(request: Request, conn=Depends(get_db), usuario=Depends(requiere("ADMIN"))):
    usuarios = conn.execute(
        f"""SELECT u.id, u.nombre, u.usuario, u.es_admin, u.activo, u.creado_en, {_ROLES_DE}
              FROM usuarios u ORDER BY u.activo DESC, u.nombre"""
    ).fetchall()
    return render(request, "usuarios/lista.html", {"usuarios": usuarios})


@router.get("/nuevo")
def nuevo(request: Request, usuario=Depends(requiere("ADMIN"))):
    return render(request, "usuarios/form.html", {"editado": {"roles": {}, "activo": True}})


@router.post("/nuevo")
def crear(request: Request, form=Depends(formulario), conn=Depends(get_db), usuario=Depends(requiere("ADMIN"))):
    try:
        datos = {
            "nombre": campo_texto(form, "nombre", "Nombre", True),
            "usuario": campo_texto(form, "usuario", "Usuario", True).lower(),
            "es_admin": campo_casilla(form, "es_admin"),
            "password_hash": hash_password(_password(form, requerido=True)),
        }
        roles = _roles(form)
        if not roles and not datos["es_admin"]:
            raise ErrorFormulario("Asígnale un rol en al menos un módulo, o márcalo como administrador.")
        with conn.transaction():
            nuevo_id = conn.execute(
                """INSERT INTO usuarios (nombre, usuario, es_admin, password_hash)
                   VALUES (%(nombre)s, %(usuario)s, %(es_admin)s, %(password_hash)s) RETURNING id""",
                datos,
            ).fetchone()["id"]
            _guardar_roles(conn, nuevo_id, roles)
    except ERRORES as e:
        return render(request, "usuarios/form.html", {"editado": _editado_desde_form(form, {}), "error": texto_error(e)},
                      422)
    conn.commit()
    avisar(request, f"Usuario «{datos['usuario']}» creado.")
    return redirigir("/usuarios")


@router.get("/{usuario_id}/editar")
def editar(usuario_id: int, request: Request, conn=Depends(get_db), usuario=Depends(requiere("ADMIN"))):
    editado = conn.execute(
        f"SELECT u.id, u.nombre, u.usuario, u.es_admin, u.activo, {_ROLES_DE} FROM usuarios u WHERE u.id = %s",
        (usuario_id,),
    ).fetchone()
    if not editado:
        raise HTTPException(404)
    return render(request, "usuarios/form.html", {"editado": editado})


@router.post("/{usuario_id}/editar")
def actualizar(usuario_id: int, request: Request, form=Depends(formulario), conn=Depends(get_db),
               usuario=Depends(requiere("ADMIN"))):
    try:
        datos = {
            "id": usuario_id,
            "nombre": campo_texto(form, "nombre", "Nombre", True),
            "es_admin": campo_casilla(form, "es_admin"),
            "activo": campo_casilla(form, "activo"),
        }
        roles = _roles(form)
        if usuario_id == usuario["id"] and (not datos["es_admin"] or not datos["activo"]):
            raise ErrorFormulario("No puedes quitarte el rol de administrador ni desactivarte a ti mismo.")
        if not roles and not datos["es_admin"]:
            raise ErrorFormulario("Asígnale un rol en al menos un módulo, o márcalo como administrador.")
        password = _password(form, requerido=False)
        with conn.transaction():
            conn.execute(
                "UPDATE usuarios SET nombre = %(nombre)s, es_admin = %(es_admin)s, activo = %(activo)s WHERE id = %(id)s",
                datos,
            )
            _guardar_roles(conn, usuario_id, roles)
            if password:
                conn.execute("UPDATE usuarios SET password_hash = %s WHERE id = %s",
                             (hash_password(password), usuario_id))
    except ERRORES as e:
        nombre_usuario = conn.execute("SELECT usuario FROM usuarios WHERE id = %s", (usuario_id,)).fetchone()
        editado = _editado_desde_form(form, {"id": usuario_id,
                                             "usuario": nombre_usuario["usuario"] if nombre_usuario else ""})
        return render(request, "usuarios/form.html", {"editado": editado, "error": texto_error(e)}, 422)
    conn.commit()
    avisar(request, "Usuario actualizado.")
    return redirigir("/usuarios")
