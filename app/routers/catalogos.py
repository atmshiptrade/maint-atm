"""Pantallas de los catálogos simples de Data (ver app/catalogos.py)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from ..catalogos import CATALOGOS, Catalogo, datos, sugerencias
from ..db import get_db
from ..seguridad import formulario, tiene_rol, usuario_actual
from ..web import ERRORES, avisar, campo_casilla, redirigir, render, texto_error

# Se incluye al final en main.py: /data/<clave> solo atiende los catálogos que no tienen pantalla propia
router = APIRouter(prefix="/data")


def _catalogo(clave: str, usuario: dict) -> Catalogo:
    catalogo = CATALOGOS.get(clave)
    if not catalogo:
        raise HTTPException(404)
    if not tiene_rol(usuario, *catalogo.roles):
        raise HTTPException(403, "No tienes permiso para esta acción.")
    return catalogo


def _lista(conn, catalogo: Catalogo, nuevo: dict, error: str | None = None) -> dict:
    registros = conn.execute(
        f"""SELECT t.*, t.{catalogo.activo} AS activo_, {catalogo.uso} AS usos FROM {catalogo.tabla} t
             ORDER BY t.{catalogo.activo} DESC, {catalogo.orden}"""
    ).fetchall()
    return {"catalogo": catalogo, "registros": registros, "nuevo": nuevo, "error": error,
            "sugerencias": sugerencias(conn, catalogo)}


def _buscar(conn, catalogo: Catalogo, registro_id: int) -> dict:
    registro = conn.execute(
        f"SELECT t.*, t.{catalogo.activo} AS activo_ FROM {catalogo.tabla} t WHERE t.id = %s", (registro_id,)
    ).fetchone()
    if not registro:
        raise HTTPException(404)
    return registro


@router.get("/{clave}")
def lista(clave: str, request: Request, conn=Depends(get_db), usuario=Depends(usuario_actual)):
    catalogo = _catalogo(clave, usuario)
    return render(request, "data/catalogo.html", _lista(conn, catalogo, {}))


@router.post("/{clave}")
def crear(clave: str, request: Request, form=Depends(formulario), conn=Depends(get_db),
          usuario=Depends(usuario_actual)):
    """También lo usan otros formularios para crear al vuelo (responde JSON)."""
    catalogo = _catalogo(clave, usuario)
    quiere_json = "application/json" in request.headers.get("accept", "")
    try:
        valores = datos(catalogo, form)
        columnas = ", ".join(valores)
        with conn.transaction():
            registro = conn.execute(
                f"""INSERT INTO {catalogo.tabla} ({columnas}) VALUES ({", ".join(f"%({c})s" for c in valores)})
                    RETURNING *""",
                valores,
            ).fetchone()
    except ERRORES as e:
        if quiere_json:
            return JSONResponse({"error": texto_error(e)}, status_code=422)
        return render(request, "data/catalogo.html", _lista(conn, catalogo, dict(form), texto_error(e)), 422)
    conn.commit()
    if quiere_json:
        return JSONResponse(jsonable_encoder({c: registro[c] for c in ("id", "nombre", *valores)}))
    avisar(request, f"«{registro['nombre']}» registrado en {catalogo.titulo}.")
    return redirigir(f"/data/{clave}")


@router.get("/{clave}/{registro_id}/editar")
def editar(clave: str, registro_id: int, request: Request, conn=Depends(get_db), usuario=Depends(usuario_actual)):
    catalogo = _catalogo(clave, usuario)
    return render(request, "data/catalogo_form.html", {
        "catalogo": catalogo, "registro": _buscar(conn, catalogo, registro_id), "sugerencias": sugerencias(conn, catalogo),
    })


@router.post("/{clave}/{registro_id}/editar")
def actualizar(clave: str, registro_id: int, request: Request, form=Depends(formulario), conn=Depends(get_db),
               usuario=Depends(usuario_actual)):
    catalogo = _catalogo(clave, usuario)
    registro = _buscar(conn, catalogo, registro_id)
    try:
        valores = datos(catalogo, form) | {catalogo.activo: campo_casilla(form, "activo_")}
        with conn.transaction():
            conn.execute(
                f"UPDATE {catalogo.tabla} SET {', '.join(f'{c} = %({c})s' for c in valores)} WHERE id = %(id_)s",
                valores | {"id_": registro_id},
            )
    except ERRORES as e:
        valores = registro | dict(form) | {"activo_": campo_casilla(form, "activo_")}
        return render(request, "data/catalogo_form.html", {
            "catalogo": catalogo, "registro": valores, "error": texto_error(e), "sugerencias": sugerencias(conn, catalogo),
        }, 422)
    conn.commit()
    avisar(request, f"«{valores['nombre']}» actualizado.")
    return redirigir(f"/data/{clave}")


@router.post("/{clave}/{registro_id}/eliminar")
def eliminar(clave: str, registro_id: int, request: Request, form=Depends(formulario), conn=Depends(get_db),
             usuario=Depends(usuario_actual)):
    catalogo = _catalogo(clave, usuario)
    registro = _buscar(conn, catalogo, registro_id)
    try:
        with conn.transaction():
            conn.execute(f"DELETE FROM {catalogo.tabla} WHERE id = %s", (registro_id,))
    except ERRORES:
        avisar(request, f"«{registro['nombre']}» {catalogo.uso_texto}: desactívalo en lugar de eliminarlo.", "danger")
        return redirigir(f"/data/{clave}")
    conn.commit()
    avisar(request, f"«{registro['nombre']}» eliminado.")
    return redirigir(f"/data/{clave}")
