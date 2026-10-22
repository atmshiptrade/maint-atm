"""Módulo de tareas: el programa (cronograma) de cada lancha o generador es el listado filtrado."""
from itertools import groupby

from fastapi import APIRouter, Depends, HTTPException, Request

from ..db import get_db
from ..seguridad import ROLES_MANTENIMIENTO, formulario, requiere, usuario_actual
from ..web import (ERRORES, ESTADOS_TAREA, ErrorFormulario, avisar, campo_casilla, campo_entero, campo_fecha,
                   campo_numero, campo_texto, param_entero, redirigir, render, texto_error)

# Solo para los roles de Mantenimiento
router = APIRouter(prefix="/programa", dependencies=[Depends(requiere(*ROLES_MANTENIMIENTO))])


@router.get("")
def lista(request: Request, conn=Depends(get_db), usuario=Depends(usuario_actual)):
    filtros = {
        "lancha_id": param_entero(request, "lancha_id"),
        "equipo_id": param_entero(request, "equipo_id"),
        "categoria_id": param_entero(request, "categoria_id"),
        "estado": request.query_params.get("estado") or None,
        "tipo": request.query_params.get("tipo") or None,
        "inactivas": request.query_params.get("inactivas") == "1",
    }
    condiciones, params = [], []
    for campo in ("lancha_id", "equipo_id", "categoria_id"):
        if filtros[campo]:
            condiciones.append(f"{campo} = %s")
            params.append(filtros[campo])
    if filtros["tipo"] in ("PROPULSION", "GENERADOR"):
        condiciones.append("tipo_equipo = %s")
        params.append(filtros["tipo"])
    if filtros["estado"] in ESTADOS_TAREA:
        condiciones.append("estado = %s")
        params.append(filtros["estado"])
    if not filtros["inactivas"] and filtros["estado"] != "INACTIVA":
        condiciones.append("activa")
    where = ("WHERE " + " AND ".join(condiciones)) if condiciones else ""
    tareas = conn.execute(
        f"""SELECT * FROM v_estado_tareas {where}
             ORDER BY tipo_equipo DESC, unidad, nivel DESC NULLS LAST, actividad""",
        params,
    ).fetchall()
    # Una tarjeta por lancha o generador, en el orden de la consulta (lanchas primero)
    grupos = [
        {"tipo": tipo, "unidad": unidad, "equipo_id": equipo_id, "lancha_id": lancha_id, "tareas": list(filas)}
        for (tipo, unidad, equipo_id, lancha_id), filas in groupby(
            tareas, key=lambda t: (t["tipo_equipo"], t["unidad"], t["equipo_id"], t["lancha_id"]))
    ]
    return render(request, "programa/lista.html", {
        "grupos": grupos,
        "filtros": filtros,
        "lanchas": conn.execute("SELECT id, codigo, nombre FROM lanchas ORDER BY numero").fetchall(),
        "categorias": conn.execute("SELECT id, nombre FROM categorias ORDER BY id").fetchall(),
    })


def _unidades(conn) -> dict:
    """Lanchas (su horómetro) y generadores activos a los que se puede programar una tarea."""
    filas = conn.execute(
        """SELECT e.id, e.tipo, nombre_unidad(e) AS nombre, e.horometro_actual
             FROM equipos e LEFT JOIN lanchas l ON l.id = e.lancha_id
            WHERE e.activo
            ORDER BY e.tipo DESC, l.numero, e.numero"""
    ).fetchall()
    return {
        "lanchas": [f for f in filas if f["tipo"] == "PROPULSION"],
        "generadores": [f for f in filas if f["tipo"] == "GENERADOR"],
    }


def _contexto_nueva(conn, valores: dict, error: str | None = None) -> dict:
    return {
        "unidades": _unidades(conn),
        "actividades": conn.execute(
            """SELECT id, nombre, intervalo_horas_sugerido, intervalo_dias_sugerido
                 FROM actividades WHERE activa ORDER BY nombre"""
        ).fetchall(),
        "valores": valores,
        "error": error,
    }


@router.get("/nueva")
def nueva(request: Request, conn=Depends(get_db), usuario=Depends(requiere("SUPERVISOR"))):
    valores = {"fecha_ultimo_servicio": conn.execute("SELECT current_date AS hoy").fetchone()["hoy"], "equipo_id": []}
    # Viene desde una lancha o un generador: queda marcado
    if param_entero(request, "equipo_id"):
        valores["equipo_id"] = [str(param_entero(request, "equipo_id"))]
    elif param_entero(request, "lancha_id"):
        fila = conn.execute(
            "SELECT id FROM equipos WHERE lancha_id = %s AND tipo = 'PROPULSION' AND activo",
            (param_entero(request, "lancha_id"),),
        ).fetchone()
        valores["equipo_id"] = [str(fila["id"])] if fila else []
    return render(request, "programa/nueva.html", _contexto_nueva(conn, valores))


@router.post("/nueva")
def crear(request: Request, form=Depends(formulario), conn=Depends(get_db),
          usuario=Depends(requiere("SUPERVISOR"))):
    try:
        actividad_id = campo_entero(form, "actividad_id", "Actividad", True)
        intervalo_horas = campo_numero(form, "intervalo_horas", "Cada cuántas horas")
        intervalo_dias = campo_entero(form, "intervalo_dias", "Cada cuántos días")
        fecha_ultimo = campo_fecha(form, "fecha_ultimo_servicio", "Fecha del último servicio")
        notas = campo_texto(form, "notas")
        equipos = [int(e) for e in form.getlist("equipo_id") if str(e).isdigit()]
        if not intervalo_horas and not intervalo_dias:
            raise ErrorFormulario("Indica cada cuántas horas, cada cuántos días, o ambos.")
        if intervalo_dias and not fecha_ultimo:
            raise ErrorFormulario("Indica la fecha del último servicio.")
        if not equipos:
            raise ErrorFormulario("Marca al menos una lancha o un generador.")

        filas = []
        for equipo_id in equipos:
            horometro_ultimo = None
            if intervalo_horas:
                horometro_ultimo = campo_numero(form, f"ultimo_{equipo_id}", "Horómetro del último servicio", True)
            filas.append({
                "equipo_id": equipo_id, "actividad_id": actividad_id,
                "intervalo_horas": intervalo_horas, "horometro_ultimo": horometro_ultimo,
                "intervalo_dias": intervalo_dias, "fecha_ultimo": fecha_ultimo if intervalo_dias else None,
                "notas": notas,
            })
        with conn.transaction():
            for fila in filas:
                conn.execute(
                    """INSERT INTO tareas (equipo_id, actividad_id, intervalo_horas, horometro_ultimo_servicio,
                                           intervalo_dias, fecha_ultimo_servicio, notas)
                       VALUES (%(equipo_id)s, %(actividad_id)s, %(intervalo_horas)s, %(horometro_ultimo)s,
                               %(intervalo_dias)s, %(fecha_ultimo)s, %(notas)s)""",
                    fila,
                )
            # Si alguna tarea ya entra en su ventana, la ODS se abre de inmediato
            creadas = conn.execute("SELECT id FROM generar_ordenes_preventivas()").fetchall()
    except ERRORES as e:
        valores = dict(form) | {"equipo_id": form.getlist("equipo_id")}
        return render(request, "programa/nueva.html", _contexto_nueva(conn, valores, texto_error(e)), 422)
    conn.commit()
    avisar(request, f"{len(filas)} tarea(s) programada(s).")
    if creadas:
        avisar(request, "Se abrieron ODS preventivas: " + ", ".join(f"#{c['id']}" for c in creadas), "warning")
    if len(equipos) == 1:
        return redirigir(f"/programa?equipo_id={equipos[0]}")
    return redirigir("/programa")


def _buscar_tarea(conn, tarea_id: int) -> dict:
    tarea = conn.execute(
        """SELECT t.*, nombre_unidad(e) AS unidad, e.tipo AS tipo_equipo, e.horometro_actual, a.nombre AS actividad
             FROM tareas t JOIN equipos e ON e.id = t.equipo_id JOIN actividades a ON a.id = t.actividad_id
            WHERE t.id = %s""",
        (tarea_id,),
    ).fetchone()
    if not tarea:
        raise HTTPException(404)
    return tarea


@router.get("/{tarea_id}/editar")
def editar(tarea_id: int, request: Request, conn=Depends(get_db), usuario=Depends(requiere("SUPERVISOR"))):
    return render(request, "programa/editar.html", {"tarea": _buscar_tarea(conn, tarea_id)})


@router.post("/{tarea_id}/editar")
def actualizar(tarea_id: int, request: Request, form=Depends(formulario), conn=Depends(get_db),
               usuario=Depends(requiere("SUPERVISOR"))):
    tarea = _buscar_tarea(conn, tarea_id)
    try:
        datos = {
            "id": tarea_id,
            "intervalo_horas": campo_numero(form, "intervalo_horas", "Cada cuántas horas"),
            "horometro_ultimo": campo_numero(form, "horometro_ultimo_servicio", "Horómetro del último servicio"),
            "intervalo_dias": campo_entero(form, "intervalo_dias", "Cada cuántos días"),
            "fecha_ultimo": campo_fecha(form, "fecha_ultimo_servicio", "Fecha del último servicio"),
            "activa": campo_casilla(form, "activa"),
            "notas": campo_texto(form, "notas"),
        }
        with conn.transaction():
            conn.execute(
                """UPDATE tareas SET intervalo_horas = %(intervalo_horas)s,
                          horometro_ultimo_servicio = %(horometro_ultimo)s,
                          intervalo_dias = %(intervalo_dias)s, fecha_ultimo_servicio = %(fecha_ultimo)s,
                          activa = %(activa)s, notas = %(notas)s
                    WHERE id = %(id)s""",
                datos,
            )
            creadas = conn.execute("SELECT id FROM generar_ordenes_preventivas()").fetchall()
    except ERRORES as e:
        valores = tarea | dict(form) | {"activa": campo_casilla(form, "activa")}
        return render(request, "programa/editar.html", {"tarea": valores, "error": texto_error(e)}, 422)
    conn.commit()
    avisar(request, "Tarea actualizada.")
    if creadas:
        avisar(request, "Se abrieron ODS preventivas: " + ", ".join(f"#{c['id']}" for c in creadas), "warning")
    return redirigir(f"/programa?equipo_id={tarea['equipo_id']}")
