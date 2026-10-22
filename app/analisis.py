"""Análisis de fallas y tiempos de mantenimiento (sección de Inicio).

* Una falla es una ODS correctiva no cancelada; se agrupa por lo que falló (ítem de inspección).
* La maquinaria se elige como en el resto del sistema: L<id> (lancha) o G<id> (generador), más
  «L» = todas las lanchas, «G» = todos los generadores y vacío = toda la flota.
* Las ODS entran al período por su fecha (el día en que se detectó la falla o se abrió la ODS).
* Horas fuera de servicio y disponibilidad: la unión de los periodos de cada unidad dentro del
  período (dos fallas que se solapan no cuentan doble; una que sigue abierta cuenta hasta ahora).
* Trabajo y factores externos: lo que dice cada ODS ya reportada (ver migración 012).
"""
import calendar
import re
from datetime import date, timedelta

REPORTADAS = ["EJECUTADA", "CERRADA"]
LIMITE_HISTORIAL = 100
LIMITE_FALLAS = 10

# Lanchas y generadores con la clave del filtro (L<id> / G<id>)
SQL_UNIDADES = """
    SELECT 'L' || l.id AS clave, 'LANCHA' AS tipo, l.id, l.nombre, l.estado = 'OPERATIVA' AS activa, l.numero
      FROM lanchas l
    UNION ALL
    SELECT 'G' || e.id, 'GENERADOR', e.id, nombre_unidad(e), e.activo, e.numero
      FROM equipos e WHERE e.tipo = 'GENERADOR'
"""


def _restar_meses(fecha: date, meses: int) -> date:
    total = fecha.year * 12 + fecha.month - 1 - meses
    anio, mes = divmod(total, 12)
    return date(anio, mes + 1, min(fecha.day, calendar.monthrange(anio, mes + 1)[1]))


def periodos(hoy: date) -> list[tuple[str, date, date]]:
    """Atajos de fechas. El último es el que se usa si no se indica otro."""
    inicio_mes = hoy.replace(day=1)
    fin_mes_pasado = inicio_mes - timedelta(days=1)
    return [
        ("Este mes", inicio_mes, hoy),
        ("Mes pasado", fin_mes_pasado.replace(day=1), fin_mes_pasado),
        ("Últimos 3 meses", _restar_meses(hoy, 3) + timedelta(days=1), hoy),
        ("Este año", hoy.replace(month=1, day=1), hoy),
        ("Últimos 12 meses", _restar_meses(hoy, 12) + timedelta(days=1), hoy),
    ]


def unidades(conn) -> list[dict]:
    return conn.execute(SQL_UNIDADES + " ORDER BY tipo DESC, numero").fetchall()


def leer_unidad(valor: str | None, todas: list[dict]) -> str:
    """La maquinaria pedida en la URL; lo que no existe se toma como toda la flota."""
    valor = (valor or "").strip().upper()
    if valor in ("L", "G"):
        return valor
    if re.fullmatch(r"[LG]\d+", valor) and any(u["clave"] == valor for u in todas):
        return valor
    return ""


def _filtro(unidad: str, tipo: str = "o.tipo_unidad", clave: str = "o.unidad_clave") -> tuple[str, list]:
    """Condición SQL para la maquinaria elegida (columnas de v_ordenes_analisis o de SQL_UNIDADES)."""
    if unidad in ("L", "G"):
        return f"{tipo} = %s", ["LANCHA" if unidad == "L" else "GENERADOR"]
    if unidad:
        return f"{clave} = %s", [unidad]
    return "true", []


def analizar(conn, unidad: str, desde: date, hasta: date) -> dict:
    """Todo lo que muestra la sección para una maquinaria (o grupo) y un período."""
    condicion, params = _filtro(unidad)
    en_periodo = f"{condicion} AND o.fecha BETWEEN %s AND %s"
    params = params + [desde, hasta]

    # Inicio y fin del período en horas: hasta el final del día «hasta», pero nunca después de ahora
    rango = conn.execute(
        """SELECT %(desde)s::timestamptz AS inicio,
                  greatest(%(desde)s::timestamptz, least((%(hasta)s::date + 1)::timestamptz, now())) AS fin""",
        {"desde": desde, "hasta": hasta},
    ).fetchone()
    horas_periodo = (rango["fin"] - rango["inicio"]).total_seconds() / 3600

    totales = conn.execute(
        f"""SELECT count(*) AS ods,
                   count(*) FILTER (WHERE o.tipo = 'CORRECTIVO') AS fallas,
                   count(*) FILTER (WHERE o.tipo = 'CORRECTIVO' AND o.item_id IS NULL) AS sin_clasificar,
                   count(*) FILTER (WHERE o.tipo = 'PREVENTIVO') AS preventivas,
                   count(*) FILTER (WHERE o.estado = ANY(%s) AND o.horas_reparacion IS NOT NULL) AS con_trabajo,
                   sum(o.horas_reparacion) FILTER (WHERE o.estado = ANY(%s)) AS trabajo,
                   sum(o.horas_hombre) FILTER (WHERE o.estado = ANY(%s)) AS horas_hombre
              FROM v_ordenes_analisis o WHERE {en_periodo}""",
        [REPORTADAS] * 3 + params,
    ).fetchone()

    fallas = conn.execute(
        f"""SELECT o.item_id, coalesce(o.item, 'Sin clasificar') AS falla, max(o.categoria) AS categoria,
                   count(*) AS veces, count(DISTINCT o.unidad_clave) AS n_unidades,
                   string_agg(DISTINCT o.unidad, ', ') AS unidades, max(o.fecha) AS ultima,
                   sum(o.horas_fuera) AS horas_fuera, sum(o.horas_reparacion) AS trabajo
              FROM v_ordenes_analisis o
             WHERE o.tipo = 'CORRECTIVO' AND {en_periodo}
             GROUP BY o.item_id, o.item
             ORDER BY o.item_id IS NULL, count(*) DESC, max(o.fecha) DESC, coalesce(o.item, '')""",
        params,
    ).fetchall()
    clasificadas = [f for f in fallas if f["item_id"] is not None]
    # La más recurrente: la de más veces (con empate, la más reciente), si alguna se repitió
    mas_recurrente = clasificadas[0] if clasificadas and clasificadas[0]["veces"] > 1 else None

    # ¿En qué se fue el tiempo fuera de servicio? (ODS ya reportadas que pararon la maquinaria)
    reparto = conn.execute(
        f"""SELECT coalesce(sum(o.horas_fuera), 0) AS fuera, coalesce(sum(o.horas_reparacion), 0) AS trabajo
              FROM v_ordenes_analisis o
             WHERE o.fuera_servicio AND o.estado = ANY(%s) AND {en_periodo}""",
        [REPORTADAS] + params,
    ).fetchone()
    motivos = conn.execute(
        f"""SELECT m.nombre, sum(d.horas) AS horas
              FROM ordenes_demoras d
              JOIN motivos_inoperatividad m ON m.id = d.motivo_id
              JOIN v_ordenes_analisis o ON o.id = d.orden_id
             WHERE o.fuera_servicio AND o.estado = ANY(%s) AND {en_periodo}
             GROUP BY m.nombre ORDER BY sum(d.horas) DESC, m.nombre""",
        [REPORTADAS] + params,
    ).fetchall()
    sin_motivo = max(reparto["fuera"] - reparto["trabajo"] - sum(m["horas"] for m in motivos), 0)
    segmentos = ([("Trabajo de reparación", reparto["trabajo"], True)]
                 + [(m["nombre"], m["horas"], False) for m in motivos]
                 + ([("Sin motivo indicado", sin_motivo, False)] if sin_motivo > 0.01 else []))
    segmentos = [(texto, horas, trabajo, float(horas) / float(reparto["fuera"]) * 100 if reparto["fuera"] else 0)
                 for texto, horas, trabajo in segmentos if horas]

    # Por unidad: fallas, la más recurrente, horas fuera de servicio (unión) y disponibilidad
    condicion_u, params_u = _filtro(unidad, "u.tipo", "u.clave")
    por_unidad = conn.execute(
        f"""WITH u AS ({SQL_UNIDADES}), ods AS (
                SELECT * FROM v_ordenes_analisis o WHERE o.fecha BETWEEN %s AND %s
            ), conteo AS (
                SELECT unidad_clave, count(*) AS ods, count(*) FILTER (WHERE tipo = 'CORRECTIVO') AS fallas,
                       sum(horas_reparacion) FILTER (WHERE estado = ANY(%s)) AS trabajo
                  FROM ods GROUP BY unidad_clave
            ), top AS (
                SELECT DISTINCT ON (unidad_clave) unidad_clave, item AS falla, count(*) AS veces
                  FROM ods WHERE tipo = 'CORRECTIVO' AND item_id IS NOT NULL
                 GROUP BY unidad_clave, item HAVING count(*) > 1
                 ORDER BY unidad_clave, count(*) DESC, max(fecha) DESC, item
            )
            SELECT u.*, coalesce(c.ods, 0) AS ods, coalesce(c.fallas, 0) AS fallas, c.trabajo,
                   t.falla, t.veces, coalesce(f.horas, 0) AS horas_fuera
              FROM u
              LEFT JOIN conteo c ON c.unidad_clave = u.clave
              LEFT JOIN top t    ON t.unidad_clave = u.clave
              LEFT JOIN horas_fuera_servicio(%s, %s) f ON f.unidad_clave = u.clave
             WHERE {condicion_u} AND (u.activa OR c.ods IS NOT NULL OR f.horas > 0)
             ORDER BY coalesce(c.fallas, 0) DESC, coalesce(f.horas, 0) DESC, u.tipo DESC, u.numero""",
        [desde, hasta, REPORTADAS, rango["inicio"], rango["fin"]] + params_u,
    ).fetchall()
    por_unidad = [u | {"disponibilidad": max(0.0, 100 - float(u["horas_fuera"]) / horas_periodo * 100)
                       if horas_periodo else None}
                  for u in por_unidad]
    horas_fuera = sum(u["horas_fuera"] for u in por_unidad)
    disponibilidad = (sum(u["disponibilidad"] for u in por_unidad) / len(por_unidad)
                      if por_unidad and horas_periodo else None)

    historial = []
    if unidad not in ("", "L", "G"):
        historial = conn.execute(
            f"""SELECT o.*, (o.estado NOT IN ('EJECUTADA', 'CERRADA') AND o.fuera_servicio
                             AND o.fuera_servicio_desde IS NOT NULL AND o.fuera_servicio_hasta IS NULL) AS sigue_fuera
                  FROM v_ordenes_analisis o WHERE {en_periodo}
                 ORDER BY o.fecha DESC, o.id DESC LIMIT %s""",
            params + [LIMITE_HISTORIAL + 1],
        ).fetchall()

    return {
        "totales": totales,
        "fallas": fallas[:LIMITE_FALLAS] + [f for f in fallas[LIMITE_FALLAS:] if f["item_id"] is None],
        "fallas_ocultas": max(len(clasificadas) - LIMITE_FALLAS, 0),
        "max_veces": max((f["veces"] for f in fallas), default=0),
        "mas_recurrente": mas_recurrente,
        "reparto": reparto,
        "segmentos": segmentos,
        "por_unidad": por_unidad,
        "horas_fuera": horas_fuera,
        "disponibilidad": disponibilidad,
        "historial": historial[:LIMITE_HISTORIAL],
        "historial_recortado": len(historial) > LIMITE_HISTORIAL,
    }
