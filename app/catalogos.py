"""Catálogos simples de Data: una definición por catálogo y unas mismas pantallas para todos.

Cada catálogo dice su tabla, sus campos y quién lo administra; el router
`routers/catalogos.py` hace la lista, el alta (también por JSON, para crear al
vuelo desde otro formulario), la edición y la baja. Un registro que ya se usó
no se elimina: se desactiva. Los catálogos de Mantenimiento más antiguos
(actividades, categorías, ítems, proveedores) tienen sus propias pantallas; los nuevos de Mantenimiento
(materiales, motivos de inoperatividad) ya usan este.
"""
import re
from dataclasses import dataclass, field
from typing import Callable

from .web import ErrorFormulario, campo_numero, campo_opcion, campo_texto


@dataclass(frozen=True)
class Campo:
    nombre: str
    etiqueta: str
    tipo: str = "texto"            # texto, numero, area, opcion, telefono, correo
    requerido: bool = False
    opciones: tuple = ()           # para tipo opcion: ((valor, texto), ...)
    ayuda: str = ""
    attrs: str = ""
    en_lista: bool = True
    ancho: str = "col-12"          # columna en el formulario
    normalizar: Callable | None = None
    sugerencias: tuple | None = None   # texto libre con sugerencias: estas más las ya usadas


@dataclass(frozen=True)
class Catalogo:
    clave: str                     # /data/<clave>
    tabla: str
    titulo: str
    nuevo: str                     # «Nuevo cliente», «Nueva ubicación»
    icono: str
    tono: str
    descripcion: str
    campos: tuple
    roles: tuple                   # quién lo administra
    modulo: str
    uso: str                       # subconsulta que cuenta en cuántos registros se usa (alias t)
    uso_titulo: str = "Viajes"
    uso_texto: str = "ya se usó en reportes"
    activo: str = "activo"         # nombre de la columna activo/activa
    orden: str = "nombre"
    resumen: Callable | None = field(default=None)   # texto corto para la lista (opcional)

    def campo(self, nombre: str) -> Campo:
        return next(c for c in self.campos if c.nombre == nombre)


def _mayusculas(valor):
    return valor.upper() if valor else valor


def _imo(valor):
    """Acepta «IMO 9074729» o con espacios; guarda solo los dígitos."""
    if not valor:
        return None
    digitos = re.sub(r"\D", "", valor)
    if len(digitos) != 7:
        raise ErrorFormulario("El IMO son 7 dígitos.")
    return digitos


_CONTACTO = (
    Campo("contacto", "Persona de contacto", ancho="col-sm-6"),
    Campo("telefono", "Teléfono", tipo="telefono", ancho="col-sm-6"),
    Campo("correo", "Correo", tipo="correo", en_lista=False),
)

TIPOS_MATERIAL = (("REPUESTO", "Repuesto"), ("HERRAMIENTA", "Herramienta"), ("INSUMO", "Insumo (aceites, grasas, líquidos)"))

CATALOGOS = {c.clave: c for c in (
    # Mantenimiento
    Catalogo(
        clave="materiales", tabla="materiales", titulo="Herramientas y repuestos", nuevo="Nuevo material",
        icono="tools", tono="tono-amarillo",
        descripcion="Repuestos, herramientas e insumos. Se eligen de la lista al indicar los materiales de una "
                    "actividad y los que se usaron en una ODS.",
        campos=(Campo("nombre", "Nombre", requerido=True, attrs='maxlength="150" placeholder="Ej. Filtro de aceite Yamaha"'),
                Campo("tipo", "Tipo", tipo="opcion", requerido=True, opciones=TIPOS_MATERIAL, ancho="col-sm-6"),
                Campo("unidad", "Unidad de medida", ancho="col-sm-6", attrs='maxlength="30"',
                      sugerencias=("unidad", "juego", "litro", "galón", "kilogramo", "metro")),
                Campo("codigo", "N° de parte o referencia", attrs='maxlength="60"'),
                Campo("notas", "Notas", tipo="area", en_lista=False)),
        roles=("SUPERVISOR",), modulo="MANTENIMIENTO",
        orden="array_position(ARRAY['REPUESTO', 'HERRAMIENTA', 'INSUMO'], tipo), nombre",
        uso="""((SELECT count(*) FROM actividades_materiales a WHERE a.material_id = t.id)
               + (SELECT count(*) FROM ordenes_materiales o WHERE o.material_id = t.id))""",
        uso_titulo="Usos", uso_texto="ya se usó en actividades u ODS",
    ),
    Catalogo(
        clave="motivos-inoperatividad", tabla="motivos_inoperatividad", titulo="Motivos de inoperatividad",
        nuevo="Nuevo motivo", icono="hourglass", tono="tono-rojo",
        descripcion="Por qué una lancha o un generador estuvo fuera de servicio más tiempo del que duró la reparación "
                    "(el repuesto no se consigue, la falla fue fuera del horario laboral…). Se indican al reportar la ODS.",
        campos=(Campo("nombre", "Motivo", requerido=True, attrs='maxlength="100"'),),
        roles=("SUPERVISOR",), modulo="MANTENIMIENTO",
        uso="(SELECT count(*) FROM ordenes_demoras d WHERE d.motivo_id = t.id)",
        uso_titulo="ODS", uso_texto="ya se usó en ODS",
    ),
    # Operaciones
    Catalogo(
        clave="clientes", tabla="clientes", titulo="Clientes", nuevo="Nuevo cliente", icono="briefcase", tono="tono-marino",
        descripcion="Empresas a las que se presta el servicio («Al servicio de» en el reporte de viaje). "
                    "Los usará también el módulo Comercial.",
        campos=(Campo("nombre", "Nombre o razón social", requerido=True, attrs='maxlength="150"'),
                Campo("rif", "RIF", normalizar=_mayusculas, attrs='maxlength="20" placeholder="J-12345678-9"'),
                *_CONTACTO,
                Campo("notas", "Notas", tipo="area", en_lista=False)),
        roles=("SUPERVISOR_OPS",), modulo="OPERACIONES",
        uso="(SELECT count(*) FROM viajes v WHERE v.cliente_id = t.id)",
    ),
    Catalogo(
        clave="buques", tabla="buques", titulo="Buques", nuevo="Nuevo buque", icono="ship", tono="tono-turquesa",
        descripcion="Buques clientes a los que se presta el servicio. Se registran una vez y se reutilizan en cada viaje.",
        campos=(Campo("nombre", "Nombre del buque", requerido=True, attrs='maxlength="150" placeholder="MV …"'),
                Campo("imo", "IMO", normalizar=_imo, ancho="col-sm-6",
                      ayuda="7 dígitos; el sistema revisa el dígito verificador.",
                      attrs='inputmode="numeric" maxlength="11"'),
                Campo("bandera", "Bandera", ancho="col-sm-6", sugerencias=()),
                Campo("tipo", "Tipo de buque", sugerencias=(), attrs='placeholder="Ej. Granelero, tanquero"'),
                Campo("arqueo_bruto", "Arqueo bruto (GT)", tipo="numero", ancho="col-sm-4",
                      attrs='step="0.1" min="0" inputmode="decimal"'),
                Campo("eslora", "Eslora (m)", tipo="numero", ancho="col-sm-4", attrs='step="0.01" min="0" inputmode="decimal"'),
                Campo("manga", "Manga (m)", tipo="numero", ancho="col-sm-4", en_lista=False,
                      attrs='step="0.01" min="0" inputmode="decimal"'),
                Campo("notas", "Notas", tipo="area", en_lista=False)),
        roles=("SUPERVISOR_OPS",), modulo="OPERACIONES",
        uso="(SELECT count(*) FROM viajes v WHERE v.buque_id = t.id)",
    ),
    Catalogo(
        clave="ubicaciones", tabla="ubicaciones", titulo="Ubicaciones", nuevo="Nueva ubicación", icono="map-pin",
        tono="tono-verde",
        descripcion="Áreas (bahías y zonas) y muelles. Las áreas se marcan en «Área donde se ejecutará el servicio»; "
                    "áreas y muelles son también los puntos del trayecto.",
        campos=(Campo("nombre", "Nombre", requerido=True, attrs='maxlength="100"'),
                Campo("tipo", "Tipo", tipo="opcion", requerido=True,
                      opciones=(("AREA", "Área (bahía o zona)"), ("MUELLE", "Muelle o terminal")))),
        roles=("SUPERVISOR_OPS",), modulo="OPERACIONES", activo="activa", orden="tipo, nombre",
        uso="""((SELECT count(*) FROM viajes_areas a WHERE a.ubicacion_id = t.id)
               + (SELECT count(*) FROM viajes_tramos r WHERE r.destino_id = t.id)
               + (SELECT count(*) FROM viajes v WHERE v.origen_id = t.id))""",
        uso_titulo="Usos",
    ),
    Catalogo(
        clave="tipos-servicio", tabla="tipos_servicio", titulo="Tipos de servicio", nuevo="Nuevo tipo de servicio",
        icono="list-details", tono="tono-naranja",
        descripcion="Opciones de «Tipo de servicio a ser ejecutado». Si no está en la lista, el reporte tiene el campo «Otros».",
        campos=(Campo("nombre", "Nombre", requerido=True, attrs='maxlength="100"'),),
        roles=("SUPERVISOR_OPS",), modulo="OPERACIONES",
        uso="(SELECT count(*) FROM viajes_servicios s WHERE s.tipo_servicio_id = t.id)",
    ),
    Catalogo(
        clave="marinos", tabla="marinos", titulo="Marinos", nuevo="Nuevo marino", icono="users-group", tono="tono-amarillo",
        descripcion="Tripulación que acompaña al patrón. Se eligen en el reporte de viaje; no entran al sistema.",
        campos=(Campo("nombre", "Nombre completo", requerido=True, attrs='maxlength="150"'),
                Campo("cedula", "Cédula", ancho="col-sm-6", normalizar=_mayusculas, attrs='maxlength="20" placeholder="V-12345678"'),
                Campo("telefono", "Teléfono", tipo="telefono", ancho="col-sm-6")),
        roles=("SUPERVISOR_OPS",), modulo="OPERACIONES",
        uso="(SELECT count(*) FROM viajes_marinos m WHERE m.marino_id = t.id)",
    ),
)}


def datos(catalogo: Catalogo, form) -> dict:
    """Lee y valida los campos del catálogo desde un formulario."""
    valores = {}
    for c in catalogo.campos:
        if c.tipo == "numero":
            valor = campo_numero(form, c.nombre, c.etiqueta, c.requerido)
        elif c.tipo == "opcion":
            valor = campo_opcion(form, c.nombre, dict(c.opciones), c.etiqueta, c.requerido)
        else:
            valor = campo_texto(form, c.nombre, c.etiqueta, c.requerido)
        valores[c.nombre] = c.normalizar(valor) if c.normalizar else valor
    return valores


def opciones(conn, catalogo: Catalogo, actual=None, extra: str = "") -> list[dict]:
    """Registros activos para un selector, más el que ya tiene el registro aunque se haya desactivado."""
    actuales = [a for a in (actual if isinstance(actual, (list, tuple, set)) else [actual]) if a not in (None, "")]
    return conn.execute(
        f"""SELECT t.*{extra} FROM {catalogo.tabla} t
             WHERE t.{catalogo.activo} OR t.id = ANY(%s::bigint[]) ORDER BY {catalogo.orden}""",
        ([int(a) for a in actuales],),
    ).fetchall()


def sugerencias(conn, catalogo: Catalogo) -> dict:
    """Para los campos de texto con sugerencias (bandera de un buque, unidad de un material…):
    las sugeridas de entrada más los valores ya usados."""
    resultado = {}
    for c in catalogo.campos:
        if c.sugerencias is not None:
            usados = [f["v"] for f in conn.execute(
                f"SELECT DISTINCT {c.nombre} AS v FROM {catalogo.tabla} WHERE {c.nombre} IS NOT NULL")]
            resultado[c.nombre] = sorted(set(c.sugerencias) | set(usados), key=str.lower)
    return resultado
