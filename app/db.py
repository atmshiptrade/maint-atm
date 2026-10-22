import psycopg
from fastapi import Request
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import config

pool = ConnectionPool(
    config.DATABASE_URL,
    min_size=1,
    max_size=10,
    open=False,
    kwargs={"row_factory": dict_row, "options": f"-c TimeZone={config.ZONA_HORARIA}"},
)


def get_db(request: Request):
    """Una conexión y una transacción por request.

    Los triggers saben quién actúa porque se fija app.usuario_id al inicio.
    Las escrituras van dentro de `with conn.transaction()` (un savepoint) para
    poder mostrar el error y seguir usando la conexión, y luego `conn.commit()`.
    """
    with pool.connection() as conn:
        conn.execute(
            "SELECT set_config('app.usuario_id', %s, true)",
            (str(request.session.get("uid") or ""),),
        )
        yield conn


MENSAJES_RESTRICCION = {
    "ux_bitacora_diaria": "Ya existe la bitácora diaria de esta lancha para esa fecha.",
    "ux_equipos_nombre_activo": "La lancha ya tiene un equipo activo con ese nombre.",
    "ux_equipos_propulsion": "La lancha ya tiene su horómetro principal.",
    "ux_ordenes_tarea_pendiente": "Esa tarea ya tiene una ODS pendiente.",
    "tareas_unica": "Esa actividad ya está programada para ese equipo.",
    "equipos_potencia_check": "La potencia debe ser mayor que cero.",
    "tareas_con_intervalo": "Indica cada cuántas horas, cada cuántos días, o ambos.",
    "tareas_horas_completas": "Si la tarea es por horas, indica el horómetro del último servicio.",
    "tareas_dias_completos": "Si la tarea es por días, indica la fecha del último servicio.",
    "respuestas_falla_con_comentario": "Toda falla debe tener una descripción.",
    "ordenes_ejecucion_completa": "Faltan datos de la ejecución (fecha, servicio realizado u horómetro).",
    "ordenes_servicio_definido": "Indica si el servicio lo hicieron los mecánicos de la empresa o fue contratado.",
    "ordenes_contratado_con_proveedor": "Elige el proveedor del servicio contratado.",
    "ordenes_proveedor_solo_contratado": "Solo un servicio contratado lleva proveedor.",
    "ordenes_programa_definido": "Indica si el servicio corresponde al programa de mantenimiento.",
    "ordenes_preventiva_del_programa": "Una ODS preventiva siempre es del programa de mantenimiento.",
    "proveedores_nombre_key": "Ya existe un proveedor con ese nombre.",
    "proveedores_rif_key": "Ya existe un proveedor con ese RIF.",
    "lanchas_matricula_key": "Ya existe una lancha con esa matrícula.",
    "actividades_nombre_key": "Ya existe una actividad con ese nombre.",
    "items_inspeccion_nombre_key": "Ya existe un ítem de inspección con ese nombre.",
    "usuarios_usuario_key": "Ese nombre de usuario ya está en uso.",
    "usuarios_roles_validos": "Ese rol no corresponde a ese módulo.",
    # Materiales, tiempos y fallas de las ODS
    "ux_materiales_nombre": "Ya existe un material con ese nombre.",
    "materiales_codigo_key": "Ya existe un material con ese número de parte.",
    "materiales_tipo_check": "Indica si es repuesto, herramienta o insumo.",
    "actividades_materiales_pkey": "Hay un material repetido en la lista.",
    "ordenes_materiales_pkey": "Hay un material repetido en la lista.",
    "actividades_materiales_cantidad": "La cantidad de cada material debe ser mayor que cero.",
    "ordenes_materiales_cantidad": "La cantidad de cada material debe ser mayor que cero.",
    "motivos_inoperatividad_nombre_key": "Ya existe un motivo con ese nombre.",
    "ordenes_demoras_pkey": "Hay un motivo repetido en los factores externos.",
    "ordenes_demoras_horas": "Las horas de cada motivo deben ser mayores que cero.",
    "ordenes_horas_reparacion_check": "La duración del trabajo debe ser mayor que cero.",
    "ordenes_item_solo_correctiva": "Solo las ODS correctivas indican qué falló.",
    "ordenes_fuera_servicio_fechas": "Las horas de fuera de servicio solo se indican si quedó fuera de servicio.",
    "ordenes_fuera_servicio_orden": "La hora en que volvió a operar no puede ser anterior a la hora en que quedó fuera de servicio.",
    # Operaciones
    "clientes_nombre_key": "Ya existe un cliente con ese nombre.",
    "clientes_rif_key": "Ya existe un cliente con ese RIF.",
    "buques_imo_key": "Ya existe un buque con ese IMO.",
    "buques_imo_valido": "El IMO no es válido: son 7 dígitos y el último es verificador. Revisa que esté bien escrito.",
    "ux_buques_nombre_sin_imo": "Ya existe un buque sin IMO con ese nombre.",
    "buques_arqueo_positivo": "El arqueo bruto debe ser mayor que cero.",
    "buques_eslora_positiva": "La eslora debe ser mayor que cero.",
    "buques_manga_positiva": "La manga debe ser mayor que cero.",
    "ubicaciones_nombre_key": "Ya existe una ubicación con ese nombre.",
    "tipos_servicio_nombre_key": "Ya existe un tipo de servicio con ese nombre.",
    "marinos_cedula_key": "Ya existe un marino con esa cédula.",
    "ux_viajes_lancha_en_curso": "Esa lancha ya tiene un viaje en curso: debe finalizarse antes de zarpar en otro.",
    "ux_viajes_patron_en_curso": "Ese patrón ya tiene un viaje en curso: debe finalizarse antes de iniciar otro.",
    "ux_tramos_navegando": "La lancha está navegando: registra la llegada antes de otro zarpe.",
    "ux_esperas_en_curso": "Ya hay una espera en curso.",
    "tramos_llegada_despues_de_salida": "La hora de llegada no puede ser anterior a la de salida.",
    "tramos_llegada_con_destino": "Indica a dónde llegó la lancha.",
    "esperas_fin_despues_de_inicio": "El fin de la espera no puede ser anterior a su inicio.",
    "viajes_cancelado_con_motivo": "Indica el motivo de la cancelación.",
}


def mensaje_error(e: psycopg.Error) -> str:
    """Traduce un error de PostgreSQL a un mensaje para el usuario."""
    if isinstance(e, psycopg.errors.RaiseException):
        return e.diag.message_primary or "Operación rechazada."
    restriccion = e.diag.constraint_name
    if restriccion in MENSAJES_RESTRICCION:
        return MENSAJES_RESTRICCION[restriccion]
    if isinstance(e, psycopg.errors.UniqueViolation):
        return "Ya existe un registro con esos datos."
    if isinstance(e, psycopg.errors.ForeignKeyViolation):
        return "No se puede: el registro está relacionado con otros datos."
    if isinstance(e, psycopg.errors.CheckViolation):
        return f"Datos inválidos ({restriccion})."
    return f"Error de base de datos: {e.diag.message_primary or e}"
