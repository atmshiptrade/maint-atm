from fastapi import APIRouter, Depends, HTTPException, Request

from .. import materiales
from ..catalogos import CATALOGOS, sugerencias
from ..db import get_db
from ..seguridad import formulario, requiere
from .data import categorias_para_selector
from ..web import (ERRORES, PRIORIDADES, avisar, campo_casilla, campo_entero, campo_numero, campo_opcion,
                   campo_texto, redirigir, render, texto_error)

router = APIRouter(prefix="/data/actividades")


def _contexto(conn, actividad: dict, filas: list[dict], error: str | None = None) -> dict:
    categorias = categorias_para_selector(conn, actividad.get("categoria_id"))
    return {"actividad": actividad, "categorias": categorias, "severidades": PRIORIDADES, "error": error,
            "filas_materiales": filas,
            "opciones_materiales": materiales.opciones(conn, [f["material_id"] for f in filas]),
            "cat_materiales": CATALOGOS["materiales"],
            "sugerencias_materiales": sugerencias(conn, CATALOGOS["materiales"])}


def _datos(form) -> dict:
    return {
        "nombre": campo_texto(form, "nombre", "Nombre", True),
        "categoria_id": campo_entero(form, "categoria_id", "Categoría", True),
        "severidad": campo_opcion(form, "severidad", PRIORIDADES, "Severidad"),
        "tolerancia_pct": campo_numero(form, "tolerancia_pct", "Tolerancia", True),
        "tiempo_estimado_h": campo_numero(form, "tiempo_estimado_h", "Tiempo estimado"),
        "intervalo_horas_sugerido": campo_numero(form, "intervalo_horas_sugerido", "Cada cuántas horas"),
        "intervalo_dias_sugerido": campo_entero(form, "intervalo_dias_sugerido", "Cada cuántos días"),
        "materiales": campo_texto(form, "materiales"),
        "descripcion": campo_texto(form, "descripcion"),
        "activa": campo_casilla(form, "activa"),
    }


@router.get("")
def lista(request: Request, conn=Depends(get_db), usuario=Depends(requiere("SUPERVISOR"))):
    actividades = conn.execute(
        """SELECT a.*, c.nombre AS categoria,
                  (SELECT count(*) FROM tareas t WHERE t.actividad_id = a.id AND t.activa) AS tareas,
                  (SELECT json_agg(json_build_object('nombre', m.nombre, 'cantidad', x.cantidad, 'unidad', m.unidad)
                                   ORDER BY m.nombre)
                     FROM actividades_materiales x JOIN materiales m ON m.id = x.material_id
                    WHERE x.actividad_id = a.id) AS lista_materiales
             FROM actividades a JOIN categorias c ON c.id = a.categoria_id
            ORDER BY a.activa DESC, c.id, a.nombre"""
    ).fetchall()
    return render(request, "actividades/lista.html", {"actividades": actividades})


@router.get("/nueva")
def nueva(request: Request, conn=Depends(get_db), usuario=Depends(requiere("SUPERVISOR"))):
    return render(request, "actividades/form.html",
                  _contexto(conn, {"tolerancia_pct": 10, "severidad": "MEDIA", "activa": True}, []))


@router.post("/nueva")
def crear(request: Request, form=Depends(formulario), conn=Depends(get_db),
          usuario=Depends(requiere("SUPERVISOR"))):
    try:
        datos = _datos(form)
        filas = materiales.leer(form)
        with conn.transaction():
            actividad_id = conn.execute(
                """INSERT INTO actividades (nombre, categoria_id, severidad, tolerancia_pct, tiempo_estimado_h,
                                            intervalo_horas_sugerido, intervalo_dias_sugerido, materiales,
                                            descripcion, activa)
                   VALUES (%(nombre)s, %(categoria_id)s, %(severidad)s, %(tolerancia_pct)s, %(tiempo_estimado_h)s,
                           %(intervalo_horas_sugerido)s, %(intervalo_dias_sugerido)s, %(materiales)s,
                           %(descripcion)s, %(activa)s)
                   RETURNING id""",
                datos,
            ).fetchone()["id"]
            materiales.guardar(conn, "actividad", actividad_id, filas)
    except ERRORES as e:
        return render(request, "actividades/form.html",
                      _contexto(conn, dict(form) | {"activa": campo_casilla(form, "activa")},
                                materiales.del_formulario(form), texto_error(e)), 422)
    conn.commit()
    avisar(request, f"Actividad «{datos['nombre']}» creada.")
    return redirigir("/data/actividades")


@router.get("/{actividad_id}/editar")
def editar(actividad_id: int, request: Request, conn=Depends(get_db), usuario=Depends(requiere("SUPERVISOR"))):
    actividad = conn.execute("SELECT * FROM actividades WHERE id = %s", (actividad_id,)).fetchone()
    if not actividad:
        raise HTTPException(404)
    return render(request, "actividades/form.html",
                  _contexto(conn, actividad, materiales.de(conn, "actividad", actividad_id)))


@router.post("/{actividad_id}/editar")
def actualizar(actividad_id: int, request: Request, form=Depends(formulario), conn=Depends(get_db),
               usuario=Depends(requiere("SUPERVISOR"))):
    try:
        datos = _datos(form) | {"id": actividad_id}
        filas = materiales.leer(form)
        with conn.transaction():
            conn.execute(
                """UPDATE actividades SET nombre = %(nombre)s, categoria_id = %(categoria_id)s,
                          severidad = %(severidad)s, tolerancia_pct = %(tolerancia_pct)s,
                          tiempo_estimado_h = %(tiempo_estimado_h)s,
                          intervalo_horas_sugerido = %(intervalo_horas_sugerido)s,
                          intervalo_dias_sugerido = %(intervalo_dias_sugerido)s,
                          materiales = %(materiales)s, descripcion = %(descripcion)s, activa = %(activa)s
                    WHERE id = %(id)s""",
                datos,
            )
            materiales.guardar(conn, "actividad", actividad_id, filas)
    except ERRORES as e:
        actividad = dict(form) | {"id": actividad_id, "activa": campo_casilla(form, "activa")}
        return render(request, "actividades/form.html",
                      _contexto(conn, actividad, materiales.del_formulario(form), texto_error(e)), 422)
    conn.commit()
    avisar(request, "Actividad actualizada.")
    return redirigir("/data/actividades")
