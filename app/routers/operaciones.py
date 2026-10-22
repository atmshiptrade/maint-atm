"""Operaciones: tablero de disponibilidad de la flota.

La disponibilidad de cada lancha se deduce, no se guarda: Inactiva (lancha inactiva), En servicio
(tiene un viaje en curso, con su situación: navegando, en espera o detenida) o Disponible (con la
última ubicación conocida). La falla crítica pendiente viene de Mantenimiento, sin mostrar la ODS.
"""
from fastapi import APIRouter, Depends, Request

from ..db import get_db
from ..seguridad import ROLES_OPERACIONES, requiere, usuario_actual
from ..web import render
from . import viajes

# Solo para los roles de Operaciones
router = APIRouter(prefix="/operaciones", dependencies=[Depends(requiere(*ROLES_OPERACIONES))])


@router.get("")
def tablero(request: Request, conn=Depends(get_db), usuario=Depends(usuario_actual)):
    lanchas = conn.execute(
        """SELECT l.id, l.nombre, l.codigo, l.estado, lancha_falla_critica(l.id) AS falla_critica,
                  (SELECT id FROM viajes v WHERE v.lancha_id = l.id AND v.estado = 'EN_CURSO') AS viaje_id,
                  (SELECT count(*) FROM viajes v WHERE v.lancha_id = l.id AND v.estado = 'BORRADOR') AS programados
             FROM lanchas l ORDER BY l.estado = 'INACTIVA', l.numero"""
    ).fetchall()
    ultimas = viajes.ultimas_ubicaciones(conn)
    for lancha in lanchas:
        if lancha["estado"] == "INACTIVA":
            lancha["disponibilidad"] = "INACTIVA"
        elif lancha["viaje_id"]:
            lancha["disponibilidad"] = "EN_SERVICIO"
            lancha["viaje"] = viajes.buscar(conn, lancha["viaje_id"])
            lancha["situacion"] = viajes.trayecto(conn, lancha["viaje"])["situacion"]
        else:
            lancha["disponibilidad"] = "DISPONIBLE"
        lancha["ultima"] = ultimas.get(lancha["id"])
    pendientes = conn.execute(
        """SELECT * FROM v_viajes WHERE estado IN ('BORRADOR', 'FINALIZADO')
            ORDER BY estado = 'BORRADOR', fecha, numero"""
    ).fetchall()
    mis_viajes = conn.execute(
        """SELECT * FROM v_viajes WHERE patron_id = %s AND estado IN ('BORRADOR', 'EN_CURSO', 'FINALIZADO')
            ORDER BY estado <> 'EN_CURSO', fecha, numero""",
        (usuario["id"],),
    ).fetchall()
    return render(request, "operaciones/inicio.html", {
        "lanchas": lanchas, "pendientes": pendientes, "mis_viajes": mis_viajes,
        "supervisor": viajes.es_supervisor(usuario),
        "conteo": {d: sum(1 for x in lanchas if x["disponibilidad"] == d) for d in ("DISPONIBLE", "EN_SERVICIO", "INACTIVA")},
    })
