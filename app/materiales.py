"""Herramientas, repuestos e insumos: los previstos de una actividad y los usados en una ODS.

La lista se administra en Data → Herramientas y repuestos (catálogo «materiales» de app/catalogos.py).
En los formularios cada fila trae `material_id` y `material_cantidad`; la cantidad es opcional.
"""
from .web import ErrorFormulario, campo_filas, leer_numero

# Dónde se guarda cada lista: tabla y columna del registro dueño
LISTAS = {
    "actividad": ("actividades_materiales", "actividad_id"),
    "orden": ("ordenes_materiales", "orden_id"),
}
ORDEN_TIPO = "array_position(ARRAY['REPUESTO', 'HERRAMIENTA', 'INSUMO'], m.tipo), m.nombre"


def opciones(conn, actuales=()) -> list[dict]:
    """Materiales activos para los selectores, más los que ya están en la lista aunque se hayan desactivado."""
    ids = [int(a) for a in actuales if str(a).isdigit()]
    return conn.execute(
        f"""SELECT m.id, m.nombre, m.tipo, m.unidad, m.codigo FROM materiales m
             WHERE m.activo OR m.id = ANY(%s::bigint[]) ORDER BY {ORDEN_TIPO}""",
        (ids,),
    ).fetchall()


def del_formulario(form) -> list[dict]:
    """Las filas tal como vienen (para volver a mostrarlas si algo falla)."""
    return [{"material_id": m, "cantidad": c} for m, c in campo_filas(form, "material_id", "material_cantidad")]


def leer(form) -> list[dict]:
    """Valida las filas del formulario: un material por fila y cantidades positivas."""
    filas, vistos = [], set()
    for material, cantidad in campo_filas(form, "material_id", "material_cantidad"):
        if not material:
            raise ErrorFormulario("Elige el material de cada fila de la lista (o quita la fila).")
        if not material.isdigit():
            raise ErrorFormulario("Hay un material no válido en la lista.")
        if material in vistos:
            raise ErrorFormulario("Hay un material repetido en la lista: deja una sola fila por material.")
        vistos.add(material)
        numero = leer_numero(cantidad, "Cantidad") if cantidad else None
        if numero is not None and numero <= 0:
            raise ErrorFormulario("La cantidad de cada material debe ser mayor que cero.")
        filas.append({"material_id": int(material), "cantidad": numero})
    return filas


def guardar(conn, lista: str, registro_id: int, filas: list[dict]) -> None:
    """Reemplaza la lista completa (va dentro de la transacción del formulario)."""
    tabla, columna = LISTAS[lista]
    conn.execute(f"DELETE FROM {tabla} WHERE {columna} = %s", (registro_id,))
    with conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO {tabla} ({columna}, material_id, cantidad) VALUES (%s, %s, %s)",
            [(registro_id, f["material_id"], f["cantidad"]) for f in filas],
        )


def de(conn, lista: str, registro_id: int | None) -> list[dict]:
    if not registro_id:
        return []
    tabla, columna = LISTAS[lista]
    return conn.execute(
        f"""SELECT x.material_id, x.cantidad, m.nombre, m.tipo, m.unidad, m.codigo
              FROM {tabla} x JOIN materiales m ON m.id = x.material_id
             WHERE x.{columna} = %s ORDER BY {ORDEN_TIPO}""",
        (registro_id,),
    ).fetchall()
