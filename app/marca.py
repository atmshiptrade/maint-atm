"""Identidad visual configurable: colores y logos (módulo Configuración).

Se lee de la base y queda en memoria; al guardar se invalida. El contenedor
arranca un solo proceso, así que el cambio se ve de inmediato; si algún día se
usan varios, cada uno lo toma en a lo sumo DURACION_CACHE segundos.

Los colores se guardan como #RRGGBB y se inyectan como variables CSS en base.html
(las mismas que define app.css). Los tonos derivados (más oscuro, más suave) se
calculan aquí y, para la vista previa, igual en configuracion.html.
"""
import io
import re
import time

from markupsafe import Markup
from PIL import Image, ImageOps, UnidentifiedImageError

from .db import pool

# (clave, variable CSS, nombre, para qué se usa, valor original del logo ATM)
COLORES = [
    ("principal", "--color-principal", "Color principal", "Botones, enlaces y pestañas.", "#192A89"),
    ("menu", "--color-menu", "Menú lateral y títulos",
     "Fondo del menú, de la barra superior en la tablet y de la página de ingreso; también los títulos.", "#131F66"),
    ("acento", "--color-acento", "Color de acento",
     "Íconos, opción activa del menú, casillas marcadas y el borde del campo donde se escribe.", "#00A7BF"),
    ("secundario", "--color-secundario", "Color secundario",
     "Nombres de las secciones del menú y detalles decorativos de la página de ingreso.", "#A9CABD"),
    ("fondo", "--color-fondo", "Fondo de las páginas", "Detrás de las tarjetas y las tablas.", "#F2F6F4"),
]
ORIGINALES = {clave: valor for clave, _, _, _, valor in COLORES}

# clave → (nombre, indicación, archivo original)
LOGOS = {
    "logo_menu": ("Logo del menú", "Va sobre el color del menú lateral: usa una versión blanca o clara, "
                                   "con fondo transparente (PNG).", "/static/img/logo-blanco.png"),
    "logo": ("Logo a color", "Se ve en la página de ingreso (sobre fondo blanco) y como ícono de la "
                             "pestaña del navegador.", "/static/img/logo.png"),
}

DURACION_CACHE = 60
MAX_BYTES_LOGO = 5 * 1024 * 1024
LADO_MAXIMO_LOGO = 1200
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")

_cache: dict | None = None
_leido_en = 0.0


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def mezclar(color: str, con: str, proporcion: float) -> str:
    """`proporcion` de `con` sobre `color` (para tonos más oscuros o más suaves)."""
    a, b = _rgb(color), _rgb(con)
    return "#" + "".join(f"{round(x + (y - x) * proporcion):02X}" for x, y in zip(a, b))


def _luminancia(color: str) -> float:
    canales = [c / 255 for c in _rgb(color)]
    lineal = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in canales]
    return 0.2126 * lineal[0] + 0.7152 * lineal[1] + 0.0722 * lineal[2]


def contraste(a: str, b: str) -> float:
    """Relación de contraste WCAG entre dos colores (1 a 21)."""
    claro, oscuro = sorted((_luminancia(a), _luminancia(b)), reverse=True)
    return (claro + 0.05) / (oscuro + 0.05)


def validar_colores(form) -> dict:
    colores = {}
    for clave, _, nombre, _, _ in COLORES:
        valor = (form.get(f"color_{clave}") or "").strip()
        if not _HEX.match(valor):
            raise ValueError(f"«{nombre}» no es un color válido.")
        colores[clave] = valor.upper()
    return colores


def avisos_contraste(colores: dict) -> list[str]:
    """Combinaciones que se leerían mal (no impiden guardar)."""
    avisos = []
    if contraste(colores["principal"], "#FFFFFF") < 3:
        avisos.append("El texto blanco de los botones se leerá mal sobre el color principal: elige uno más oscuro.")
    if contraste(colores["menu"], "#FFFFFF") < 4.5:
        avisos.append("El texto blanco del menú se leerá mal: el color del menú debería ser más oscuro.")
    if contraste(colores["fondo"], "#1F2A44") < 7:
        avisos.append("El fondo de las páginas es oscuro para el texto: elige uno más claro.")
    return avisos


def _variables(colores: dict) -> dict:
    """Variables CSS a partir de los colores elegidos (mismo cálculo que la vista previa)."""
    derivados = {
        "--color-menu-oscuro": mezclar(colores["menu"], "#000000", 0.2),
        "--color-acento-oscuro": mezclar(colores["acento"], "#000000", 0.19),
        "--color-principal-suave": mezclar(colores["principal"], "#FFFFFF", 0.9),
        "--color-principal-borde": mezclar(colores["principal"], "#FFFFFF", 0.7),
        "--color-acento-suave": mezclar(colores["acento"], "#FFFFFF", 0.87),
    }
    variables = {variable: colores[clave] for clave, variable, *_ in COLORES} | derivados
    # Bootstrap necesita algunos colores como "r, g, b"
    for variable in ("--color-principal", "--color-menu", "--color-menu-oscuro", "--color-acento",
                     "--color-acento-oscuro", "--color-secundario"):
        variables[f"{variable}-rgb"] = ", ".join(str(c) for c in _rgb(variables[variable]))
    return variables


def actual() -> dict:
    """Colores y logos vigentes, listos para base.html."""
    global _cache, _leido_en
    if _cache is not None and time.monotonic() - _leido_en < DURACION_CACHE:
        return _cache
    with pool.connection() as conn:
        fila = conn.execute("SELECT valor FROM configuracion WHERE clave = 'colores'").fetchone()
        logos = {f["clave"]: f["version"] for f in conn.execute(
            "SELECT clave, left(md5(contenido), 12) AS version FROM configuracion_imagenes")}
    # Un color faltante o dañado vuelve al original
    guardados = fila["valor"] if fila else {}
    colores = {clave: (guardados.get(clave) if _HEX.match(str(guardados.get(clave, ""))) else original)
               for clave, original in ORIGINALES.items()}
    variables = _variables(colores)
    _cache = {
        "colores": colores,
        "personalizados": colores != ORIGINALES,
        "estilo": Markup(":root { " + " ".join(f"{k}: {v};" for k, v in variables.items()) + " }"),
        "logos": {clave: (f"/marca/{clave}.png?v={logos[clave]}" if clave in logos else original)
                  for clave, (_, _, original) in LOGOS.items()},
        "logos_propios": set(logos),
    }
    _leido_en = time.monotonic()
    return _cache


def invalidar() -> None:
    global _cache
    _cache = None


def procesar_logo(contenido: bytes, nombre: str) -> tuple[bytes, int, int]:
    """PNG con transparencia, máximo LADO_MAXIMO_LOGO px por lado. Rechaza lo que no sea imagen."""
    if len(contenido) > MAX_BYTES_LOGO:
        raise ValueError(f"«{nombre}» pesa más de 5 MB.")
    try:
        with Image.open(io.BytesIO(contenido)) as imagen:
            if imagen.format not in ("PNG", "JPEG", "WEBP", "GIF"):
                raise UnidentifiedImageError
            imagen = ImageOps.exif_transpose(imagen).convert("RGBA")
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError):
        raise ValueError(f"«{nombre}» no es una imagen que se pueda usar (PNG, JPG o WebP).") from None
    imagen.thumbnail((LADO_MAXIMO_LOGO, LADO_MAXIMO_LOGO), Image.Resampling.LANCZOS)
    salida = io.BytesIO()
    imagen.save(salida, "PNG", optimize=True)
    return salida.getvalue(), imagen.width, imagen.height
