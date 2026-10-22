"""Órdenes de servicio (ODS).

Flujo: ABIERTA -> EN_PROCESO (el mecánico la toma) -> EJECUTADA (el mecánico reporta
el servicio) -> CERRADA (el supervisor de mantenimiento la aprueba). El supervisor
también asigna, devuelve una ODS reportada o la cancela, y puede reportar y aprobar
en un solo paso las que hace él mismo.
"""
from datetime import timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from .. import fotos, materiales
from ..catalogos import CATALOGOS, sugerencias
from ..db import get_db
from ..seguridad import ROLES_MANTENIMIENTO, formulario, requiere, tiene_rol, usuario_actual
from ..web import (ERRORES, ESTADOS_OT, OT_PENDIENTES, PRIORIDADES, ErrorFormulario, avisar, campo_entero,
                   campo_fecha, campo_fechahora, campo_filas, campo_numero, campo_opcion, campo_texto, leer_numero,
                   param_entero, param_fecha, redirigir, render, texto_error)

# Solo para los roles de Mantenimiento
router = APIRouter(prefix="/ordenes", dependencies=[Depends(requiere(*ROLES_MANTENIMIENTO))])

LIMITE_LISTA = 300
# Qué fecha usa el filtro "desde / hasta" de la lista
FECHAS_FILTRO = {
    "ejecucion": ("Realizada", "o.fecha_ejecucion"),
    "apertura": ("Abierta", "o.fecha_apertura"),
    "cierre": ("Aprobada", "o.fecha_cierre"),
}


@router.get("")
def lista(request: Request, conn=Depends(get_db), usuario=Depends(usuario_actual)):
    filtros = {
        "vista": request.query_params.get("vista") or "pendientes",
        "lancha_id": param_entero(request, "lancha_id"),
        "equipo_id": param_entero(request, "equipo_id"),
        "tipo": request.query_params.get("tipo") or None,
        "fecha": request.query_params.get("fecha") if request.query_params.get("fecha") in FECHAS_FILTRO else "ejecucion",
        "desde": param_fecha(request, "desde"),
        "hasta": param_fecha(request, "hasta"),
    }
    if filtros["desde"] and filtros["hasta"] and filtros["desde"] > filtros["hasta"]:
        filtros["desde"], filtros["hasta"] = filtros["hasta"], filtros["desde"]
    por_fecha = bool(filtros["desde"] or filtros["hasta"])
    columna_fecha = FECHAS_FILTRO[filtros["fecha"]][1]
    condiciones, params = [], []
    if filtros["vista"] == "pendientes":
        condiciones.append("o.estado = ANY(%s)")
        params.append(OT_PENDIENTES)
    elif filtros["vista"] == "mias":
        condiciones.append("o.asignado_a = %s AND o.estado = ANY(%s)")
        params += [usuario["id"], OT_PENDIENTES]
    elif filtros["vista"] in ESTADOS_OT:
        condiciones.append("o.estado = %s")
        params.append(filtros["vista"])
    if filtros["lancha_id"]:
        condiciones.append("o.lancha_id = %s")
        params.append(filtros["lancha_id"])
    if filtros["equipo_id"]:
        condiciones.append("o.equipo_id = %s")
        params.append(filtros["equipo_id"])
    if filtros["tipo"] in ("PREVENTIVO", "CORRECTIVO"):
        condiciones.append("o.tipo = %s")
        params.append(filtros["tipo"])
    # Las fechas con hora (apertura, aprobación) se comparan por día en la hora local
    if filtros["desde"]:
        condiciones.append(f"{columna_fecha} >= %s")
        params.append(filtros["desde"])
    if filtros["hasta"]:
        condiciones.append(f"{columna_fecha} < %s")
        params.append(filtros["hasta"] + timedelta(days=1))
    where = ("WHERE " + " AND ".join(condiciones)) if condiciones else ""
    # Buscando por fechas, lo más reciente primero; si no, lo que requiere atención primero
    if por_fecha:
        orden = f"{columna_fecha} DESC NULLS LAST,"
    else:
        orden = """array_position(ARRAY['EJECUTADA', 'EN_PROCESO', 'ABIERTA', 'CERRADA', 'CANCELADA'], o.estado),
                   array_position(ARRAY['CRITICA', 'ALTA', 'MEDIA', 'BAJA'], o.prioridad),"""
    ordenes = conn.execute(
        f"""SELECT o.*, l.nombre AS lancha, CASE WHEN e.tipo = 'GENERADOR' THEN nombre_unidad(e) END AS generador,
                   ua.nombre AS asignado
              FROM ordenes o
              LEFT JOIN lanchas l ON l.id = o.lancha_id
              LEFT JOIN equipos e ON e.id = o.equipo_id
              LEFT JOIN usuarios ua ON ua.id = o.asignado_a
             {where}
             ORDER BY {orden} o.id DESC
             LIMIT %s""",
        params + [LIMITE_LISTA],
    ).fetchall()
    hoy = conn.execute("SELECT current_date AS hoy").fetchone()["hoy"]
    inicio_mes = hoy.replace(day=1)
    inicio_mes_pasado = (inicio_mes - timedelta(days=1)).replace(day=1)
    return render(request, "ordenes/lista.html", {
        "ordenes": ordenes,
        "limite": LIMITE_LISTA,
        "filtros": filtros,
        "por_fecha": por_fecha,
        "fechas_filtro": FECHAS_FILTRO,
        # Para que pestañas y atajos conserven los demás filtros
        "consulta": lambda **cambios: urlencode({k: v for k, v in (filtros | cambios).items()
                                                 if v and not (k == "fecha" and v == "ejecucion")}),
        "periodos": [("Este mes", inicio_mes, hoy),
                     ("Mes pasado", inicio_mes_pasado, inicio_mes - timedelta(days=1)),
                     ("Este año", hoy.replace(month=1, day=1), hoy)],
        "lanchas": conn.execute("SELECT id, codigo, nombre FROM lanchas ORDER BY numero").fetchall(),
        "generadores": conn.execute(
            "SELECT id, codigo, nombre FROM equipos WHERE tipo = 'GENERADOR' ORDER BY numero"
        ).fetchall(),
    })


# ---------------------------------------------------------------------------
# ODS correctiva manual (fallas detectadas fuera de la bitácora)
# ---------------------------------------------------------------------------
def _contexto_nueva(conn, valores: dict, error: str | None = None) -> dict:
    return {
        "lanchas": conn.execute("SELECT id, codigo, nombre FROM lanchas ORDER BY numero").fetchall(),
        "generadores": conn.execute(
            "SELECT id, codigo, nombre FROM equipos WHERE tipo = 'GENERADOR' AND activo ORDER BY numero"
        ).fetchall(),
        "tecnicos": _tecnicos(conn),
        "items_falla": _items_falla(conn),
        "valores": valores,
        "error": error,
    }


def _items_falla(conn, actual=None) -> list[dict]:
    """Para clasificar qué falló: los ítems de inspección (de lancha y de generador)."""
    return conn.execute(
        """SELECT i.id, i.nombre, i.aplica_a, c.nombre AS categoria FROM items_inspeccion i
             JOIN categorias c ON c.id = i.categoria_id
            WHERE i.activo OR i.id = %s
            ORDER BY i.aplica_a DESC, c.nombre, i.orden, i.nombre""",
        (actual,),
    ).fetchall()


def _tecnicos(conn) -> list[dict]:
    return conn.execute(
        """SELECT u.id, u.nombre FROM usuarios u
             JOIN usuarios_roles r ON r.usuario_id = u.id AND r.modulo = 'MANTENIMIENTO'
            WHERE u.activo ORDER BY u.nombre"""
    ).fetchall()


@router.get("/nueva")
def nueva(request: Request, conn=Depends(get_db), usuario=Depends(requiere("SUPERVISOR"))):
    valores = {"prioridad": "MEDIA"}
    if param_entero(request, "lancha_id"):
        valores["unidad"] = f"L{param_entero(request, 'lancha_id')}"
    elif param_entero(request, "equipo_id"):
        valores["unidad"] = f"G{param_entero(request, 'equipo_id')}"
    return render(request, "ordenes/nueva.html", _contexto_nueva(conn, valores))


@router.post("/nueva")
def crear(request: Request, form=Depends(formulario), conn=Depends(get_db),
          usuario=Depends(requiere("SUPERVISOR"))):
    try:
        # "unidad" llega como L<id de lancha> o G<id de generador>
        unidad = campo_texto(form, "unidad", "Lancha o generador", True)
        tipo_unidad, numero = unidad[:1], unidad[1:]
        if tipo_unidad not in ("L", "G") or not numero.isdigit():
            raise ErrorFormulario("Elige la lancha o el generador.")
        datos = {
            "lancha_id": int(numero) if tipo_unidad == "L" else None,
            "equipo_id": int(numero) if tipo_unidad == "G" else None,
            "prioridad": campo_opcion(form, "prioridad", PRIORIDADES, "Prioridad"),
            "descripcion": campo_texto(form, "descripcion", "Descripción", True),
            "item_id": campo_entero(form, "item_id"),
            "asignado_a": campo_entero(form, "asignado_a"),
        }
        with conn.transaction():
            orden_id = conn.execute(
                """INSERT INTO ordenes (lancha_id, equipo_id, tipo, prioridad, descripcion, item_id, asignado_a)
                   VALUES (%(lancha_id)s, %(equipo_id)s, 'CORRECTIVO', %(prioridad)s, %(descripcion)s, %(item_id)s,
                           %(asignado_a)s)
                   RETURNING id""",
                datos,
            ).fetchone()["id"]
    except ERRORES as e:
        return render(request, "ordenes/nueva.html", _contexto_nueva(conn, dict(form), texto_error(e)), 422)
    conn.commit()
    avisar(request, f"ODS #{orden_id} creada.")
    return redirigir(f"/ordenes/{orden_id}")


# ---------------------------------------------------------------------------
# Detalle y acciones
# ---------------------------------------------------------------------------
def _buscar(conn, orden_id: int) -> dict:
    orden = conn.execute(
        """SELECT o.*, l.nombre AS lancha, l.codigo AS codigo_lancha, e.tipo AS tipo_equipo,
                  CASE WHEN e.tipo = 'GENERADOR' THEN nombre_unidad(e) END AS generador, e.horometro_actual,
                  ua.nombre AS asignado, uc.nombre AS creado, ue.nombre AS ejecutado, ucl.nombre AS cerrado,
                  round(extract(epoch FROM o.fuera_servicio_hasta - o.fuera_servicio_desde) / 3600, 2) AS horas_fuera,
                  r.bitacora_id, r.comentario AS falla_comentario, i.nombre AS item,
                  t.actividad_id, a.nombre AS actividad, a.materiales AS materiales_sugeridos, a.tiempo_estimado_h,
                  a.descripcion AS actividad_descripcion, p.nombre AS proveedor
             FROM ordenes o
             LEFT JOIN proveedores p ON p.id = o.proveedor_id
             LEFT JOIN lanchas l ON l.id = o.lancha_id
             LEFT JOIN equipos e  ON e.id = o.equipo_id
             LEFT JOIN usuarios ua  ON ua.id  = o.asignado_a
             LEFT JOIN usuarios uc  ON uc.id  = o.creado_por
             LEFT JOIN usuarios ue  ON ue.id  = o.ejecutado_por
             LEFT JOIN usuarios ucl ON ucl.id = o.cerrado_por
             LEFT JOIN bitacora_respuestas r ON r.id = o.falla_id
             LEFT JOIN items_inspeccion i    ON i.id = o.item_id
             LEFT JOIN tareas t      ON t.id = o.tarea_id
             LEFT JOIN actividades a ON a.id = t.actividad_id
            WHERE o.id = %s""",
        (orden_id,),
    ).fetchone()
    if not orden:
        raise HTTPException(404)
    return orden


def _acciones(orden: dict, usuario: dict) -> set[str]:
    """Qué puede hacer este usuario con esta ODS en su estado actual."""
    supervisor = tiene_rol(usuario, "SUPERVISOR")
    mia = orden["asignado_a"] == usuario["id"]
    estado = orden["estado"]
    acciones = set()
    if estado == "ABIERTA" and (supervisor or orden["asignado_a"] in (None, usuario["id"])):
        acciones.add("tomar")
    if estado == "EN_PROCESO" and (supervisor or mia):
        acciones |= {"guardar", "ejecutar", "liberar", "fotos"}
    if estado == "EN_PROCESO" and supervisor:
        acciones.add("ejecutar_aprobar")
    if estado == "EJECUTADA" and supervisor:
        acciones |= {"cerrar", "devolver", "fotos"}
    if estado in OT_PENDIENTES and supervisor:
        acciones.add("cancelar")
    if estado in ("ABIERTA", "EN_PROCESO") and supervisor:
        acciones.add("asignar")
    return acciones


def _equipo_programa(conn, orden: dict) -> int | None:
    """La maquinaria cuyo programa aplica: el generador de la ODS, o el horómetro de su lancha."""
    if orden["equipo_id"]:
        return orden["equipo_id"]
    fila = conn.execute(
        "SELECT id FROM equipos WHERE lancha_id = %s AND tipo = 'PROPULSION' AND activo", (orden["lancha_id"],)
    ).fetchone()
    return fila["id"] if fila else None


def _tareas_programa(conn, equipo_id: int | None) -> list[dict]:
    if not equipo_id:
        return []
    return conn.execute(
        "SELECT * FROM v_estado_tareas WHERE equipo_id = %s AND activa ORDER BY nivel DESC, actividad", (equipo_id,)
    ).fetchall()


def _tareas_vinculadas(conn, orden_id: int) -> list[dict]:
    return conn.execute(
        """SELECT t.id, a.nombre AS actividad, t.intervalo_horas, t.intervalo_dias,
                  t.horometro_ultimo_servicio, t.proximo_horometro, t.fecha_ultimo_servicio, t.proxima_fecha
             FROM ordenes_tareas ot JOIN tareas t ON t.id = ot.tarea_id JOIN actividades a ON a.id = t.actividad_id
            WHERE ot.orden_id = %s ORDER BY a.nombre""",
        (orden_id,),
    ).fetchall()


def _demoras_guardadas(conn, orden_id: int) -> list[dict]:
    return conn.execute(
        """SELECT d.motivo_id, d.horas, m.nombre FROM ordenes_demoras d
             JOIN motivos_inoperatividad m ON m.id = d.motivo_id
            WHERE d.orden_id = %s ORDER BY d.horas DESC, m.nombre""",
        (orden_id,),
    ).fetchall()


def _motivos(conn, actuales=()) -> list[dict]:
    """Motivos activos de inoperatividad, más los que ya tiene la ODS; «Otro» al final."""
    ids = [int(a) for a in actuales if str(a).isdigit()]
    return conn.execute(
        """SELECT id, nombre FROM motivos_inoperatividad WHERE activo OR id = ANY(%s::bigint[])
            ORDER BY lower(nombre) = 'otro', nombre""",
        (ids,),
    ).fetchall()


def _proveedores(conn, actual=None) -> list[dict]:
    return conn.execute(
        "SELECT id, nombre, activo FROM proveedores WHERE activo OR id = %s ORDER BY nombre", (actual,)
    ).fetchall()


def _render_detalle(request, conn, usuario, orden_id: int, valores=None, error=None, status_code=200):
    orden = _buscar(conn, orden_id)
    historial = conn.execute(
        """SELECT h.*, u.nombre AS usuario FROM ordenes_historial h
             LEFT JOIN usuarios u ON u.id = h.usuario_id
            WHERE h.orden_id = %s ORDER BY h.fecha, h.id""",
        (orden_id,),
    ).fetchall()
    galeria = conn.execute(
        """SELECT f.*, u.nombre AS subido_nombre FROM ordenes_fotos f
             LEFT JOIN usuarios u ON u.id = f.subido_por
            WHERE f.orden_id = %s ORDER BY f.subido_en, f.id""",
        (orden_id,),
    ).fetchall()
    vinculadas = _tareas_vinculadas(conn, orden_id)
    valores = valores or {}
    previstos = materiales.de(conn, "actividad", orden["actividad_id"])
    usados = materiales.de(conn, "orden", orden_id)
    # En el formulario: lo escrito (si hubo un error), lo ya guardado o, al empezar, lo que prevé la actividad
    filas_materiales = valores.get("materiales", usados or previstos)
    demoras = _demoras_guardadas(conn, orden_id)
    filas_demoras = valores.get("demoras", demoras)
    return render(request, "ordenes/detalle.html", {
        "orden": orden,
        "historial": historial,
        "tareas_programa": _tareas_programa(conn, _equipo_programa(conn, orden)),
        "vinculadas": vinculadas,
        "vinculadas_ids": [str(t["id"]) for t in vinculadas],
        "galeria": galeria,
        "acciones": _acciones(orden, usuario),
        "tecnicos": _tecnicos(conn),
        "proveedores": _proveedores(conn, orden["proveedor_id"]),
        "materiales_previstos": previstos,
        "materiales_usados": usados,
        "filas_materiales": filas_materiales,
        "opciones_materiales": materiales.opciones(conn, [f["material_id"] for f in filas_materiales]),
        "cat_materiales": CATALOGOS["materiales"],
        "sugerencias_materiales": sugerencias(conn, CATALOGOS["materiales"]),
        "demoras": demoras,
        "filas_demoras": filas_demoras,
        "motivos": _motivos(conn, [f["motivo_id"] for f in filas_demoras]),
        "items_falla": _items_falla(conn, orden["item_id"]),
        "hoy": conn.execute("SELECT current_date AS hoy").fetchone()["hoy"],
        "valores": valores,
        "error": error,
    }, status_code)


@router.get("/{orden_id}")
def detalle(orden_id: int, request: Request, conn=Depends(get_db), usuario=Depends(usuario_actual)):
    return _render_detalle(request, conn, usuario, orden_id)


# ---------------------------------------------------------------------------
# Fotos (solo con sesión iniciada; no son archivos públicos)
# ---------------------------------------------------------------------------
def _buscar_foto(conn, orden_id: int, foto_id: int) -> dict:
    foto = conn.execute(
        "SELECT * FROM ordenes_fotos WHERE id = %s AND orden_id = %s", (foto_id, orden_id)
    ).fetchone()
    if not foto:
        raise HTTPException(404)
    return foto


@router.get("/{orden_id}/fotos/{foto_id}")
def ver_foto(orden_id: int, foto_id: int, request: Request, mini: bool = False, conn=Depends(get_db),
             usuario=Depends(usuario_actual)):
    foto = _buscar_foto(conn, orden_id, foto_id)
    destino = fotos.ruta(foto["archivo"], miniatura=mini)
    if not destino.exists():
        raise HTTPException(404)
    return FileResponse(destino, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.post("/{orden_id}/fotos/{foto_id}/eliminar")
def eliminar_foto(orden_id: int, foto_id: int, request: Request, form=Depends(formulario), conn=Depends(get_db),
                  usuario=Depends(requiere("MECANICO", "SUPERVISOR"))):
    orden = _buscar(conn, orden_id)
    foto = _buscar_foto(conn, orden_id, foto_id)
    if "fotos" not in _acciones(orden, usuario) or not (
            tiene_rol(usuario, "SUPERVISOR") or foto["subido_por"] == usuario["id"]):
        raise HTTPException(403, "No puedes quitar esta foto.")
    try:
        with conn.transaction():
            conn.execute("DELETE FROM ordenes_fotos WHERE id = %s", (foto_id,))
    except ERRORES as e:
        avisar(request, texto_error(e), "danger")
        return redirigir(f"/ordenes/{orden_id}")
    conn.commit()
    fotos.eliminar(foto["archivo"])
    avisar(request, "Foto eliminada.")
    return redirigir(f"/ordenes/{orden_id}#fotos")


# ---------------------------------------------------------------------------
# Acciones
# ---------------------------------------------------------------------------
def _cambiar(conn, orden_id: int, desde: list[str], cambios: dict, comentario: str | None = None) -> None:
    """UPDATE condicionado al estado esperado: si otro usuario la movió antes, no se pisa."""
    asignaciones = ", ".join(f"{columna} = %({columna})s" for columna in cambios)
    with conn.transaction():
        if comentario:
            conn.execute("SELECT set_config('app.comentario', %s, true)", (comentario,))
        cursor = conn.execute(
            f"UPDATE ordenes SET {asignaciones} WHERE id = %(id)s AND estado = ANY(%(desde)s)",
            cambios | {"id": orden_id, "desde": desde},
        )
        if cursor.rowcount == 0:
            raise ErrorFormulario("La ODS cambió de estado mientras la veías. Revisa y vuelve a intentar.")


def _datos_programa(conn, form, orden: dict, para_reportar: bool) -> tuple[dict, list[int]]:
    """¿El servicio cumple tareas del programa de la maquinaria? Devuelve (cambios, tareas)."""
    equipo_programa = _equipo_programa(conn, orden)
    validas = {t["tarea_id"] for t in _tareas_programa(conn, equipo_programa)}
    marcadas = {int(t) for t in form.getlist("tarea_id") if str(t).isdigit()}
    if marcadas - validas:
        raise ErrorFormulario("Alguna tarea marcada no es del programa de esta maquinaria.")
    if orden["tipo"] == "PREVENTIVO":
        return {}, sorted(marcadas | {orden["tarea_id"]})   # su propia tarea siempre cuenta

    programa = form.get("programa") or None
    if programa not in (None, "SI", "NO"):
        raise ErrorFormulario("Respuesta no válida sobre el programa de mantenimiento.")
    if para_reportar and not programa:
        raise ErrorFormulario("Indica si el servicio corresponde al programa de mantenimiento.")
    if programa == "SI" and para_reportar and not marcadas:
        raise ErrorFormulario("Marca qué tareas del programa se cumplieron con este servicio.")
    tareas = sorted(marcadas) if programa == "SI" else []
    cambios = {"pertenece_programa": None if programa is None else programa == "SI"}
    # Una ODS de lancha que cumple tareas del programa se registra contra el horómetro de la lancha
    if orden["tipo_equipo"] != "GENERADOR":
        cambios["equipo_id"] = equipo_programa if tareas else None
    return cambios, tareas


def _datos_tiempos(form, para_reportar: bool) -> dict:
    """Fuera de servicio (desde / hasta) y duración del trabajo. «Volvió a operar» puede quedar vacío
    mientras la maquinaria siga parada; al reportar todo es obligatorio."""
    fuera = form.get("fuera_servicio") or None
    if fuera not in (None, "SI", "NO"):
        raise ErrorFormulario("Respuesta no válida sobre si quedó fuera de servicio.")
    if para_reportar and not fuera:
        raise ErrorFormulario("Indica si la maquinaria quedó fuera de servicio por esta falla o servicio.")
    fuera_si = fuera == "SI"
    return {
        "fuera_servicio": None if fuera is None else fuera_si,
        "fuera_servicio_desde": campo_fechahora(form, "fuera_servicio_desde", "Fuera de servicio desde",
                                                para_reportar) if fuera_si else None,
        "fuera_servicio_hasta": campo_fechahora(form, "fuera_servicio_hasta", "Volvió a operar",
                                                para_reportar) if fuera_si else None,
        "horas_reparacion": campo_numero(form, "horas_reparacion", "Duración del trabajo", para_reportar),
    }


def _demoras(form, fuera_servicio: bool) -> list[dict]:
    """Factores externos: motivo y horas de cada uno (solo si quedó fuera de servicio)."""
    if not fuera_servicio:
        return []
    filas, vistos = [], set()
    for motivo, horas in campo_filas(form, "demora_motivo", "demora_horas"):
        if not motivo:
            raise ErrorFormulario("Elige el motivo de cada fila de los factores externos (o quita la fila).")
        if not motivo.isdigit():
            raise ErrorFormulario("Hay un motivo no válido en los factores externos.")
        if motivo in vistos:
            raise ErrorFormulario("Hay un motivo repetido en los factores externos: deja una sola fila por motivo.")
        vistos.add(motivo)
        if not horas:
            raise ErrorFormulario("Indica las horas de cada motivo de los factores externos.")
        numero = leer_numero(horas, "Horas")
        if numero <= 0:
            raise ErrorFormulario("Las horas de cada motivo deben ser mayores que cero.")
        filas.append({"motivo_id": int(motivo), "horas": numero})
    return filas


def _guardar_demoras(conn, orden_id: int, filas: list[dict]) -> None:
    conn.execute("DELETE FROM ordenes_demoras WHERE orden_id = %s", (orden_id,))
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO ordenes_demoras (orden_id, motivo_id, horas) VALUES (%s, %s, %s)",
                        [(orden_id, f["motivo_id"], f["horas"]) for f in filas])


def _datos_servicio(form, orden: dict, para_reportar: bool, con_horometro: bool) -> dict:
    """Lo que el mecánico registra del servicio. Al reportar, lo esencial es obligatorio."""
    servicio = form.get("servicio") or None
    if servicio not in (None, "PROPIO", "CONTRATADO"):
        raise ErrorFormulario("Tipo de servicio no válido.")
    if para_reportar and not servicio:
        raise ErrorFormulario("Indica si el servicio lo hicieron los mecánicos de la empresa o fue contratado.")
    contratado = None if servicio is None else servicio == "CONTRATADO"
    proveedor_id = campo_entero(form, "proveedor_id", "Proveedor", requerido=bool(contratado)) if contratado else None
    # Qué falló: las de la bitácora ya lo saben; en las manuales se puede indicar (o corregir) al reportar
    clasificacion = {}
    if orden["tipo"] == "CORRECTIVO" and not orden["falla_id"] and "item_id" in form:
        clasificacion["item_id"] = campo_entero(form, "item_id")
    return clasificacion | _datos_tiempos(form, para_reportar) | {
        "servicio_contratado": contratado,
        "proveedor_id": proveedor_id,
        "diagnostico": campo_texto(form, "diagnostico"),
        "servicio_requerido": campo_texto(form, "servicio_requerido"),
        "trabajo_realizado": campo_texto(form, "trabajo_realizado",
                                         "Descripción del servicio de mantenimiento realizado", para_reportar),
        "materiales_usados": campo_texto(form, "materiales_usados"),
        "observaciones": campo_texto(form, "observaciones"),
        "fecha_ejecucion": campo_fecha(form, "fecha_ejecucion", "Fecha de ejecución", para_reportar),
        "horometro_ejecucion": campo_numero(form, "horometro_ejecucion", "Horómetro",
                                            requerido=para_reportar and con_horometro),
        "horas_hombre": campo_numero(form, "horas_hombre", "Horas-hombre"),
    }


@router.post("/{orden_id}/{accion}")
def accion(orden_id: int, accion: str, request: Request, form=Depends(formulario), conn=Depends(get_db),
           usuario=Depends(requiere("MECANICO", "SUPERVISOR"))):
    orden = _buscar(conn, orden_id)
    if accion not in _acciones(orden, usuario):
        raise HTTPException(403, "Esta acción no está disponible para ti en el estado actual de la ODS.")
    escritas = []   # fotos ya guardadas en disco (se borran si algo falla)
    try:
        if accion == "tomar":
            _cambiar(conn, orden_id, ["ABIERTA"],
                     {"estado": "EN_PROCESO", "asignado_a": orden["asignado_a"] or usuario["id"]})
            mensaje = "Trabajo iniciado."
        elif accion == "liberar":
            _cambiar(conn, orden_id, ["EN_PROCESO"], {"estado": "ABIERTA", "asignado_a": None},
                     campo_texto(form, "comentario"))
            mensaje = "La ODS volvió a quedar disponible."
        elif accion == "asignar":
            tecnico = campo_entero(form, "asignado_a")
            _cambiar(conn, orden_id, ["ABIERTA", "EN_PROCESO"], {"asignado_a": tecnico})
            mensaje = "Asignación actualizada."
        elif accion in ("guardar", "ejecutar", "ejecutar_aprobar", "fotos"):
            reportar = accion != "guardar" and accion != "fotos"
            tareas = filas_materiales = demoras = None
            cambios = {}
            if accion != "fotos":
                cambios, tareas = _datos_programa(conn, form, orden, para_reportar=reportar)
                con_horometro = bool(cambios.get("equipo_id", orden["equipo_id"]))
                cambios |= _datos_servicio(form, orden, para_reportar=reportar, con_horometro=con_horometro)
                filas_materiales = materiales.leer(form)
                demoras = _demoras(form, cambios["fuera_servicio"] is True)
            for archivo in fotos.subidas(form):
                escritas.append(fotos.guardar(orden_id, archivo.file.read(), archivo.filename))
            with conn.transaction():
                if tareas is not None:
                    conn.execute("DELETE FROM ordenes_tareas WHERE orden_id = %s AND NOT tarea_id = ANY(%s)",
                                 (orden_id, tareas))
                    with conn.cursor() as cur:
                        cur.executemany(
                            "INSERT INTO ordenes_tareas (orden_id, tarea_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                            [(orden_id, t) for t in tareas],
                        )
                # Antes del cambio de estado: al reportar, la base revisa que los motivos cuadren con los tiempos
                if filas_materiales is not None:
                    materiales.guardar(conn, "orden", orden_id, filas_materiales)
                    _guardar_demoras(conn, orden_id, demoras)
                if reportar:
                    _cambiar(conn, orden_id, ["EN_PROCESO"], cambios | {"estado": "EJECUTADA"})
                elif cambios:
                    _cambiar(conn, orden_id, ["EN_PROCESO"], cambios)
                with conn.cursor() as cur:
                    cur.executemany(
                        """INSERT INTO ordenes_fotos (orden_id, archivo, nombre_original, ancho, alto, bytes)
                           VALUES (%(orden_id)s, %(archivo)s, %(nombre_original)s, %(ancho)s, %(alto)s, %(bytes)s)""",
                        [d | {"orden_id": orden_id} for d in escritas],
                    )
                if accion == "ejecutar_aprobar":
                    _cambiar(conn, orden_id, ["EJECUTADA"], {"estado": "CERRADA"},
                             "Reportada y aprobada por el supervisor de mantenimiento")
            fotos_txt = f" ({len(escritas)} foto(s) agregada(s))" if escritas else ""
            mensaje = {
                "guardar": "Avance guardado.",
                "fotos": "Fotos agregadas.",
                "ejecutar": "Servicio reportado. Queda pendiente de aprobación del supervisor de mantenimiento.",
                "ejecutar_aprobar": "Servicio reportado y aprobado. ODS cerrada.",
            }[accion] + fotos_txt
        elif accion == "cerrar":
            _cambiar(conn, orden_id, ["EJECUTADA"], {"estado": "CERRADA"},
                     campo_texto(form, "comentario") or "Aprobada")
            reprogramadas = len(_tareas_vinculadas(conn, orden_id))
            mensaje = "ODS aprobada y cerrada." + (
                f" Se reprogramaron {reprogramadas} tarea(s) del programa." if reprogramadas else "")
        elif accion == "devolver":
            motivo = campo_texto(form, "comentario", "Motivo de la devolución", True)
            _cambiar(conn, orden_id, ["EJECUTADA"], {"estado": "EN_PROCESO"}, motivo)
            mensaje = "ODS devuelta al mecánico."
        elif accion == "cancelar":
            motivo = campo_texto(form, "comentario", "Motivo de la cancelación", True)
            _cambiar(conn, orden_id, OT_PENDIENTES, {"estado": "CANCELADA"}, motivo)
            mensaje = "ODS cancelada."
        else:
            raise HTTPException(404)
    except ERRORES as e:
        for dato in escritas:
            fotos.eliminar(dato["archivo"])
        valores = {k: v for k, v in form.items() if isinstance(v, str)} | {
            "accion": accion, "tarea_id": form.getlist("tarea_id"), "materiales": materiales.del_formulario(form),
            "demoras": [{"motivo_id": m, "horas": h} for m, h in campo_filas(form, "demora_motivo", "demora_horas")]}
        return _render_detalle(request, conn, usuario, orden_id, valores, texto_error(e), 422)
    conn.commit()
    avisar(request, mensaje)
    return redirigir(f"/ordenes/{orden_id}")
