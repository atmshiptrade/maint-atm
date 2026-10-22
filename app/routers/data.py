"""Módulo Data: las listas desplegables que administra la empresa.

Mantenimiento: actividades, categorías, ítems de inspección de la bitácora y proveedores
(pantallas propias, aquí). Operaciones: clientes, buques, ubicaciones, tipos de servicio y marinos
(catálogos simples de app/catalogos.py). Cada supervisor ve solo los de su módulo. Las listas que usa
la lógica del sistema (severidad, estados, roles) son fijas.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ..catalogos import CATALOGOS
from ..db import get_db
from ..seguridad import formulario, requiere, tiene_rol
from ..web import ERRORES, avisar, campo_casilla, campo_texto, redirigir, render, texto_error

router = APIRouter(prefix="/data")


def categorias_para_selector(conn, actual=None) -> list[dict]:
    """Categorías activas, más la que ya tiene el registro aunque se haya desactivado."""
    try:
        actual = int(actual) if actual not in (None, "") else None
    except (TypeError, ValueError):
        actual = None
    return conn.execute(
        "SELECT id, nombre, activa FROM categorias WHERE activa OR id = %s ORDER BY nombre", (actual,)
    ).fetchall()


@router.get("")
def inicio(request: Request, conn=Depends(get_db), usuario=Depends(requiere("SUPERVISOR", "SUPERVISOR_OPS"))):
    resumen = None
    if tiene_rol(usuario, "SUPERVISOR"):
        resumen = conn.execute(
            """SELECT (SELECT count(*) FROM actividades WHERE activa) AS actividades,
                      (SELECT count(*) FROM categorias WHERE activa) AS categorias,
                      (SELECT count(*) FROM items_inspeccion WHERE activo AND aplica_a = 'LANCHA') AS items_lancha,
                      (SELECT count(*) FROM items_inspeccion WHERE activo AND aplica_a = 'GENERADOR') AS items_generador,
                      (SELECT count(*) FROM proveedores WHERE activo) AS proveedores"""
        ).fetchone()
    # Catálogos simples de los demás módulos, agrupados por módulo
    otros = {}
    for catalogo in CATALOGOS.values():
        if tiene_rol(usuario, *catalogo.roles):
            total = conn.execute(f"SELECT count(*) AS n FROM {catalogo.tabla} WHERE {catalogo.activo}").fetchone()["n"]
            otros.setdefault(catalogo.modulo, []).append((catalogo, total))
    return render(request, "data/inicio.html", {"resumen": resumen, "otros": otros})


# ---------------------------------------------------------------------------
# Categorías
# ---------------------------------------------------------------------------
def _contexto_categorias(conn, nueva: dict, error: str | None = None) -> dict:
    categorias = conn.execute(
        """SELECT c.*,
                  (SELECT count(*) FROM actividades a WHERE a.categoria_id = c.id) AS actividades,
                  (SELECT count(*) FROM items_inspeccion i WHERE i.categoria_id = c.id) AS total_items
             FROM categorias c
            ORDER BY c.activa DESC, c.nombre"""
    ).fetchall()
    return {"categorias": categorias, "nueva": nueva, "error": error}


@router.get("/categorias")
def categorias(request: Request, conn=Depends(get_db), usuario=Depends(requiere("SUPERVISOR"))):
    return render(request, "data/categorias.html", _contexto_categorias(conn, {}))


@router.post("/categorias")
def crear_categoria(request: Request, form=Depends(formulario), conn=Depends(get_db),
                    usuario=Depends(requiere("SUPERVISOR"))):
    """También la usan los formularios de actividad e ítem (creación rápida, responde JSON)."""
    quiere_json = "application/json" in request.headers.get("accept", "")
    try:
        nombre = campo_texto(form, "nombre", "Nombre de la categoría", True)
        with conn.transaction():
            categoria = conn.execute(
                "INSERT INTO categorias (nombre) VALUES (%s) RETURNING id, nombre", (nombre,)
            ).fetchone()
    except ERRORES as e:
        if quiere_json:
            return JSONResponse({"error": texto_error(e)}, status_code=422)
        return render(request, "data/categorias.html", _contexto_categorias(conn, dict(form), texto_error(e)), 422)
    conn.commit()
    if quiere_json:
        return JSONResponse(categoria)
    avisar(request, f"Categoría «{categoria['nombre']}» creada.")
    return redirigir("/data/categorias")


def _buscar_categoria(conn, categoria_id: int) -> dict:
    categoria = conn.execute("SELECT * FROM categorias WHERE id = %s", (categoria_id,)).fetchone()
    if not categoria:
        raise HTTPException(404)
    return categoria


@router.get("/categorias/{categoria_id}/editar")
def editar_categoria(categoria_id: int, request: Request, conn=Depends(get_db),
                     usuario=Depends(requiere("SUPERVISOR"))):
    return render(request, "data/categoria_form.html", {"categoria": _buscar_categoria(conn, categoria_id)})


@router.post("/categorias/{categoria_id}/editar")
def actualizar_categoria(categoria_id: int, request: Request, form=Depends(formulario), conn=Depends(get_db),
                         usuario=Depends(requiere("SUPERVISOR"))):
    categoria = _buscar_categoria(conn, categoria_id)
    try:
        nombre = campo_texto(form, "nombre", "Nombre", True)
        with conn.transaction():
            conn.execute("UPDATE categorias SET nombre = %s, activa = %s WHERE id = %s",
                         (nombre, campo_casilla(form, "activa"), categoria_id))
    except ERRORES as e:
        valores = categoria | dict(form) | {"activa": campo_casilla(form, "activa")}
        return render(request, "data/categoria_form.html", {"categoria": valores, "error": texto_error(e)}, 422)
    conn.commit()
    avisar(request, "Categoría actualizada.")
    return redirigir("/data/categorias")


@router.post("/categorias/{categoria_id}/eliminar")
def eliminar_categoria(categoria_id: int, request: Request, form=Depends(formulario), conn=Depends(get_db),
                       usuario=Depends(requiere("SUPERVISOR"))):
    categoria = _buscar_categoria(conn, categoria_id)
    try:
        with conn.transaction():
            conn.execute("DELETE FROM categorias WHERE id = %s", (categoria_id,))
    except ERRORES:
        avisar(request, f"«{categoria['nombre']}» ya se usa en actividades o ítems: desactívala en lugar de eliminarla.",
               "danger")
        return redirigir("/data/categorias")
    conn.commit()
    avisar(request, f"Categoría «{categoria['nombre']}» eliminada.")
    return redirigir("/data/categorias")


# ---------------------------------------------------------------------------
# Proveedores (servicios contratados en las ODS)
# ---------------------------------------------------------------------------
def _datos_proveedor(form) -> dict:
    rif = campo_texto(form, "rif")
    return {
        "nombre": campo_texto(form, "nombre", "Nombre o razón social", True),
        "rif": rif.upper() if rif else None,
        "especialidad": campo_texto(form, "especialidad"),
        "contacto": campo_texto(form, "contacto"),
        "telefono": campo_texto(form, "telefono"),
        "correo": campo_texto(form, "correo"),
        "notas": campo_texto(form, "notas"),
    }


def _contexto_proveedores(conn, nuevo: dict, error: str | None = None) -> dict:
    proveedores = conn.execute(
        """SELECT p.*, (SELECT count(*) FROM ordenes o WHERE o.proveedor_id = p.id) AS servicios
             FROM proveedores p ORDER BY p.activo DESC, p.nombre"""
    ).fetchall()
    return {"proveedores": proveedores, "nuevo": nuevo, "error": error}


@router.get("/proveedores")
def proveedores(request: Request, conn=Depends(get_db), usuario=Depends(requiere("SUPERVISOR"))):
    return render(request, "data/proveedores.html", _contexto_proveedores(conn, {}))


@router.post("/proveedores")
def crear_proveedor(request: Request, form=Depends(formulario), conn=Depends(get_db),
                    usuario=Depends(requiere("SUPERVISOR"))):
    """También la usa el formulario de la ODS (creación rápida, responde JSON)."""
    quiere_json = "application/json" in request.headers.get("accept", "")
    try:
        datos = _datos_proveedor(form)
        with conn.transaction():
            proveedor = conn.execute(
                """INSERT INTO proveedores (nombre, rif, especialidad, contacto, telefono, correo, notas)
                   VALUES (%(nombre)s, %(rif)s, %(especialidad)s, %(contacto)s, %(telefono)s, %(correo)s, %(notas)s)
                   RETURNING id, nombre""",
                datos,
            ).fetchone()
    except ERRORES as e:
        if quiere_json:
            return JSONResponse({"error": texto_error(e)}, status_code=422)
        return render(request, "data/proveedores.html", _contexto_proveedores(conn, dict(form), texto_error(e)), 422)
    conn.commit()
    if quiere_json:
        return JSONResponse(proveedor)
    avisar(request, f"Proveedor «{proveedor['nombre']}» registrado.")
    return redirigir("/data/proveedores")


def _buscar_proveedor(conn, proveedor_id: int) -> dict:
    proveedor = conn.execute("SELECT * FROM proveedores WHERE id = %s", (proveedor_id,)).fetchone()
    if not proveedor:
        raise HTTPException(404)
    return proveedor


@router.get("/proveedores/{proveedor_id}/editar")
def editar_proveedor(proveedor_id: int, request: Request, conn=Depends(get_db),
                     usuario=Depends(requiere("SUPERVISOR"))):
    return render(request, "data/proveedor_form.html", {"proveedor": _buscar_proveedor(conn, proveedor_id)})


@router.post("/proveedores/{proveedor_id}/editar")
def actualizar_proveedor(proveedor_id: int, request: Request, form=Depends(formulario), conn=Depends(get_db),
                         usuario=Depends(requiere("SUPERVISOR"))):
    proveedor = _buscar_proveedor(conn, proveedor_id)
    try:
        datos = _datos_proveedor(form) | {"id": proveedor_id, "activo": campo_casilla(form, "activo")}
        with conn.transaction():
            conn.execute(
                """UPDATE proveedores SET nombre = %(nombre)s, rif = %(rif)s, especialidad = %(especialidad)s,
                          contacto = %(contacto)s, telefono = %(telefono)s, correo = %(correo)s,
                          notas = %(notas)s, activo = %(activo)s
                    WHERE id = %(id)s""",
                datos,
            )
    except ERRORES as e:
        valores = proveedor | dict(form) | {"activo": campo_casilla(form, "activo")}
        return render(request, "data/proveedor_form.html", {"proveedor": valores, "error": texto_error(e)}, 422)
    conn.commit()
    avisar(request, "Proveedor actualizado.")
    return redirigir("/data/proveedores")


@router.post("/proveedores/{proveedor_id}/eliminar")
def eliminar_proveedor(proveedor_id: int, request: Request, form=Depends(formulario), conn=Depends(get_db),
                       usuario=Depends(requiere("SUPERVISOR"))):
    proveedor = _buscar_proveedor(conn, proveedor_id)
    try:
        with conn.transaction():
            conn.execute("DELETE FROM proveedores WHERE id = %s", (proveedor_id,))
    except ERRORES:
        avisar(request, f"«{proveedor['nombre']}» ya tiene servicios registrados: desactívalo en lugar de eliminarlo.",
               "danger")
        return redirigir("/data/proveedores")
    conn.commit()
    avisar(request, f"Proveedor «{proveedor['nombre']}» eliminado.")
    return redirigir("/data/proveedores")
