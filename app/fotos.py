"""Fotos de las ODS: se reducen y se guardan en el volumen de fotos.

La tablet ya las comprime antes de subirlas (ahorra datos en el muelle); aquí se
vuelven a procesar siempre: se endereza según la orientación de la cámara, se
limita a 1920 px por lado, se guarda como JPEG de buena calidad sin metadatos
(incluida la ubicación GPS) y se genera una miniatura para las galerías.
"""
import io
import uuid
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from . import config
from .web import ErrorFormulario

Image.MAX_IMAGE_PIXELS = 60_000_000   # fotos de hasta ~60 MP; más grande se rechaza

LADO_MAXIMO = 1920
LADO_MINIATURA = 480
CALIDAD = 82
MAX_BYTES_ORIGINAL = 25 * 1024 * 1024
MAX_FOTOS_POR_ENVIO = 12

CARPETA = Path(config.FOTOS_DIR)


def _jpeg(imagen: Image.Image, lado: int, calidad: int) -> bytes:
    copia = imagen.copy()
    copia.thumbnail((lado, lado), Image.Resampling.LANCZOS)
    salida = io.BytesIO()
    copia.save(salida, "JPEG", quality=calidad, optimize=True, progressive=True)
    return salida.getvalue()


def procesar(contenido: bytes, nombre: str) -> tuple[bytes, bytes, int, int]:
    """Devuelve (foto, miniatura, ancho, alto). Rechaza lo que no sea una imagen válida."""
    if len(contenido) > MAX_BYTES_ORIGINAL:
        raise ErrorFormulario(f"«{nombre}» pesa más de 25 MB.")
    try:
        with Image.open(io.BytesIO(contenido)) as imagen:
            imagen = ImageOps.exif_transpose(imagen).convert("RGB")
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError):
        raise ErrorFormulario(f"«{nombre}» no es una imagen que se pueda leer (usa JPG o PNG).") from None
    foto = _jpeg(imagen, LADO_MAXIMO, CALIDAD)
    miniatura = _jpeg(imagen, LADO_MINIATURA, 75)
    ancho, alto = Image.open(io.BytesIO(foto)).size
    return foto, miniatura, ancho, alto


def ruta(archivo: str, miniatura: bool = False) -> Path:
    if miniatura:
        archivo = archivo.removesuffix(".jpg") + "_mini.jpg"
    destino = (CARPETA / archivo).resolve()
    if not destino.is_relative_to(CARPETA.resolve()):
        raise ValueError("Ruta de foto fuera de la carpeta de fotos")
    return destino


def guardar(orden_id: int, contenido: bytes, nombre: str) -> dict:
    """Procesa y escribe la foto en disco. Devuelve los datos para registrarla en la base."""
    foto, miniatura, ancho, alto = procesar(contenido, nombre)
    archivo = f"{orden_id}/{uuid.uuid4().hex}.jpg"
    ruta(archivo).parent.mkdir(parents=True, exist_ok=True)
    ruta(archivo).write_bytes(foto)
    ruta(archivo, miniatura=True).write_bytes(miniatura)
    return {"archivo": archivo, "nombre_original": nombre[:200], "ancho": ancho, "alto": alto, "bytes": len(foto)}


def eliminar(archivo: str) -> None:
    for destino in (ruta(archivo), ruta(archivo, miniatura=True)):
        destino.unlink(missing_ok=True)


def subidas(form) -> list:
    """Archivos elegidos en el campo "fotos" (el navegador manda uno vacío si no se eligió nada)."""
    archivos = [f for f in form.getlist("fotos") if getattr(f, "filename", "")]
    if len(archivos) > MAX_FOTOS_POR_ENVIO:
        raise ErrorFormulario(f"Sube como máximo {MAX_FOTOS_POR_ENVIO} fotos a la vez.")
    return archivos
