from itertools import groupby

from fastapi import APIRouter, Depends, HTTPException, Request

from ..db import get_db
from ..seguridad import ROLES_MANTENIMIENTO, formulario, requiere, tiene_rol, usuario_actual
from ..web import (ERRORES, ErrorFormulario, avisar, campo_entero, campo_fecha, campo_numero, campo_texto,
                   param_entero, redirigir, render, texto_error)

# Solo para los roles de Mantenimiento
router = APIRouter(prefix="/bitacora", dependencies=[Depends(requiere(*ROLES_MANTENIMIENTO))])

RESULTADOS = ("OK", "FALLA", "NA")


@router.get("")
def lista(request: Request, conn=Depends(get_db), usuario=Depends(usuario_actual)):
    filtros = {
        "lancha_id": param_entero(request, "lancha_id"),
        "desde": request.query_params.get("desde") or None,
        "hasta": request.query_params.get("hasta") or None,
        "fallas": request.query_params.get("fallas") == "1",
    }
    condiciones, params = [], []
    if filtros["lancha_id"]:
        condiciones.append("b.lancha_id = %s")
        params.append(filtros["lancha_id"])
    if filtros["desde"]:
        condiciones.append("b.fecha >= %s::date")
        params.append(filtros["desde"])
    if filtros["hasta"]:
        condiciones.append("b.fecha <= %s::date")
        params.append(filtros["hasta"])
    if filtros["fallas"]:
        condiciones.append("""EXISTS (SELECT 1 FROM bitacora_respuestas r
                                       WHERE r.bitacora_id = b.id AND r.resultado = 'FALLA')""")
    where = ("WHERE " + " AND ".join(condiciones)) if condiciones else ""
    try:
        bitacoras = conn.execute(
            f"""SELECT b.id, b.fecha, b.origen, b.observacion, l.nombre AS lancha, u.nombre AS registrado_por,
                       (SELECT string_agg(coalesce(e.codigo, 'Lancha') || ': ' || bl.horometro || ' h', ' · '
                                          ORDER BY (e.tipo = 'GENERADOR'), e.numero)
                          FROM bitacora_lecturas bl JOIN equipos e ON e.id = bl.equipo_id
                         WHERE bl.bitacora_id = b.id) AS lecturas,
                       (SELECT count(*) FROM bitacora_respuestas r
                         WHERE r.bitacora_id = b.id AND r.resultado = 'FALLA') AS fallas
                  FROM bitacora b
                  LEFT JOIN lanchas l ON l.id = b.lancha_id
                  LEFT JOIN usuarios u ON u.id = b.registrado_por
                 {where}
                 ORDER BY b.fecha DESC, b.id DESC
                 LIMIT 200""",
            params,
        ).fetchall()
    except ERRORES:   # fecha mal escrita en la URL
        conn.rollback()
        return redirigir("/bitacora")
    return render(request, "bitacora/lista.html", {
        "bitacoras": bitacoras,
        "filtros": filtros,
        "lanchas": conn.execute("SELECT id, codigo, nombre FROM lanchas ORDER BY numero").fetchall(),
    })


def _contexto_formulario(conn, lancha_id: int | None, valores: dict, error: str | None = None) -> dict:
    ctx = {
        "lanchas": conn.execute("SELECT id, codigo, nombre, estado FROM lanchas ORDER BY estado DESC, numero").fetchall(),
        "lancha": None,
        "valores": valores,
        "error": error,
        "hoy": conn.execute("SELECT current_date AS hoy").fetchone()["hoy"],
    }
    if not lancha_id:
        return ctx
    ctx["lancha"] = conn.execute("SELECT id, nombre FROM lanchas WHERE id = %s", (lancha_id,)).fetchone()
    if not ctx["lancha"]:
        raise HTTPException(404)
    ctx["horometro"] = _horometro_lancha(conn, lancha_id)
    ctx["generadores"] = conn.execute(
        """SELECT g.id, g.codigo, g.nombre, g.horometro_actual, ub.lancha_id AS ubicacion_id, ub.lancha AS ubicacion,
                  (SELECT max(b.fecha) FROM bitacora_lecturas l JOIN bitacora b ON b.id = l.bitacora_id
                    WHERE l.equipo_id = g.id) AS ultima_fecha
             FROM equipos g
             LEFT JOIN LATERAL (
                  SELECT la.id AS lancha_id, la.nombre AS lancha
                    FROM bitacora_lecturas l JOIN bitacora b ON b.id = l.bitacora_id JOIN lanchas la ON la.id = b.lancha_id
                   WHERE l.equipo_id = g.id ORDER BY b.fecha DESC, b.id DESC LIMIT 1) ub ON true
            WHERE g.tipo = 'GENERADOR' AND g.activo
            ORDER BY g.numero"""
    ).fetchall()
    if "generador_id" not in valores:
        # Se propone el generador que iba en esta lancha, si desde entonces no se registró en otra
        sugerido = next((g for g in ctx["generadores"] if g["ubicacion_id"] == lancha_id), None)
        ctx["valores"] = valores | {"generador_id": str(sugerido["id"]) if sugerido else ""}
    items = conn.execute(
        """SELECT i.id, i.nombre, i.aplica_a, c.nombre AS categoria
             FROM items_inspeccion i JOIN categorias c ON c.id = i.categoria_id
            WHERE i.activo
            ORDER BY c.id, i.orden, i.nombre"""
    ).fetchall()
    for aplica_a, clave in (("LANCHA", "grupos_lancha"), ("GENERADOR", "grupos_generador")):
        filtrados = [i for i in items if i["aplica_a"] == aplica_a]
        ctx[clave] = [(cat, list(filas)) for cat, filas in groupby(filtrados, key=lambda i: i["categoria"])]
    # Fallas de la lancha que ya tienen ODS pendiente: se muestran para no reportarlas "como nuevas"
    ctx["ot_por_item"] = {
        f["item_id"]: f["orden_id"]
        for f in conn.execute(
            """SELECT r.item_id, o.id AS orden_id
                 FROM ordenes o JOIN bitacora_respuestas r ON r.id = o.falla_id
                WHERE o.lancha_id = %s AND o.equipo_id IS NULL
                  AND o.estado IN ('ABIERTA', 'EN_PROCESO', 'EJECUTADA')""",
            (lancha_id,),
        )
    }
    ctx["existente"] = conn.execute(
        "SELECT id FROM bitacora WHERE lancha_id = %s AND fecha = current_date AND origen = 'DIARIA'",
        (lancha_id,),
    ).fetchone()
    return ctx


def _horometro_lancha(conn, lancha_id: int) -> dict | None:
    return conn.execute(
        """SELECT e.id, e.horometro_actual,
                  (SELECT max(b.fecha) FROM bitacora_lecturas l JOIN bitacora b ON b.id = l.bitacora_id
                    WHERE l.equipo_id = e.id) AS ultima_fecha
             FROM equipos e WHERE e.lancha_id = %s AND e.tipo = 'PROPULSION' AND e.activo""",
        (lancha_id,),
    ).fetchone()


@router.get("/nueva")
def nueva(request: Request, conn=Depends(get_db), usuario=Depends(requiere("MECANICO", "SUPERVISOR"))):
    lancha_id = param_entero(request, "lancha_id")
    return render(request, "bitacora/form.html", _contexto_formulario(conn, lancha_id, {}))


@router.post("/nueva")
def crear(request: Request, form=Depends(formulario), conn=Depends(get_db),
          usuario=Depends(requiere("MECANICO", "SUPERVISOR"))):
    try:
        lancha_id = int(form.get("lancha_id") or 0)
    except ValueError:
        lancha_id = 0
    if not lancha_id:
        return redirigir("/bitacora/nueva")
    try:
        fecha = campo_fecha(form, "fecha", "Fecha", True)
        observacion = campo_texto(form, "observacion")
        lecturas = []
        horometro = _horometro_lancha(conn, lancha_id)
        if horometro:
            lecturas.append((horometro["id"], campo_numero(form, "horometro_lancha", "Horómetro de la lancha", True)))
        generador_id = campo_entero(form, "generador_id")
        if generador_id:
            generador = conn.execute(
                "SELECT codigo FROM equipos WHERE id = %s AND tipo = 'GENERADOR' AND activo", (generador_id,)
            ).fetchone()
            if not generador:
                raise ErrorFormulario("El generador elegido no existe o está desactivado.")
            lecturas.append((generador_id, campo_numero(form, "horometro_generador",
                                                        f"Horómetro del generador {generador['codigo']}", True)))

        # Ítems de la lancha siempre; los del generador solo si iba uno a bordo
        items = conn.execute(
            "SELECT id, nombre FROM items_inspeccion WHERE activo AND (aplica_a = 'LANCHA' OR %s) ORDER BY orden",
            (bool(generador_id),),
        ).fetchall()
        respuestas = []
        for item in items:
            resultado = form.get(f"item_{item['id']}") or "OK"
            if resultado not in RESULTADOS:
                raise ErrorFormulario(f"Respuesta no válida en «{item['nombre']}».")
            comentario = campo_texto(form, f"comentario_{item['id']}")
            if resultado == "FALLA" and not comentario:
                raise ErrorFormulario(f"Describe la falla de «{item['nombre']}».")
            respuestas.append((item["id"], resultado, comentario))

        with conn.transaction():
            bitacora_id = conn.execute(
                "INSERT INTO bitacora (lancha_id, fecha, observacion) VALUES (%s, %s, %s) RETURNING id",
                (lancha_id, fecha, observacion),
            ).fetchone()["id"]
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO bitacora_lecturas (bitacora_id, equipo_id, horometro) VALUES (%s, %s, %s)",
                    [(bitacora_id, equipo_id, valor) for equipo_id, valor in lecturas],
                )
                cur.executemany(
                    """INSERT INTO bitacora_respuestas (bitacora_id, item_id, resultado, comentario)
                       VALUES (%s, %s, %s, %s)""",
                    [(bitacora_id, *r) for r in respuestas],
                )
            creadas = conn.execute(
                "SELECT id, tipo FROM procesar_bitacora(%s)", (bitacora_id,)
            ).fetchall()
    except ERRORES as e:
        return render(request, "bitacora/form.html",
                      _contexto_formulario(conn, lancha_id, dict(form), texto_error(e)), 422)
    conn.commit()

    fallas = sum(1 for r in respuestas if r[1] == "FALLA")
    avisar(request, f"Bitácora #{bitacora_id} guardada ({fallas} falla(s) reportada(s)).")
    if creadas:
        detalle = ", ".join(f"#{o['id']} {o['tipo'].lower()}" for o in creadas)
        avisar(request, f"Se abrieron órdenes de servicio: {detalle}.", "warning")
    return redirigir(f"/bitacora/{bitacora_id}")


@router.get("/{bitacora_id}")
def detalle(bitacora_id: int, request: Request, conn=Depends(get_db), usuario=Depends(usuario_actual)):
    bitacora = conn.execute(
        """SELECT b.*, l.nombre AS lancha, u.nombre AS registrado_nombre
             FROM bitacora b LEFT JOIN lanchas l ON l.id = b.lancha_id
             LEFT JOIN usuarios u ON u.id = b.registrado_por
            WHERE b.id = %s""",
        (bitacora_id,),
    ).fetchone()
    if not bitacora:
        raise HTTPException(404)
    lecturas = conn.execute(
        """SELECT CASE WHEN e.tipo = 'GENERADOR' THEN 'Generador ' || nombre_unidad(e) ELSE 'Horómetro de la lancha' END
                  AS etiqueta, e.tipo, e.id AS equipo_id, l.horometro,
                  coalesce((SELECT l2.horometro
                              FROM bitacora_lecturas l2 JOIN bitacora b2 ON b2.id = l2.bitacora_id
                             WHERE l2.equipo_id = l.equipo_id AND (b2.fecha, l2.id) < (b.fecha, l.id)
                             ORDER BY b2.fecha DESC, l2.id DESC LIMIT 1),
                           e.horometro_inicial) AS anterior
             FROM bitacora_lecturas l
             JOIN equipos e  ON e.id = l.equipo_id
             JOIN bitacora b ON b.id = l.bitacora_id
            WHERE l.bitacora_id = %s ORDER BY (e.tipo = 'GENERADOR'), e.numero""",
        (bitacora_id,),
    ).fetchall()
    respuestas = conn.execute(
        """SELECT r.resultado, r.comentario, i.nombre, c.nombre AS categoria,
                  (SELECT o.id FROM ordenes o WHERE o.falla_id = r.id) AS orden_id
             FROM bitacora_respuestas r
             JOIN items_inspeccion i ON i.id = r.item_id
             JOIN categorias c ON c.id = i.categoria_id
            WHERE r.bitacora_id = %s
            ORDER BY (r.resultado = 'FALLA') DESC, (r.resultado = 'NA') DESC, c.id, i.orden""",
        (bitacora_id,),
    ).fetchall()
    cambios = conn.execute(
        """SELECT c.*, u.nombre AS usuario FROM bitacora_cambios c LEFT JOIN usuarios u ON u.id = c.usuario_id
            WHERE c.bitacora_id = %s ORDER BY c.fecha DESC""",
        (bitacora_id,),
    ).fetchall()
    return render(request, "bitacora/detalle.html", {
        "bitacora": bitacora, "lecturas": lecturas, "respuestas": respuestas, "cambios": cambios,
        "puede_editar": _puede_editar(bitacora, usuario),
    })


# ---------------------------------------------------------------------------
# Corrección de una bitácora ya registrada
# ---------------------------------------------------------------------------
ETIQUETA_RESULTADO = {"OK": "OK", "FALLA": "Falla", "NA": "N/A"}


def _puede_editar(bitacora: dict, usuario: dict) -> bool:
    """El supervisor corrige cualquier bitácora diaria; el mecánico, las que registró él."""
    if bitacora["origen"] != "DIARIA":
        return False
    return tiene_rol(usuario, "SUPERVISOR") or bitacora["registrado_por"] == usuario["id"]


def _buscar_editable(conn, bitacora_id: int, usuario: dict) -> dict:
    bitacora = conn.execute(
        """SELECT b.*, l.nombre AS lancha FROM bitacora b LEFT JOIN lanchas l ON l.id = b.lancha_id
            WHERE b.id = %s""",
        (bitacora_id,),
    ).fetchone()
    if not bitacora:
        raise HTTPException(404)
    if not _puede_editar(bitacora, usuario):
        raise HTTPException(403, "Solo el supervisor de mantenimiento o quien registró esta bitácora diaria puede corregirla.")
    return bitacora


def _datos_edicion(conn, bitacora_id: int) -> tuple[list[dict], list[dict]]:
    lecturas = conn.execute(
        """SELECT l.id, l.horometro, e.tipo,
                  CASE WHEN e.tipo = 'GENERADOR' THEN 'Generador ' || nombre_unidad(e)
                       ELSE 'Horómetro de la lancha' END AS etiqueta
             FROM bitacora_lecturas l JOIN equipos e ON e.id = l.equipo_id
            WHERE l.bitacora_id = %s ORDER BY (e.tipo = 'GENERADOR'), e.numero""",
        (bitacora_id,),
    ).fetchall()
    respuestas = conn.execute(
        """SELECT r.id, r.item_id, r.resultado, r.comentario, i.nombre, i.aplica_a, c.nombre AS categoria,
                  (SELECT o.id FROM ordenes o WHERE o.falla_id = r.id) AS ods
             FROM bitacora_respuestas r
             JOIN items_inspeccion i ON i.id = r.item_id
             JOIN categorias c ON c.id = i.categoria_id
            WHERE r.bitacora_id = %s
            ORDER BY i.aplica_a DESC, c.id, i.orden, i.nombre""",
        (bitacora_id,),
    ).fetchall()
    return lecturas, respuestas


def _contexto_edicion(conn, bitacora: dict, valores: dict | None, error: str | None = None) -> dict:
    lecturas, respuestas = _datos_edicion(conn, bitacora["id"])
    if valores is None:   # primera carga: lo guardado
        valores = {"observacion": bitacora["observacion"] or ""}
        valores |= {f"horometro_{l['id']}": l["horometro"] for l in lecturas}
        for r in respuestas:
            valores[f"item_{r['item_id']}"] = r["resultado"]
            valores[f"comentario_{r['item_id']}"] = r["comentario"] or ""

    def grupos(aplica_a):
        filas = [{"id": r["item_id"], "nombre": r["nombre"], "categoria": r["categoria"]}
                 for r in respuestas if r["aplica_a"] == aplica_a]
        return [(cat, list(items)) for cat, items in groupby(filas, key=lambda i: i["categoria"])]

    return {
        "bitacora": bitacora, "lecturas": lecturas, "valores": valores, "error": error,
        "grupos_lancha": grupos("LANCHA"), "grupos_generador": grupos("GENERADOR"),
        "bloqueados": {r["item_id"]: r["ods"] for r in respuestas if r["ods"]},
    }


@router.get("/{bitacora_id}/editar")
def editar(bitacora_id: int, request: Request, conn=Depends(get_db),
           usuario=Depends(requiere("MECANICO", "SUPERVISOR"))):
    bitacora = _buscar_editable(conn, bitacora_id, usuario)
    return render(request, "bitacora/editar.html", _contexto_edicion(conn, bitacora, None))


@router.post("/{bitacora_id}/editar")
def guardar_edicion(bitacora_id: int, request: Request, form=Depends(formulario), conn=Depends(get_db),
                    usuario=Depends(requiere("MECANICO", "SUPERVISOR"))):
    bitacora = _buscar_editable(conn, bitacora_id, usuario)
    lecturas, respuestas = _datos_edicion(conn, bitacora_id)
    try:
        motivo = campo_texto(form, "motivo", "Motivo de la corrección", True)
        cambios, nuevas_lecturas, nuevas_respuestas = [], [], []
        for lectura in lecturas:
            valor = campo_numero(form, f"horometro_{lectura['id']}", lectura["etiqueta"], True)
            if valor != lectura["horometro"]:
                cambios.append(f"{lectura['etiqueta']}: {lectura['horometro']} h → {valor} h")
                nuevas_lecturas.append((valor, lectura["id"]))
        for r in respuestas:
            # Una falla que ya generó su ODS no cambia de resultado (solo su descripción)
            resultado = r["resultado"] if r["ods"] else (form.get(f"item_{r['item_id']}") or "OK")
            if resultado not in RESULTADOS:
                raise ErrorFormulario(f"Respuesta no válida en «{r['nombre']}».")
            comentario = campo_texto(form, f"comentario_{r['item_id']}") if resultado == "FALLA" else None
            if resultado == "FALLA" and not comentario:
                raise ErrorFormulario(f"Describe la falla de «{r['nombre']}».")
            if (resultado, comentario) != (r["resultado"], r["comentario"]):
                detalle = f"{r['nombre']}: {ETIQUETA_RESULTADO[r['resultado']]} → {ETIQUETA_RESULTADO[resultado]}"
                cambios.append(detalle + (f" ({comentario})" if comentario else ""))
                nuevas_respuestas.append((resultado, comentario, r["id"]))
        observacion = campo_texto(form, "observacion")
        if observacion != bitacora["observacion"]:
            cambios.append("Observación general modificada")
        if not cambios:
            avisar(request, "No había cambios que guardar.", "info")
            return redirigir(f"/bitacora/{bitacora_id}")

        with conn.transaction():
            with conn.cursor() as cur:
                cur.executemany("UPDATE bitacora_lecturas SET horometro = %s WHERE id = %s", nuevas_lecturas)
                cur.executemany("UPDATE bitacora_respuestas SET resultado = %s, comentario = %s WHERE id = %s",
                                nuevas_respuestas)
            conn.execute("UPDATE bitacora SET observacion = %s WHERE id = %s", (observacion, bitacora_id))
            conn.execute("INSERT INTO bitacora_cambios (bitacora_id, motivo, detalle) VALUES (%s, %s, %s)",
                         (bitacora_id, motivo, "\n".join(cambios)))
            # Las fallas nuevas abren su ODS; los horómetros corregidos pueden activar preventivas
            creadas = conn.execute("SELECT id, tipo FROM procesar_bitacora(%s)", (bitacora_id,)).fetchall()
    except ERRORES as e:
        return render(request, "bitacora/editar.html",
                      _contexto_edicion(conn, bitacora, dict(form), texto_error(e)), 422)
    conn.commit()
    avisar(request, f"Bitácora #{bitacora_id} corregida ({len(cambios)} cambio(s)).")
    if creadas:
        detalle = ", ".join(f"#{o['id']} {o['tipo'].lower()}" for o in creadas)
        avisar(request, f"Se abrieron órdenes de servicio: {detalle}.", "warning")
    return redirigir(f"/bitacora/{bitacora_id}")
