from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request

from .. import analisis
from ..db import get_db
from ..seguridad import ROLES_MANTENIMIENTO, ROLES_OPERACIONES, tiene_rol, usuario_actual
from ..web import param_fecha, redirigir, render

router = APIRouter()

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre"]


@router.get("/")
def inicio(request: Request, conn=Depends(get_db), usuario=Depends(usuario_actual)):
    """Inicio de Mantenimiento; quien solo tiene rol en Operaciones va a su módulo."""
    if not tiene_rol(usuario, *ROLES_MANTENIMIENTO):
        return redirigir("/operaciones" if tiene_rol(usuario, *ROLES_OPERACIONES) else "/cuenta")
    conteo_tareas = {
        f["estado"]: f["n"]
        for f in conn.execute(
            "SELECT estado, count(*) AS n FROM v_estado_tareas WHERE nivel >= 1 GROUP BY estado"
        )
    }
    conteo_ot = {
        f["estado"]: f["n"]
        for f in conn.execute(
            """SELECT estado, count(*) AS n FROM ordenes
                WHERE estado IN ('ABIERTA', 'EN_PROCESO', 'EJECUTADA') GROUP BY estado"""
        )
    }
    alertas = conn.execute(
        """SELECT * FROM v_estado_tareas
            WHERE nivel >= 1
            ORDER BY nivel DESC, fecha_estimada NULLS LAST, lancha
            LIMIT 20"""
    ).fetchall()
    mis_ordenes = conn.execute(
        """SELECT o.id, o.tipo, o.estado, o.prioridad, o.descripcion, l.nombre AS lancha
             FROM ordenes o JOIN lanchas l ON l.id = o.lancha_id
            WHERE o.asignado_a = %s AND o.estado IN ('ABIERTA', 'EN_PROCESO')
            ORDER BY array_position(ARRAY['CRITICA', 'ALTA', 'MEDIA', 'BAJA'], o.prioridad), o.id""",
        (usuario["id"],),
    ).fetchall()
    sin_bitacora = conn.execute(
        """SELECT l.id, l.nombre FROM lanchas l
            WHERE l.estado = 'OPERATIVA'
              AND NOT EXISTS (SELECT 1 FROM bitacora b
                               WHERE b.lancha_id = l.id AND b.fecha = current_date AND b.origen = 'DIARIA')
            ORDER BY l.nombre"""
    ).fetchall()
    hoy = conn.execute("SELECT current_date AS hoy").fetchone()["hoy"]

    # Fallas y tiempos: maquinaria y período elegidos (por defecto, toda la flota en los últimos 12 meses)
    atajos = analisis.periodos(hoy)
    desde = param_fecha(request, "desde") or atajos[-1][1]
    hasta = param_fecha(request, "hasta") or hoy
    if desde > hasta:
        desde, hasta = hasta, desde
    unidades = analisis.unidades(conn)
    unidad = analisis.leer_unidad(request.query_params.get("unidad"), unidades)
    filtro = {"unidad": unidad, "desde": desde, "hasta": hasta}
    return render(request, "inicio.html", {
        "fecha_larga": f"{DIAS[hoy.weekday()].capitalize()}, {hoy.day} de {MESES[hoy.month - 1]} de {hoy.year}",
        "conteo_tareas": conteo_tareas,
        "conteo_ot": conteo_ot,
        "alertas": alertas,
        "mis_ordenes": mis_ordenes,
        "sin_bitacora": sin_bitacora,
        "analisis": analisis.analizar(conn, unidad, desde, hasta),
        "filtro": filtro,
        "unidades": unidades,
        "unidad_elegida": next((u for u in unidades if u["clave"] == unidad), None),
        "atajos": atajos,
        "consulta": lambda **cambios: urlencode({k: v for k, v in (filtro | cambios).items() if v}),
    })
