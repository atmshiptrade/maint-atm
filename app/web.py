"""Plantillas, mensajes y lectura de formularios."""
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import psycopg
from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from . import marca
from .db import mensaje_error
from .seguridad import MODULOS, ROLES, ROLES_MANTENIMIENTO, ROLES_OPERACIONES, nombre_roles, tiene_rol, token_csrf

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

# Menú lateral: (sección, [(url, texto, ícono Tabler, roles que lo ven; None = todos)])
# Cada sección es un módulo del sistema; los módulos nuevos se agregan como otra sección.
# Solo se ven las opciones de los módulos en los que el usuario tiene un rol.
MENU = [
    ("Mantenimiento", [
        ("/", "Inicio", "layout-dashboard", ROLES_MANTENIMIENTO),
        ("/bitacora", "Bitácora", "notebook", ROLES_MANTENIMIENTO),
        ("/ordenes", "Órdenes de servicio", "tool", ROLES_MANTENIMIENTO),
        ("/programa", "Programa", "calendar-event", ROLES_MANTENIMIENTO),
        ("/maquinaria", "Maquinaria", "engine", ROLES_MANTENIMIENTO),
    ]),
    ("Operaciones", [
        ("/operaciones", "Disponibilidad", "anchor", ROLES_OPERACIONES),
        ("/viajes", "Reportes de viaje", "route", ROLES_OPERACIONES),
    ]),
    ("Desarrollador", [
        ("/data", "Data", "database", ("SUPERVISOR", "SUPERVISOR_OPS")),
        ("/usuarios", "Usuarios", "users", ("ADMIN",)),
        ("/configuracion", "Configuración", "settings", ("ADMIN",)),
    ]),
]


# Páginas que también marcan como activo un enlace del menú
ACTIVO_TAMBIEN = {"/maquinaria": ("/lanchas", "/generadores")}


def _menu_visible(usuario: dict | None) -> list:
    """Solo las secciones y enlaces que el rol del usuario puede ver."""
    if not usuario:
        return []
    secciones = []
    for titulo, enlaces in MENU:
        visibles = [(url, texto, icono, (url, *ACTIVO_TAMBIEN.get(url, ()))) for url, texto, icono, roles in enlaces
                    if tiene_rol(usuario, *roles)]
        if visibles:
            secciones.append((titulo, visibles))
    return secciones

ESTADOS_TAREA = ["AL DIA", "PROXIMA", "VENCIDA", "FUERA DE TOLERANCIA", "INACTIVA"]
ESTADOS_OT = ["ABIERTA", "EN_PROCESO", "EJECUTADA", "CERRADA", "CANCELADA"]
OT_PENDIENTES = ["ABIERTA", "EN_PROCESO", "EJECUTADA"]
PRIORIDADES = ["CRITICA", "ALTA", "MEDIA", "BAJA"]


def _num(valor, decimales=1):
    if valor is None or valor == "":
        return "—"
    return f"{Decimal(valor):.{decimales}f}"


def _fecha(valor):
    if not valor:
        return "—"
    if isinstance(valor, str):
        valor = date.fromisoformat(valor)
    return valor.strftime("%d/%m/%Y")


def _fechahora(valor: datetime | None):
    return valor.strftime("%d/%m/%Y %H:%M") if valor else "—"


def _hora(valor: datetime | None, dia: date | None = None):
    """08:30; si cae en otro día que el indicado, 17/10 01:15."""
    if not valor:
        return "—"
    return valor.strftime("%d/%m %H:%M" if dia and valor.date() != dia else "%H:%M")


def _duracion(valor: timedelta | None):
    if valor is None:
        return "—"
    horas, minutos = divmod(int(valor.total_seconds() // 60), 60)
    return f"{horas} h {minutos:02d} min" if horas else f"{minutos} min"


def _valor_fechahora(valor: datetime | None):
    """Para <input type="datetime-local">."""
    return valor.strftime("%Y-%m-%dT%H:%M") if valor else ""


def _cantidad(valor):
    """2, 1.5, 0.25: sin ceros de más. Lo que no es número se devuelve tal cual (para re-mostrar)."""
    if valor is None or valor == "":
        return ""
    try:
        return format(Decimal(str(valor)).normalize(), "f")
    except InvalidOperation:
        return valor


def _horas(valor, decimales=1):
    """12.5 h (o — si no hay dato)."""
    if valor is None or valor == "":
        return "—"
    return f"{Decimal(valor):.{decimales}f} h"


templates.env.filters.update(num=_num, fecha=_fecha, fechahora=_fechahora, hora=_hora, duracion=_duracion,
                             valor_fechahora=_valor_fechahora, cantidad=_cantidad, horas=_horas)
templates.env.globals.update(
    ROLES=ROLES, MODULOS=MODULOS, nombre_roles=nombre_roles, ESTADOS_TAREA=ESTADOS_TAREA, ESTADOS_OT=ESTADOS_OT, PRIORIDADES=PRIORIDADES
)


def render(request: Request, plantilla: str, contexto: dict | None = None, status_code: int = 200):
    token = token_csrf(request)
    usuario = getattr(request.state, "usuario", None)
    ctx = {
        "usuario": usuario,
        "iniciales": "".join(p[0] for p in usuario["nombre"].split()[:2]).upper() if usuario else "",
        "csrf_input": Markup(f'<input type="hidden" name="csrf" value="{escape(token)}">'),
        "mensajes": request.session.pop("mensajes", []),
        "menu": _menu_visible(usuario),
        "tiene_rol": tiene_rol,
        "marca": marca.actual(),
    }
    ctx.update(contexto or {})
    return templates.TemplateResponse(request, plantilla, ctx, status_code=status_code)


def avisar(request: Request, texto: str, tipo: str = "success") -> None:
    """Mensaje que se muestra en la siguiente página (tipo: success, warning, danger, info)."""
    mensajes = request.session.get("mensajes", [])
    mensajes.append({"texto": texto, "tipo": tipo})
    request.session["mensajes"] = mensajes


def redirigir(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


# ---------------------------------------------------------------------------
# Lectura de formularios
# ---------------------------------------------------------------------------
class ErrorFormulario(ValueError):
    pass


ERRORES = (ErrorFormulario, psycopg.Error)


def texto_error(e: Exception) -> str:
    return mensaje_error(e) if isinstance(e, psycopg.Error) else str(e)


def campo_texto(form, nombre: str, etiqueta: str = "", requerido: bool = False) -> str | None:
    valor = str(form.get(nombre) or "").strip()
    if not valor:
        if requerido:
            raise ErrorFormulario(f"«{etiqueta or nombre}» es obligatorio.")
        return None
    return valor


def leer_numero(valor: str, etiqueta: str) -> Decimal:
    try:
        numero = Decimal(valor.strip().replace(",", "."))
    except InvalidOperation:
        numero = None
    if numero is None or not numero.is_finite():
        raise ErrorFormulario(f"«{etiqueta}» debe ser un número.")
    return numero


def campo_numero(form, nombre: str, etiqueta: str = "", requerido: bool = False) -> Decimal | None:
    valor = campo_texto(form, nombre, etiqueta, requerido)
    return None if valor is None else leer_numero(valor, etiqueta or nombre)


def campo_filas(form, *nombres: str) -> list[tuple[str, ...]]:
    """Filas de una lista que el usuario arma en el formulario (varios campos con el mismo nombre,
    uno por columna). Devuelve una tupla por fila y omite las filas que quedaron vacías."""
    columnas = [form.getlist(n) for n in nombres]
    filas = []
    for i in range(len(columnas[0])):
        fila = tuple(str(c[i]).strip() if i < len(c) else "" for c in columnas)
        if any(fila):
            filas.append(fila)
    return filas


def campo_entero(form, nombre: str, etiqueta: str = "", requerido: bool = False) -> int | None:
    numero = campo_numero(form, nombre, etiqueta, requerido)
    if numero is None:
        return None
    if numero != int(numero):
        raise ErrorFormulario(f"«{etiqueta or nombre}» debe ser un número entero.")
    return int(numero)


def campo_fecha(form, nombre: str, etiqueta: str = "", requerido: bool = False) -> date | None:
    valor = campo_texto(form, nombre, etiqueta, requerido)
    if valor is None:
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        raise ErrorFormulario(f"«{etiqueta or nombre}» no es una fecha válida.") from None


def campo_fechahora(form, nombre: str, etiqueta: str = "", requerido: bool = False) -> datetime | None:
    """Fecha y hora local de un <input type="datetime-local">."""
    valor = campo_texto(form, nombre, etiqueta, requerido)
    if valor is None:
        return None
    try:
        return datetime.fromisoformat(valor)
    except ValueError:
        raise ErrorFormulario(f"«{etiqueta or nombre}» no es una fecha y hora válida.") from None


def campo_opcion(form, nombre: str, opciones, etiqueta: str = "", requerido: bool = True) -> str | None:
    valor = campo_texto(form, nombre, etiqueta, requerido)
    if valor is not None and valor not in opciones:
        raise ErrorFormulario(f"«{etiqueta or nombre}» tiene un valor no válido.")
    return valor


def campo_casilla(form, nombre: str) -> bool:
    return form.get(nombre) in ("on", "1", "true")


def param_entero(request: Request, nombre: str) -> int | None:
    """Parámetro opcional de la URL (filtros). Lo inválido se ignora."""
    try:
        return int(request.query_params.get(nombre, ""))
    except ValueError:
        return None


def param_fecha(request: Request, nombre: str) -> date | None:
    """Fecha opcional de la URL (AAAA-MM-DD). Lo inválido se ignora."""
    try:
        return date.fromisoformat(request.query_params.get(nombre, ""))
    except ValueError:
        return None
