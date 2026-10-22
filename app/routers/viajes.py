"""Operaciones: reportes de viaje (formato ATM-RG-OP-002 rev. 04).

El supervisor de operaciones crea el viaje con la lancha, el patrón y el encabezado. El patrón
registra el trayecto con botones (Zarpé, Llegué, Inicio y Fin de espera; la hora la pone el
sistema) y lo finaliza; el supervisor lo revisa y lo cierra. Antes del cierre, el patrón y el
supervisor pueden corregir horas; después, solo el supervisor, con una enmienda que exige motivo
y queda registrada. Las reglas viven en la base (migración 011); aquí se arma cada paso.
"""
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import catalogos
from ..db import get_db
from ..seguridad import ROLES_OPERACIONES, formulario, requiere, tiene_rol, usuario_actual
from ..web import (ERRORES, ErrorFormulario, avisar, campo_entero, campo_fecha, campo_fechahora, campo_texto,
                   param_entero, param_fecha, redirigir, render, texto_error)

# Solo para los roles de Operaciones
router = APIRouter(prefix="/viajes", dependencies=[Depends(requiere(*ROLES_OPERACIONES))])

ABIERTOS = ["BORRADOR", "EN_CURSO", "FINALIZADO"]
LIMITE_LISTA = 300


# ---------------------------------------------------------------------------
# Lectura del viaje y su trayecto (también la usa el tablero de Operaciones)
# ---------------------------------------------------------------------------
def buscar(conn, viaje_id: int) -> dict:
    viaje = conn.execute(
        """SELECT v.*, uo.nombre AS origen_nombre, uc.nombre AS cerrado_por_nombre,
                  b.imo, b.bandera, b.tipo AS tipo_buque, b.arqueo_bruto, b.eslora, b.manga
             FROM v_viajes v
             LEFT JOIN ubicaciones uo ON uo.id = v.origen_id
             LEFT JOIN usuarios uc ON uc.id = v.cerrado_por
             LEFT JOIN buques b ON b.id = v.buque_id
            WHERE v.id = %s""",
        (viaje_id,),
    ).fetchone()
    if not viaje:
        raise HTTPException(404)
    return viaje


def _nombre_punto(ubicacion: str | None, a_bordo: bool, buque: str | None) -> str | None:
    return f"A bordo del {buque or 'buque'}" if a_bordo else ubicacion


def trayecto(conn, viaje: dict) -> dict:
    """Tramos con su origen (el destino del anterior), esperas y la situación actual."""
    tramos = conn.execute(
        """SELECT t.*, u.nombre AS destino_nombre FROM viajes_tramos t
             LEFT JOIN ubicaciones u ON u.id = t.destino_id
            WHERE t.viaje_id = %s ORDER BY t.salida, t.id""",
        (viaje["id"],),
    ).fetchall()
    esperas = conn.execute(
        "SELECT * FROM viajes_esperas WHERE viaje_id = %s ORDER BY inicio, id", (viaje["id"],)
    ).fetchall()
    # Dónde está la lancha: (nombre de la ubicación, ¿a bordo del buque?)
    lugar = (viaje["origen_nombre"], viaje["origen_buque"]) if viaje["origen_id"] or viaje["origen_buque"] else None
    for t in tramos:
        t["origen"] = _nombre_punto(*lugar, viaje["buque"]) if lugar else None
        t["destino"] = (_nombre_punto(t["destino_nombre"], t["destino_buque"], viaje["buque"])
                        if t["destino_id"] or t["destino_buque"] else None)
        t["punto"] = "BUQUE" if t["destino_buque"] else (f"U{t['destino_id']}" if t["destino_id"] else "")
        t["duracion"] = t["llegada"] - t["salida"] if t["llegada"] else None
        if t["llegada"]:
            lugar = (t["destino_nombre"], t["destino_buque"])
    for e in esperas:
        e["duracion"] = e["fin"] - e["inicio"] if e["fin"] else None
    navegando = next((t for t in tramos if t["llegada"] is None), None)
    esperando = next((e for e in esperas if e["fin"] is None), None)
    buque = viaje["buque"] or "buque"
    en = (f"a bordo del {buque}" if lugar[1] else f"en {lugar[0]}") if lugar else ""
    if navegando:
        hacia = ("el " + buque if navegando["destino_buque"] else navegando["destino_nombre"]) if navegando["punto"] else None
        situacion = {"tipo": "NAVEGANDO", "desde": navegando["salida"],
                     "texto": f"Navegando hacia {hacia}" if hacia else "Navegando (destino por confirmar)",
                     "detalle": (f"Salió del {buque}" if lugar[1] else f"Salió de {lugar[0]}") if lugar else ""}
    elif esperando:
        situacion = {"tipo": "ESPERA", "desde": esperando["inicio"], "texto": f"En espera {en}".strip(),
                     "detalle": esperando["motivo"] or ""}
    elif tramos:
        situacion = {"tipo": "DETENIDO", "desde": tramos[-1]["llegada"], "texto": en[:1].upper() + en[1:], "detalle": ""}
    else:
        situacion = {"tipo": "SIN_ZARPAR", "desde": None, "texto": "Todavía no ha zarpado", "detalle": ""}
    ubicacion = _nombre_punto(*lugar, viaje["buque"]) if lugar else None
    return {"tramos": tramos, "esperas": esperas, "navegando": navegando, "esperando": esperando,
            "ubicacion": ubicacion, "situacion": situacion}


def ultimas_ubicaciones(conn) -> dict:
    """Dónde quedó cada lancha al terminar su último tramo: {lancha_id: {texto, ubicacion_id, llegada}}."""
    filas = conn.execute(
        """SELECT DISTINCT ON (v.lancha_id) v.lancha_id, t.destino_id, t.destino_buque, t.llegada,
                  u.nombre AS ubicacion, b.nombre AS buque
             FROM viajes_tramos t JOIN viajes v ON v.id = t.viaje_id
             LEFT JOIN ubicaciones u ON u.id = t.destino_id LEFT JOIN buques b ON b.id = v.buque_id
            WHERE t.llegada IS NOT NULL AND v.estado <> 'CANCELADO'
            ORDER BY v.lancha_id, t.llegada DESC"""
    ).fetchall()
    return {f["lancha_id"]: {"texto": _nombre_punto(f["ubicacion"], f["destino_buque"], f["buque"]),
                             "ubicacion_id": f["destino_id"], "llegada": f["llegada"]} for f in filas}


def es_supervisor(usuario: dict) -> bool:
    return tiene_rol(usuario, "SUPERVISOR_OPS")


def puede_operar(usuario: dict, viaje: dict) -> bool:
    """Registrar eventos del trayecto: el patrón del viaje o el supervisor, antes de finalizar."""
    return viaje["estado"] in ("BORRADOR", "EN_CURSO") and (es_supervisor(usuario) or viaje["patron_id"] == usuario["id"])


def puede_editar(usuario: dict, viaje: dict) -> bool:
    """Antes del cierre: el supervisor y el patrón del viaje. Cerrado: solo el supervisor (enmienda)."""
    if viaje["estado"] == "CANCELADO":
        return False
    if viaje["estado"] == "CERRADO":
        return es_supervisor(usuario)
    return es_supervisor(usuario) or viaje["patron_id"] == usuario["id"]


# ---------------------------------------------------------------------------
# Lectura de formularios
# ---------------------------------------------------------------------------
def _exigir_edicion(conn, usuario: dict, viaje: dict, form) -> None:
    """Dentro de la transacción: permiso y, si el reporte está cerrado, el motivo de la enmienda."""
    if not puede_editar(usuario, viaje):
        raise HTTPException(403, "No puedes modificar este reporte.")
    if viaje["estado"] == "CERRADO":
        motivo = campo_texto(form, "motivo_enmienda", "Motivo de la enmienda", True)
        conn.execute("SELECT set_config('app.enmienda', %s, true)", (motivo,))


def _exigir_operacion(usuario: dict, viaje: dict) -> None:
    if not puede_operar(usuario, viaje):
        if viaje["estado"] not in ("BORRADOR", "EN_CURSO"):
            raise ErrorFormulario("El viaje ya terminó: para cambiar algo, corrige el trayecto.")
        raise HTTPException(403, "Solo el patrón del viaje o el supervisor registran el trayecto.")


def _punto(form, nombre: str, etiqueta: str, requerido: bool = True) -> tuple[int | None, bool]:
    """Un punto del trayecto: «U<id>» de una ubicación o «BUQUE» (a bordo del buque cliente)."""
    valor = form.get(nombre) or ""
    if valor == "BUQUE":
        return None, True
    if valor[:1] == "U" and valor[1:].isdigit():
        return int(valor[1:]), False
    if requerido:
        raise ErrorFormulario(f"Indica {etiqueta}.")
    return None, False


def _hora(conn, form, nombre: str, etiqueta: str, requerido: bool = True) -> datetime | None:
    valor = campo_fechahora(form, nombre, etiqueta, requerido)
    ahora = conn.execute("SELECT localtimestamp AS ahora").fetchone()["ahora"]
    if valor and valor > ahora + timedelta(minutes=10):
        raise ErrorFormulario(f"«{etiqueta}» no puede ser una hora futura.")
    return valor


def _ids(form, nombre: str) -> list[int]:
    return sorted({int(v) for v in form.getlist(nombre) if str(v).isdigit()})


def _datos_encabezado(form, supervisor: bool, viaje: dict | None) -> tuple[dict, dict]:
    datos = {
        "requisicion": campo_texto(form, "requisicion"),
        "cliente_id": campo_entero(form, "cliente_id"),
        "solicitado_por": campo_texto(form, "solicitado_por"),
        "buque_id": campo_entero(form, "buque_id"),
        "servicio_otro": campo_texto(form, "servicio_otro"),
        "descripcion": campo_texto(form, "descripcion"),
    }
    if supervisor:
        datos["fecha"] = campo_fecha(form, "fecha", "Fecha", True)
        # La lancha y el patrón solo cambian antes del primer zarpe (o por enmienda)
        if viaje is None or viaje["estado"] in ("BORRADOR", "CERRADO"):
            datos["lancha_id"] = campo_entero(form, "lancha_id", "Lancha", True)
            datos["patron_id"] = campo_entero(form, "patron_id", "Patrón", True)
    listas = {"servicios": _ids(form, "servicios"), "areas": _ids(form, "areas"), "marinos": _ids(form, "marinos")}
    return datos, listas


_LISTAS = (("viajes_servicios", "tipo_servicio_id", "servicios"), ("viajes_areas", "ubicacion_id", "areas"),
           ("viajes_marinos", "marino_id", "marinos"))


def _sincronizar_listas(conn, viaje_id: int, listas: dict) -> None:
    """Solo agrega y quita lo que cambió (así una enmienda registra lo justo)."""
    for tabla, columna, clave in _LISTAS:
        conn.execute(f"DELETE FROM {tabla} WHERE viaje_id = %s AND NOT {columna} = ANY(%s::bigint[])",
                     (viaje_id, listas[clave]))
        conn.execute(f"""INSERT INTO {tabla} (viaje_id, {columna}) SELECT %s, unnest(%s::bigint[])
                          ON CONFLICT DO NOTHING""", (viaje_id, listas[clave]))


def _listas_del_viaje(conn, viaje_id: int) -> dict:
    return {clave: [f["id"] for f in conn.execute(f"SELECT {columna} AS id FROM {tabla} WHERE viaje_id = %s",
                                                    (viaje_id,))]
            for tabla, columna, clave in _LISTAS}


# ---------------------------------------------------------------------------
# Pantallas
# ---------------------------------------------------------------------------
def _contexto_encabezado(conn, usuario: dict, valores: dict, viaje: dict | None = None, error: str | None = None) -> dict:
    cat = catalogos.CATALOGOS
    return {
        "valores": valores, "viaje": viaje, "error": error, "supervisor": es_supervisor(usuario),
        "lanchas": conn.execute(
            """SELECT l.id, l.nombre, l.codigo, l.estado, lancha_falla_critica(l.id) AS falla_critica,
                      (SELECT codigo FROM viajes v WHERE v.lancha_id = l.id AND v.estado = 'EN_CURSO') AS en_viaje
                 FROM lanchas l WHERE l.estado = 'OPERATIVA' OR l.id = %s ORDER BY l.numero""",
            (valores.get("lancha_id") if str(valores.get("lancha_id") or "").isdigit() else None,),
        ).fetchall(),
        "patrones": conn.execute(
            """SELECT u.id, u.nombre,
                      (SELECT codigo FROM viajes v WHERE v.patron_id = u.id AND v.estado = 'EN_CURSO') AS en_viaje
                 FROM usuarios u
                 JOIN usuarios_roles r ON r.usuario_id = u.id AND r.modulo = 'OPERACIONES' AND r.rol = 'PATRON'
                WHERE u.activo ORDER BY u.nombre"""
        ).fetchall(),
        "clientes": catalogos.opciones(conn, cat["clientes"], valores.get("cliente_id")),
        "buques": catalogos.opciones(conn, cat["buques"], valores.get("buque_id")),
        "tipos_servicio": catalogos.opciones(conn, cat["tipos-servicio"], valores.get("servicios", [])),
        "ubicaciones": catalogos.opciones(conn, cat["ubicaciones"], valores.get("areas", [])),
        "marinos": catalogos.opciones(conn, cat["marinos"], valores.get("marinos", [])),
        "cat_buques": cat["buques"], "cat_clientes": cat["clientes"],
        "sugerencias": catalogos.sugerencias(conn, cat["buques"]),
    }


def _valores_desde_form(form) -> dict:
    return dict(form) | {"servicios": _ids(form, "servicios"), "areas": _ids(form, "areas"),
                         "marinos": _ids(form, "marinos")}


@router.get("")
def lista(request: Request, conn=Depends(get_db), usuario=Depends(usuario_actual)):
    supervisor = es_supervisor(usuario)
    filtros = {
        "vista": request.query_params.get("vista") or ("activos" if supervisor else "mios"),
        "lancha_id": param_entero(request, "lancha_id"),
        "desde": param_fecha(request, "desde"),
        "hasta": param_fecha(request, "hasta"),
    }
    condiciones, params = [], []
    if filtros["vista"] == "activos":
        condiciones.append("v.estado = ANY(%s)")
        params.append(ABIERTOS)
    elif filtros["vista"] == "mios":
        condiciones.append("v.patron_id = %s")
        params.append(usuario["id"])
    elif filtros["vista"] in ("BORRADOR", "EN_CURSO", "FINALIZADO", "CERRADO", "CANCELADO"):
        condiciones.append("v.estado = %s")
        params.append(filtros["vista"])
    if filtros["lancha_id"]:
        condiciones.append("v.lancha_id = %s")
        params.append(filtros["lancha_id"])
    if filtros["desde"]:
        condiciones.append("v.fecha >= %s")
        params.append(filtros["desde"])
    if filtros["hasta"]:
        condiciones.append("v.fecha <= %s")
        params.append(filtros["hasta"])
    where = ("WHERE " + " AND ".join(condiciones)) if condiciones else ""
    viajes = conn.execute(
        f"SELECT v.* FROM v_viajes v {where} ORDER BY v.fecha DESC, v.numero DESC LIMIT %s", params + [LIMITE_LISTA]
    ).fetchall()
    return render(request, "viajes/lista.html", {
        "viajes": viajes, "filtros": filtros, "supervisor": supervisor, "limite": LIMITE_LISTA,
        "lanchas": conn.execute("SELECT id, nombre FROM lanchas ORDER BY numero").fetchall(),
    })


@router.get("/nuevo")
def nuevo(request: Request, conn=Depends(get_db), usuario=Depends(requiere("SUPERVISOR_OPS"))):
    hoy = conn.execute("SELECT current_date AS hoy").fetchone()["hoy"]
    valores = {"fecha": hoy, "lancha_id": param_entero(request, "lancha_id"), "servicios": [], "areas": [], "marinos": []}
    return render(request, "viajes/form.html", _contexto_encabezado(conn, usuario, valores))


@router.post("/nuevo")
def crear(request: Request, form=Depends(formulario), conn=Depends(get_db), usuario=Depends(requiere("SUPERVISOR_OPS"))):
    try:
        datos, listas = _datos_encabezado(form, True, None)
        with conn.transaction():
            viaje = conn.execute(
                """INSERT INTO viajes (fecha, lancha_id, patron_id, requisicion, cliente_id, solicitado_por, buque_id,
                                       servicio_otro, descripcion)
                   VALUES (%(fecha)s, %(lancha_id)s, %(patron_id)s, %(requisicion)s, %(cliente_id)s, %(solicitado_por)s,
                           %(buque_id)s, %(servicio_otro)s, %(descripcion)s)
                   RETURNING id, codigo, lancha_falla_critica(lancha_id) AS falla_critica""",
                datos,
            ).fetchone()
            _sincronizar_listas(conn, viaje["id"], listas)
    except ERRORES as e:
        return render(request, "viajes/form.html",
                      _contexto_encabezado(conn, usuario, _valores_desde_form(form), error=texto_error(e)), 422)
    conn.commit()
    avisar(request, f"Reporte {viaje['codigo']} creado. El patrón ya lo ve en sus viajes.")
    if viaje["falla_critica"]:
        avisar(request, "Ojo: la lancha tiene una falla crítica pendiente en Mantenimiento.", "warning")
    return redirigir(f"/viajes/{viaje['id']}")


def _render_detalle(request: Request, conn, usuario: dict, viaje: dict, error: str | None = None, status_code: int = 200):
    t = trayecto(conn, viaje)
    ultimas = ultimas_ubicaciones(conn) if viaje["estado"] in ("BORRADOR", "EN_CURSO") and not t["tramos"] else {}
    return render(request, "viajes/detalle.html", {
        "viaje": viaje, "t": t, "error": error,
        "supervisor": es_supervisor(usuario),
        "puede_operar": puede_operar(usuario, viaje),
        "puede_editar": puede_editar(usuario, viaje),
        # Las activas, más las que ya usa el trayecto aunque se hayan desactivado
        "ubicaciones": catalogos.opciones(conn, catalogos.CATALOGOS["ubicaciones"],
                                          [viaje["origen_id"]] + [x["destino_id"] for x in t["tramos"]]),
        "origen_sugerido": (ultimas.get(viaje["lancha_id"]) or {}).get("ubicacion_id"),
        "servicios": conn.execute(
            """SELECT s.nombre FROM viajes_servicios vs JOIN tipos_servicio s ON s.id = vs.tipo_servicio_id
                WHERE vs.viaje_id = %s ORDER BY s.nombre""", (viaje["id"],)).fetchall(),
        "areas": conn.execute(
            """SELECT u.nombre, u.tipo FROM viajes_areas va JOIN ubicaciones u ON u.id = va.ubicacion_id
                WHERE va.viaje_id = %s ORDER BY u.tipo, u.nombre""", (viaje["id"],)).fetchall(),
        "marinos": conn.execute(
            """SELECT m.nombre FROM viajes_marinos vm JOIN marinos m ON m.id = vm.marino_id
                WHERE vm.viaje_id = %s ORDER BY m.nombre""", (viaje["id"],)).fetchall(),
        "falla_critica": conn.execute("SELECT lancha_falla_critica(%s) AS f", (viaje["lancha_id"],)).fetchone()["f"],
        "enmiendas": _enmiendas(conn, viaje["id"]),
    }, status_code)


@router.get("/{viaje_id}")
def detalle(viaje_id: int, request: Request, conn=Depends(get_db), usuario=Depends(usuario_actual)):
    return _render_detalle(request, conn, usuario, buscar(conn, viaje_id))


@router.get("/{viaje_id}/editar")
def editar(viaje_id: int, request: Request, conn=Depends(get_db), usuario=Depends(usuario_actual)):
    viaje = buscar(conn, viaje_id)
    if not puede_editar(usuario, viaje):
        raise HTTPException(403, "No puedes modificar este reporte.")
    valores = dict(viaje) | _listas_del_viaje(conn, viaje_id)
    return render(request, "viajes/form.html", _contexto_encabezado(conn, usuario, valores, viaje))


@router.post("/{viaje_id}/editar")
def actualizar(viaje_id: int, request: Request, form=Depends(formulario), conn=Depends(get_db),
               usuario=Depends(usuario_actual)):
    viaje = buscar(conn, viaje_id)
    try:
        datos, listas = _datos_encabezado(form, es_supervisor(usuario), viaje)
        with conn.transaction():
            _exigir_edicion(conn, usuario, viaje, form)
            conn.execute(f"UPDATE viajes SET {', '.join(f'{c} = %({c})s' for c in datos)} WHERE id = %(id_)s",
                         datos | {"id_": viaje_id})
            _sincronizar_listas(conn, viaje_id, listas)
    except ERRORES as e:
        return render(request, "viajes/form.html",
                      _contexto_encabezado(conn, usuario, dict(viaje) | _valores_desde_form(form), viaje, texto_error(e)), 422)
    conn.commit()
    avisar(request, "Enmienda registrada." if viaje["estado"] == "CERRADO" else "Reporte actualizado.")
    return redirigir(f"/viajes/{viaje_id}")


def _accion(request: Request, conn, usuario: dict, viaje_id: int, hacer, mensaje: str):
    """Corre un paso dentro de una transacción; si la base lo rechaza, vuelve al detalle con el motivo."""
    viaje = buscar(conn, viaje_id)
    try:
        with conn.transaction():
            texto = hacer(viaje)
    except ERRORES as e:
        return _render_detalle(request, conn, usuario, viaje, texto_error(e), 422)
    conn.commit()
    avisar(request, texto or mensaje)
    return redirigir(f"/viajes/{viaje_id}")


def _hora_actual(conn) -> str:
    return conn.execute("SELECT to_char(now(), 'HH24:MI') AS h").fetchone()["h"]


# --- Botones del patrón ------------------------------------------------------
@router.post("/{viaje_id}/zarpe")
def zarpe(viaje_id: int, request: Request, form=Depends(formulario), conn=Depends(get_db),
          usuario=Depends(usuario_actual)):
    def hacer(viaje):
        _exigir_operacion(usuario, viaje)
        t = trayecto(conn, viaje)
        if t["navegando"]:
            raise ErrorFormulario("La lancha ya está navegando: registra la llegada antes de otro zarpe.")
        if t["esperando"]:
            raise ErrorFormulario("Hay una espera en curso: registra su fin antes de zarpar.")
        if not t["tramos"]:
            origen_id, origen_buque = _punto(form, "origen", "de dónde zarpa la lancha")
            conn.execute("UPDATE viajes SET origen_id = %s, origen_buque = %s WHERE id = %s",
                         (origen_id, origen_buque, viaje["id"]))
        rumbo_id, rumbo_buque = _punto(form, "rumbo", "el rumbo", requerido=False)
        conn.execute("""INSERT INTO viajes_tramos (viaje_id, salida, destino_id, destino_buque)
                        VALUES (%s, now(), %s, %s)""", (viaje["id"], rumbo_id, rumbo_buque))
        return f"Zarpe registrado a las {_hora_actual(conn)}."
    return _accion(request, conn, usuario, viaje_id, hacer, "")


@router.post("/{viaje_id}/llegada")
def llegada(viaje_id: int, request: Request, form=Depends(formulario), conn=Depends(get_db),
            usuario=Depends(usuario_actual)):
    def hacer(viaje):
        _exigir_operacion(usuario, viaje)
        destino_id, destino_buque = _punto(form, "destino", "a dónde llegó la lancha")
        fila = conn.execute(
            """UPDATE viajes_tramos SET destino_id = %s, destino_buque = %s, llegada = now()
                WHERE viaje_id = %s AND llegada IS NULL RETURNING id""",
            (destino_id, destino_buque, viaje["id"]),
        ).fetchone()
        if not fila:
            raise ErrorFormulario("La lancha no está navegando: primero registra el zarpe.")
        return f"Llegada registrada a las {_hora_actual(conn)}."
    return _accion(request, conn, usuario, viaje_id, hacer, "")


@router.post("/{viaje_id}/espera/inicio")
def espera_inicio(viaje_id: int, request: Request, form=Depends(formulario), conn=Depends(get_db),
                  usuario=Depends(usuario_actual)):
    def hacer(viaje):
        _exigir_operacion(usuario, viaje)
        t = trayecto(conn, viaje)
        if t["navegando"]:
            raise ErrorFormulario("La lancha está navegando: la espera se registra con la lancha detenida.")
        if t["esperando"]:
            raise ErrorFormulario("Ya hay una espera en curso.")
        conn.execute("INSERT INTO viajes_esperas (viaje_id, inicio, motivo) VALUES (%s, now(), %s)",
                     (viaje["id"], campo_texto(form, "motivo")))
        return f"Inicio de espera registrado a las {_hora_actual(conn)}."
    return _accion(request, conn, usuario, viaje_id, hacer, "")


@router.post("/{viaje_id}/espera/fin")
def espera_fin(viaje_id: int, request: Request, form=Depends(formulario), conn=Depends(get_db),
               usuario=Depends(usuario_actual)):
    def hacer(viaje):
        _exigir_operacion(usuario, viaje)
        fila = conn.execute(
            """UPDATE viajes_esperas SET fin = now(), motivo = coalesce(%s, motivo)
                WHERE viaje_id = %s AND fin IS NULL RETURNING id""",
            (campo_texto(form, "motivo"), viaje["id"]),
        ).fetchone()
        if not fila:
            raise ErrorFormulario("No hay una espera en curso.")
        return f"Fin de espera registrado a las {_hora_actual(conn)}."
    return _accion(request, conn, usuario, viaje_id, hacer, "")


@router.post("/{viaje_id}/finalizar")
def finalizar(viaje_id: int, request: Request, form=Depends(formulario), conn=Depends(get_db),
              usuario=Depends(usuario_actual)):
    def hacer(viaje):
        _exigir_operacion(usuario, viaje)
        conn.execute("UPDATE viajes SET estado = 'FINALIZADO' WHERE id = %s", (viaje["id"],))
    return _accion(request, conn, usuario, viaje_id, hacer, "Viaje finalizado. Queda pendiente el cierre del supervisor.")


@router.post("/{viaje_id}/reanudar")
def reanudar(viaje_id: int, request: Request, form=Depends(formulario), conn=Depends(get_db),
             usuario=Depends(usuario_actual)):
    def hacer(viaje):
        if viaje["estado"] != "FINALIZADO" or not puede_editar(usuario, viaje):
            raise HTTPException(403, "Solo se reanuda un viaje finalizado y sin cerrar.")
        conn.execute("UPDATE viajes SET estado = 'EN_CURSO' WHERE id = %s", (viaje["id"],))
    return _accion(request, conn, usuario, viaje_id, hacer, "Viaje reanudado: ya puedes registrar más tramos.")


# --- Supervisor --------------------------------------------------------------
@router.post("/{viaje_id}/cerrar")
def cerrar(viaje_id: int, request: Request, form=Depends(formulario), conn=Depends(get_db),
           usuario=Depends(requiere("SUPERVISOR_OPS"))):
    def hacer(viaje):
        conn.execute("UPDATE viajes SET estado = 'CERRADO' WHERE id = %s", (viaje["id"],))
    return _accion(request, conn, usuario, viaje_id, hacer, "Reporte cerrado. Desde ahora solo cambia por enmienda.")


@router.post("/{viaje_id}/cancelar")
def cancelar(viaje_id: int, request: Request, form=Depends(formulario), conn=Depends(get_db),
             usuario=Depends(requiere("SUPERVISOR_OPS"))):
    def hacer(viaje):
        motivo = campo_texto(form, "motivo_cancelacion", "Motivo de la cancelación", True)
        conn.execute("UPDATE viajes SET estado = 'CANCELADO', motivo_cancelacion = %s WHERE id = %s",
                     (motivo, viaje["id"]))
    return _accion(request, conn, usuario, viaje_id, hacer, "Reporte cancelado.")


# --- Correcciones del trayecto (antes del cierre, o enmienda después) ----------
def _datos_tramo(conn, form, completo: bool) -> dict:
    datos = {"salida": _hora(conn, form, "salida", "Hora de salida")}
    datos["destino_id"], datos["destino_buque"] = _punto(form, "destino", "a dónde llegó", requerido=completo)
    datos["llegada"] = _hora(conn, form, "llegada", "Hora de llegada", requerido=completo)
    return datos


def _origen_del_primer_tramo(conn, form, viaje: dict, tramo_id: int | None) -> None:
    """El primer tramo también corrige de dónde zarpó la lancha."""
    if "origen" not in form:
        return
    primero = conn.execute("SELECT id FROM viajes_tramos WHERE viaje_id = %s ORDER BY salida, id LIMIT 1",
                           (viaje["id"],)).fetchone()
    if tramo_id is None or (primero and primero["id"] == tramo_id):
        origen_id, origen_buque = _punto(form, "origen", "de dónde zarpó la lancha")
        conn.execute("UPDATE viajes SET origen_id = %s, origen_buque = %s WHERE id = %s AND "
                     "(origen_id, origen_buque) IS DISTINCT FROM (%s::bigint, %s::boolean)",
                     (origen_id, origen_buque, viaje["id"], origen_id, origen_buque))


@router.post("/{viaje_id}/tramos/nuevo")
def agregar_tramo(viaje_id: int, request: Request, form=Depends(formulario), conn=Depends(get_db),
                  usuario=Depends(usuario_actual)):
    def hacer(viaje):
        _exigir_edicion(conn, usuario, viaje, form)
        datos = _datos_tramo(conn, form, completo=True)
        _origen_del_primer_tramo(conn, form, viaje, None)
        conn.execute("""INSERT INTO viajes_tramos (viaje_id, salida, destino_id, destino_buque, llegada)
                        VALUES (%s, %s, %s, %s, %s)""",
                     (viaje["id"], datos["salida"], datos["destino_id"], datos["destino_buque"], datos["llegada"]))
    return _accion(request, conn, usuario, viaje_id, hacer, "Tramo agregado.")


@router.post("/{viaje_id}/tramos/{tramo_id}")
def corregir_tramo(viaje_id: int, tramo_id: int, request: Request, form=Depends(formulario), conn=Depends(get_db),
                   usuario=Depends(usuario_actual)):
    def hacer(viaje):
        _exigir_edicion(conn, usuario, viaje, form)
        datos = _datos_tramo(conn, form, completo=False)
        if datos["llegada"] and not (datos["destino_id"] or datos["destino_buque"]):
            raise ErrorFormulario("Indica a dónde llegó.")
        _origen_del_primer_tramo(conn, form, viaje, tramo_id)
        fila = conn.execute(
            """UPDATE viajes_tramos SET salida = %(salida)s, destino_id = %(destino_id)s,
                      destino_buque = %(destino_buque)s, llegada = %(llegada)s
                WHERE id = %(id)s AND viaje_id = %(viaje)s
                  AND (salida, destino_id, destino_buque, llegada)
                      IS DISTINCT FROM (%(salida)s::timestamptz, %(destino_id)s::bigint, %(destino_buque)s, %(llegada)s::timestamptz)
            RETURNING id""",
            datos | {"id": tramo_id, "viaje": viaje["id"]},
        ).fetchone()
        return "Tramo corregido." if fila else "Sin cambios en el tramo."
    return _accion(request, conn, usuario, viaje_id, hacer, "")


@router.post("/{viaje_id}/tramos/{tramo_id}/eliminar")
def eliminar_tramo(viaje_id: int, tramo_id: int, request: Request, form=Depends(formulario), conn=Depends(get_db),
                   usuario=Depends(usuario_actual)):
    def hacer(viaje):
        _exigir_edicion(conn, usuario, viaje, form)
        conn.execute("DELETE FROM viajes_tramos WHERE id = %s AND viaje_id = %s", (tramo_id, viaje["id"]))
    return _accion(request, conn, usuario, viaje_id, hacer, "Tramo eliminado.")


def _datos_espera(conn, form, completo: bool) -> dict:
    return {"inicio": _hora(conn, form, "inicio", "Inicio de la espera"),
            "fin": _hora(conn, form, "fin", "Fin de la espera", requerido=completo),
            "motivo": campo_texto(form, "motivo")}


@router.post("/{viaje_id}/esperas/nueva")
def agregar_espera(viaje_id: int, request: Request, form=Depends(formulario), conn=Depends(get_db),
                   usuario=Depends(usuario_actual)):
    def hacer(viaje):
        _exigir_edicion(conn, usuario, viaje, form)
        conn.execute("""INSERT INTO viajes_esperas (viaje_id, inicio, fin, motivo)
                        VALUES (%(viaje)s, %(inicio)s, %(fin)s, %(motivo)s)""",
                     _datos_espera(conn, form, completo=True) | {"viaje": viaje["id"]})
    return _accion(request, conn, usuario, viaje_id, hacer, "Espera agregada.")


@router.post("/{viaje_id}/esperas/{espera_id}")
def corregir_espera(viaje_id: int, espera_id: int, request: Request, form=Depends(formulario), conn=Depends(get_db),
                    usuario=Depends(usuario_actual)):
    def hacer(viaje):
        _exigir_edicion(conn, usuario, viaje, form)
        datos = _datos_espera(conn, form, completo=False)
        fila = conn.execute(
            """UPDATE viajes_esperas SET inicio = %(inicio)s, fin = %(fin)s, motivo = %(motivo)s
                WHERE id = %(id)s AND viaje_id = %(viaje)s
                  AND (inicio, fin, motivo) IS DISTINCT FROM (%(inicio)s::timestamptz, %(fin)s::timestamptz, %(motivo)s::text)
            RETURNING id""",
            datos | {"id": espera_id, "viaje": viaje["id"]},
        ).fetchone()
        return "Espera corregida." if fila else "Sin cambios en la espera."
    return _accion(request, conn, usuario, viaje_id, hacer, "")


@router.post("/{viaje_id}/esperas/{espera_id}/eliminar")
def eliminar_espera(viaje_id: int, espera_id: int, request: Request, form=Depends(formulario), conn=Depends(get_db),
                    usuario=Depends(usuario_actual)):
    def hacer(viaje):
        _exigir_edicion(conn, usuario, viaje, form)
        conn.execute("DELETE FROM viajes_esperas WHERE id = %s AND viaje_id = %s", (espera_id, viaje["id"]))
    return _accion(request, conn, usuario, viaje_id, hacer, "Espera eliminada.")


# ---------------------------------------------------------------------------
# Enmiendas: cada cambio a un reporte cerrado, legible
# ---------------------------------------------------------------------------
_CAMPOS = {
    "fecha": "Fecha", "lancha_id": "Lancha", "patron_id": "Patrón", "cliente_id": "Al servicio de", "buque_id": "Buque",
    "requisicion": "N° de requisición comercial", "solicitado_por": "Solicitado por", "servicio_otro": "Otro servicio",
    "descripcion": "Descripción del servicio", "origen_id": "Punto de partida", "origen_buque": "Partida a bordo del buque",
    "salida": "Salida", "llegada": "Llegada", "destino_id": "Destino", "destino_buque": "Destino a bordo del buque",
    "inicio": "Inicio de espera", "fin": "Fin de espera", "motivo": "Motivo de la espera",
}
_TABLAS = {"viajes": "Reporte", "viajes_tramos": "Tramo", "viajes_esperas": "Espera",
           "viajes_servicios": "Tipo de servicio", "viajes_areas": "Área", "viajes_marinos": "Marino"}
_REFERENCIAS = {"lancha_id": "lanchas", "patron_id": "usuarios", "cliente_id": "clientes", "buque_id": "buques",
                "origen_id": "ubicaciones", "destino_id": "ubicaciones", "tipo_servicio_id": "tipos_servicio",
                "ubicacion_id": "ubicaciones", "marino_id": "marinos"}
_INTERNOS = {"id", "viaje_id", "registrado_por", "registrado_en"}


def _legible(conn, clave: str, valor) -> str:
    if valor is None or valor == "":
        return "—"
    if clave in _REFERENCIAS:
        fila = conn.execute(f"SELECT nombre FROM {_REFERENCIAS[clave]} WHERE id = %s", (int(valor),)).fetchone()
        return fila["nombre"] if fila else f"#{valor}"
    if isinstance(valor, bool):
        return "Sí" if valor else "No"
    if isinstance(valor, str) and re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", valor):
        return datetime.fromisoformat(valor).strftime("%d/%m/%Y %H:%M")
    return str(valor)


def _enmiendas(conn, viaje_id: int) -> list[dict]:
    """Agrupadas por enmienda (mismo momento, autor y motivo), cada una con sus cambios."""
    grupos = []
    for f in conn.execute(
        """SELECT e.*, u.nombre AS autor FROM viajes_enmiendas e LEFT JOIN usuarios u ON u.id = e.usuario_id
            WHERE e.viaje_id = %s ORDER BY e.fecha, e.id""", (viaje_id,)
    ):
        if not grupos or (grupos[-1]["fecha"], grupos[-1]["autor"], grupos[-1]["motivo"]) != (f["fecha"], f["autor"], f["motivo"]):
            grupos.append({"fecha": f["fecha"], "autor": f["autor"], "motivo": f["motivo"], "cambios": []})
        tabla = _TABLAS.get(f["tabla"], f["tabla"])
        if f["accion"] == "UPDATE":
            for clave, antes in f["antes"].items():
                if clave not in _INTERNOS:
                    grupos[-1]["cambios"].append(
                        f"{tabla} · {_CAMPOS.get(clave, clave)}: {_legible(conn, clave, antes)} → "
                        f"{_legible(conn, clave, f['despues'].get(clave))}")
        else:
            fila = f["despues"] if f["accion"] == "INSERT" else f["antes"]
            detalle = ", ".join(f"{_CAMPOS.get(k, k).lower()} {_legible(conn, k, v)}" for k, v in fila.items()
                                if k not in _INTERNOS and k in _CAMPOS and v not in (None, False))
            if not detalle:
                detalle = next((_legible(conn, k, v) for k, v in fila.items() if k in _REFERENCIAS), "")
            grupos[-1]["cambios"].append(f"{tabla} {'agregado' if f['accion'] == 'INSERT' else 'quitado'}: {detalle}")
    return grupos
