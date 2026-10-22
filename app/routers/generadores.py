"""Generadores: equipos independientes con su propio horómetro, programa y órdenes.
No pertenecen a una lancha: en cada bitácora se registra en qué lancha iba el generador,
y de ahí sale su historial de ubicación."""
from fastapi import APIRouter, Depends, HTTPException, Request

from ..db import get_db
from ..seguridad import ROLES_MANTENIMIENTO, formulario, requiere, usuario_actual
from ..web import (ERRORES, avisar, campo_casilla, campo_fecha, campo_numero, campo_texto, redirigir, render,
                   texto_error)

# Solo para los roles de Mantenimiento
router = APIRouter(prefix="/generadores", dependencies=[Depends(requiere(*ROLES_MANTENIMIENTO))])

SQL_GENERADOR = """
    SELECT g.*, u.horas_por_dia, ub.lancha_id AS ubicacion_id, ub.lancha AS ubicacion, ub.fecha AS ubicacion_fecha,
           (SELECT max(b.fecha) FROM bitacora_lecturas bl JOIN bitacora b ON b.id = bl.bitacora_id
             WHERE bl.equipo_id = g.id) AS ultima_lectura,
           (SELECT count(*) FROM v_estado_tareas v WHERE v.equipo_id = g.id AND v.nivel >= 1) AS alertas,
           (SELECT count(*) FROM ordenes o
             WHERE o.equipo_id = g.id AND o.estado IN ('ABIERTA', 'EN_PROCESO', 'EJECUTADA')) AS ot_pendientes
      FROM equipos g
      LEFT JOIN v_uso_equipos u ON u.equipo_id = g.id
      -- Última lancha donde se registró su horómetro
      LEFT JOIN LATERAL (
           SELECT la.id AS lancha_id, la.nombre AS lancha, b.fecha
             FROM bitacora_lecturas bl
             JOIN bitacora b ON b.id = bl.bitacora_id
             JOIN lanchas la ON la.id = b.lancha_id
            WHERE bl.equipo_id = g.id
            ORDER BY b.fecha DESC, b.id DESC
            LIMIT 1) ub ON true
     WHERE g.tipo = 'GENERADOR'
"""


def _buscar(conn, generador_id: int) -> dict:
    generador = conn.execute(SQL_GENERADOR + " AND g.id = %s", (generador_id,)).fetchone()
    if not generador:
        raise HTTPException(404)
    return generador


def _datos(form) -> dict:
    return {
        "nombre": campo_texto(form, "nombre", "Nombre", True),
        "marca": campo_texto(form, "marca"),
        "modelo": campo_texto(form, "modelo"),
        "serie": campo_texto(form, "serie"),
        "potencia_kva": campo_numero(form, "potencia_kva", "Potencia (kVA)"),
        "fecha_alta": campo_fecha(form, "fecha_alta", "Fecha de alta", True),
        "horometro_inicial": campo_numero(form, "horometro_inicial", "Horómetro actual", True),
        "notas": campo_texto(form, "notas"),
    }


@router.get("")
def lista(request: Request, conn=Depends(get_db), usuario=Depends(usuario_actual)):
    generadores = conn.execute(SQL_GENERADOR + " ORDER BY g.activo DESC, g.numero").fetchall()
    return render(request, "generadores/lista.html", {"generadores": generadores})


@router.get("/nuevo")
def nuevo(request: Request, conn=Depends(get_db), usuario=Depends(requiere("SUPERVISOR"))):
    hoy = conn.execute("SELECT current_date AS hoy").fetchone()["hoy"]
    return render(request, "generadores/form.html", {"generador": {"fecha_alta": hoy}})


@router.post("/nuevo")
def crear(request: Request, form=Depends(formulario), conn=Depends(get_db),
          usuario=Depends(requiere("SUPERVISOR"))):
    try:
        datos = _datos(form)
        with conn.transaction():
            generador = conn.execute(
                """INSERT INTO equipos (tipo, nombre, marca, modelo, serie, potencia_kva,
                                        fecha_alta, horometro_inicial, notas)
                   VALUES ('GENERADOR', %(nombre)s, %(marca)s, %(modelo)s, %(serie)s,
                           %(potencia_kva)s, %(fecha_alta)s, %(horometro_inicial)s, %(notas)s)
                   RETURNING id, codigo""",
                datos,
            ).fetchone()
    except ERRORES as e:
        return render(request, "generadores/form.html", {"generador": dict(form), "error": texto_error(e)}, 422)
    conn.commit()
    avisar(request, f"Generador {generador['codigo']} creado. Ahora prográmale sus tareas de mantenimiento.")
    return redirigir(f"/generadores/{generador['id']}")


@router.get("/{generador_id}")
def detalle(generador_id: int, request: Request, conn=Depends(get_db), usuario=Depends(usuario_actual)):
    generador = _buscar(conn, generador_id)
    tareas = conn.execute(
        "SELECT * FROM v_estado_tareas WHERE equipo_id = %s AND activa ORDER BY nivel DESC, actividad",
        (generador_id,),
    ).fetchall()
    ordenes = conn.execute(
        """SELECT id, tipo, estado, prioridad, descripcion FROM ordenes
            WHERE equipo_id = %s AND estado IN ('ABIERTA', 'EN_PROCESO', 'EJECUTADA') ORDER BY id""",
        (generador_id,),
    ).fetchall()
    lecturas = conn.execute(
        """SELECT b.id AS bitacora_id, b.fecha, b.origen, l.horometro, la.id AS lancha_id, la.nombre AS lancha
             FROM bitacora_lecturas l
             JOIN bitacora b ON b.id = l.bitacora_id
             LEFT JOIN lanchas la ON la.id = b.lancha_id
            WHERE l.equipo_id = %s ORDER BY b.fecha DESC, l.id DESC LIMIT 15""",
        (generador_id,),
    ).fetchall()
    return render(request, "generadores/detalle.html",
                  {"generador": generador, "tareas": tareas, "ordenes": ordenes, "lecturas": lecturas})


@router.get("/{generador_id}/editar")
def editar(generador_id: int, request: Request, conn=Depends(get_db), usuario=Depends(requiere("SUPERVISOR"))):
    return render(request, "generadores/form.html", {"generador": _buscar(conn, generador_id)})


@router.post("/{generador_id}/editar")
def actualizar(generador_id: int, request: Request, form=Depends(formulario), conn=Depends(get_db),
               usuario=Depends(requiere("SUPERVISOR"))):
    generador = _buscar(conn, generador_id)
    try:
        datos = _datos(form) | {"id": generador_id, "activo": campo_casilla(form, "activo")}
        with conn.transaction():
            conn.execute(
                """UPDATE equipos SET nombre = %(nombre)s, marca = %(marca)s, modelo = %(modelo)s,
                          serie = %(serie)s, potencia_kva = %(potencia_kva)s, fecha_alta = %(fecha_alta)s,
                          horometro_inicial = %(horometro_inicial)s, activo = %(activo)s, notas = %(notas)s
                    WHERE id = %(id)s AND tipo = 'GENERADOR'""",
                datos,
            )
    except ERRORES as e:
        valores = generador | dict(form) | {"activo": campo_casilla(form, "activo")}
        return render(request, "generadores/form.html", {"generador": valores, "error": texto_error(e)}, 422)
    conn.commit()
    avisar(request, "Generador actualizado.")
    return redirigir(f"/generadores/{generador_id}")
