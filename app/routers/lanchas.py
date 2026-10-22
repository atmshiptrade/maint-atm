"""Lanchas. El horómetro se maneja por lancha: cada una tiene un equipo de
propulsión que se crea con ella y no se administra aparte."""
from fastapi import APIRouter, Depends, HTTPException, Request

from ..db import get_db
from ..seguridad import ROLES_MANTENIMIENTO, formulario, requiere, usuario_actual
from ..web import (ERRORES, avisar, campo_fecha, campo_numero, campo_opcion, campo_texto, redirigir, render,
                   texto_error)

# Solo para los roles de Mantenimiento
router = APIRouter(prefix="/lanchas", dependencies=[Depends(requiere(*ROLES_MANTENIMIENTO))])

ESTADOS = ["OPERATIVA", "INACTIVA"]

# Lancha + datos de su horómetro (equipo de propulsión activo)
SQL_LANCHA = """
    SELECT l.*, e.id AS propulsion_id, e.fecha_alta, e.horometro_inicial, e.horometro_actual,
           u.horas_por_dia,
           (SELECT max(b.fecha) FROM bitacora_lecturas bl JOIN bitacora b ON b.id = bl.bitacora_id
             WHERE bl.equipo_id = e.id) AS ultima_lectura,
           EXISTS (SELECT 1 FROM bitacora_lecturas bl WHERE bl.equipo_id = e.id) AS tiene_lecturas
      FROM lanchas l
      LEFT JOIN equipos e ON e.lancha_id = l.id AND e.tipo = 'PROPULSION' AND e.activo
      LEFT JOIN v_uso_equipos u ON u.equipo_id = e.id
"""


def _buscar_lancha(conn, lancha_id: int) -> dict:
    lancha = conn.execute(SQL_LANCHA + " WHERE l.id = %s", (lancha_id,)).fetchone()
    if not lancha:
        raise HTTPException(404)
    return lancha


def _datos_lancha(form) -> dict:
    return {
        "nombre": campo_texto(form, "nombre", "Nombre", True),
        "matricula": campo_texto(form, "matricula", "Matrícula", True).upper(),
        "estado": campo_opcion(form, "estado", ESTADOS, "Estado"),
        "notas": campo_texto(form, "notas"),
        "fecha_alta": campo_fecha(form, "fecha_alta", "Fecha de alta", True),
        "horometro_inicial": campo_numero(form, "horometro_inicial", "Horómetro actual", True),
    }


@router.get("")
def lista(request: Request, conn=Depends(get_db), usuario=Depends(usuario_actual)):
    lanchas = conn.execute(
        """SELECT l.*, e.horometro_actual,
                  (SELECT json_agg(g.codigo ORDER BY g.numero)
                     FROM v_ubicacion_generadores u JOIN equipos g ON g.id = u.equipo_id
                    WHERE u.lancha_id = l.id AND g.activo) AS generadores,
                  (SELECT max(b.fecha) FROM bitacora b
                    WHERE b.lancha_id = l.id AND b.origen = 'DIARIA') AS ultima_bitacora,
                  (SELECT count(*) FROM ordenes o
                    WHERE o.lancha_id = l.id AND o.estado IN ('ABIERTA', 'EN_PROCESO', 'EJECUTADA')) AS ot_pendientes
             FROM lanchas l
             LEFT JOIN equipos e ON e.lancha_id = l.id AND e.tipo = 'PROPULSION' AND e.activo
            ORDER BY l.estado DESC, l.numero"""
    ).fetchall()
    return render(request, "lanchas/lista.html", {"lanchas": lanchas})


@router.get("/nueva")
def nueva(request: Request, conn=Depends(get_db), usuario=Depends(requiere("SUPERVISOR"))):
    hoy = conn.execute("SELECT current_date AS hoy").fetchone()["hoy"]
    return render(request, "lanchas/form.html",
                  {"lancha": {"estado": "OPERATIVA", "fecha_alta": hoy}, "estados": ESTADOS})


@router.post("/nueva")
def crear(request: Request, form=Depends(formulario), conn=Depends(get_db),
          usuario=Depends(requiere("SUPERVISOR"))):
    try:
        datos = _datos_lancha(form)
        with conn.transaction():
            lancha = conn.execute(
                """INSERT INTO lanchas (nombre, matricula, estado, notas)
                   VALUES (%(nombre)s, %(matricula)s, %(estado)s, %(notas)s)
                   RETURNING id, codigo""",
                datos,
            ).fetchone()
            conn.execute(
                """INSERT INTO equipos (lancha_id, tipo, nombre, fecha_alta, horometro_inicial)
                   VALUES (%s, 'PROPULSION', 'Motores', %s, %s)""",
                (lancha["id"], datos["fecha_alta"], datos["horometro_inicial"]),
            )
    except ERRORES as e:
        return render(request, "lanchas/form.html",
                      {"lancha": dict(form), "estados": ESTADOS, "error": texto_error(e)}, 422)
    conn.commit()
    avisar(request, f"Lancha {lancha['codigo']} · {datos['nombre']} creada.")
    return redirigir(f"/lanchas/{lancha['id']}")


@router.get("/{lancha_id}")
def detalle(lancha_id: int, request: Request, conn=Depends(get_db), usuario=Depends(usuario_actual)):
    lancha = _buscar_lancha(conn, lancha_id)
    # Generadores a bordo: los que se registraron por última vez en esta lancha
    generadores = conn.execute(
        """SELECT g.*, u.fecha AS desde,
                  (SELECT count(*) FROM v_estado_tareas v WHERE v.equipo_id = g.id AND v.nivel >= 1) AS alertas
             FROM v_ubicacion_generadores u JOIN equipos g ON g.id = u.equipo_id
            WHERE u.lancha_id = %s AND g.activo
            ORDER BY g.numero""",
        (lancha_id,),
    ).fetchall()
    tareas = conn.execute(
        """SELECT * FROM v_estado_tareas WHERE lancha_id = %s AND activa
            ORDER BY nivel DESC, actividad""",
        (lancha_id,),
    ).fetchall()
    bitacoras = conn.execute(
        """SELECT b.id, b.fecha, b.origen,
                  (SELECT count(*) FROM bitacora_respuestas r
                    WHERE r.bitacora_id = b.id AND r.resultado = 'FALLA') AS fallas
             FROM bitacora b WHERE b.lancha_id = %s
            ORDER BY b.fecha DESC, b.id DESC LIMIT 10""",
        (lancha_id,),
    ).fetchall()
    ordenes = conn.execute(
        """SELECT o.id, o.tipo, o.estado, o.prioridad, o.descripcion FROM ordenes o
            WHERE o.lancha_id = %s AND o.estado IN ('ABIERTA', 'EN_PROCESO', 'EJECUTADA')
            ORDER BY o.id""",
        (lancha_id,),
    ).fetchall()
    return render(request, "lanchas/detalle.html", {
        "lancha": lancha, "generadores": generadores, "tareas": tareas, "bitacoras": bitacoras, "ordenes": ordenes,
    })


@router.get("/{lancha_id}/editar")
def editar(lancha_id: int, request: Request, conn=Depends(get_db), usuario=Depends(requiere("SUPERVISOR"))):
    return render(request, "lanchas/form.html", {"lancha": _buscar_lancha(conn, lancha_id), "estados": ESTADOS})


@router.post("/{lancha_id}/editar")
def actualizar(lancha_id: int, request: Request, form=Depends(formulario), conn=Depends(get_db),
               usuario=Depends(requiere("SUPERVISOR"))):
    lancha = _buscar_lancha(conn, lancha_id)
    try:
        datos = _datos_lancha(form) | {"id": lancha_id, "propulsion_id": lancha["propulsion_id"]}
        with conn.transaction():
            conn.execute(
                """UPDATE lanchas SET nombre = %(nombre)s, matricula = %(matricula)s,
                          estado = %(estado)s, notas = %(notas)s
                    WHERE id = %(id)s""",
                datos,
            )
            # El horómetro inicial solo cambia mientras no haya lecturas (lo controla la base)
            conn.execute(
                """UPDATE equipos SET fecha_alta = %(fecha_alta)s, horometro_inicial = %(horometro_inicial)s
                    WHERE id = %(propulsion_id)s""",
                datos,
            )
    except ERRORES as e:
        valores = lancha | dict(form)
        return render(request, "lanchas/form.html",
                      {"lancha": valores, "estados": ESTADOS, "error": texto_error(e)}, 422)
    conn.commit()
    avisar(request, "Lancha actualizada.")
    return redirigir(f"/lanchas/{lancha_id}")
