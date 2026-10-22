"""Ítems del formulario de inspección de la bitácora (los administra el supervisor)."""
from fastapi import APIRouter, Depends, HTTPException, Request

from ..db import get_db
from ..seguridad import formulario, requiere
from .data import categorias_para_selector
from ..web import (ERRORES, PRIORIDADES, avisar, campo_casilla, campo_entero, campo_opcion, campo_texto,
                   redirigir, render, texto_error)

router = APIRouter(prefix="/data/items")


def _contexto(conn, item: dict, error: str | None = None) -> dict:
    items = conn.execute(
        """SELECT i.*, c.nombre AS categoria FROM items_inspeccion i
             JOIN categorias c ON c.id = i.categoria_id
            ORDER BY i.activo DESC, i.aplica_a DESC, c.id, i.orden, i.nombre"""
    ).fetchall()
    return {
        "items": items,
        "item": item,
        "categorias": categorias_para_selector(conn, item.get("categoria_id")),
        "error": error,
    }


def _datos(form) -> dict:
    return {
        "nombre": campo_texto(form, "nombre", "Nombre", True),
        "categoria_id": campo_entero(form, "categoria_id", "Categoría", True),
        "prioridad": campo_opcion(form, "prioridad", PRIORIDADES, "Prioridad de la ODS"),
        "aplica_a": campo_opcion(form, "aplica_a", ("LANCHA", "GENERADOR"), "Se revisa en"),
        "orden": campo_entero(form, "orden", "Orden"),   # vacío = al final de su categoría
        "activo": campo_casilla(form, "activo"),
    }


@router.get("")
def lista(request: Request, conn=Depends(get_db), usuario=Depends(requiere("SUPERVISOR"))):
    return render(request, "items/lista.html",
                  _contexto(conn, {"prioridad": "MEDIA", "aplica_a": "LANCHA", "activo": True}))


@router.post("")
def crear(request: Request, form=Depends(formulario), conn=Depends(get_db),
          usuario=Depends(requiere("SUPERVISOR"))):
    try:
        datos = _datos(form)
        with conn.transaction():
            conn.execute(
                """INSERT INTO items_inspeccion (nombre, categoria_id, prioridad, aplica_a, orden, activo)
                   VALUES (%(nombre)s, %(categoria_id)s, %(prioridad)s, %(aplica_a)s,
                           coalesce(%(orden)s, (SELECT coalesce(max(orden), 0) + 1 FROM items_inspeccion
                                                 WHERE categoria_id = %(categoria_id)s)),
                           %(activo)s)""",
                datos,
            )
    except ERRORES as e:
        item = dict(form) | {"activo": campo_casilla(form, "activo")}
        return render(request, "items/lista.html", _contexto(conn, item, texto_error(e)), 422)
    conn.commit()
    avisar(request, f"Ítem «{datos['nombre']}» agregado al formulario de bitácora.")
    return redirigir("/data/items")


@router.get("/{item_id}/editar")
def editar(item_id: int, request: Request, conn=Depends(get_db), usuario=Depends(requiere("SUPERVISOR"))):
    item = conn.execute("SELECT * FROM items_inspeccion WHERE id = %s", (item_id,)).fetchone()
    if not item:
        raise HTTPException(404)
    return render(request, "items/form.html", _contexto(conn, item))


@router.post("/{item_id}/editar")
def actualizar(item_id: int, request: Request, form=Depends(formulario), conn=Depends(get_db),
               usuario=Depends(requiere("SUPERVISOR"))):
    try:
        datos = _datos(form) | {"id": item_id}
        with conn.transaction():
            conn.execute(
                """UPDATE items_inspeccion SET nombre = %(nombre)s, categoria_id = %(categoria_id)s,
                          prioridad = %(prioridad)s, aplica_a = %(aplica_a)s,
                          orden = coalesce(%(orden)s, orden), activo = %(activo)s
                    WHERE id = %(id)s""",
                datos,
            )
    except ERRORES as e:
        item = dict(form) | {"id": item_id, "activo": campo_casilla(form, "activo")}
        return render(request, "items/form.html", _contexto(conn, item, texto_error(e)), 422)
    conn.commit()
    avisar(request, "Ítem actualizado.")
    return redirigir("/data/items")
