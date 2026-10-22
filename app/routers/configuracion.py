"""Configuración del sistema (solo administrador): colores y logos."""
import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from psycopg.types.json import Jsonb

from .. import marca
from ..db import get_db
from ..seguridad import formulario, requiere
from ..web import avisar, redirigir, render, texto_error

router = APIRouter()


def _render(request: Request, error: str | None = None, colores: dict | None = None, status_code: int = 200):
    vigente = marca.actual()
    return render(request, "configuracion.html", {
        "colores_def": marca.COLORES,
        "logos_def": marca.LOGOS,
        "colores": colores or vigente["colores"],
        "originales": marca.ORIGINALES,
        "error": error,
    }, status_code)


@router.get("/configuracion")
def ver(request: Request, usuario=Depends(requiere("ADMIN"))):
    return _render(request)


@router.post("/configuracion/colores")
def guardar_colores(request: Request, form=Depends(formulario), conn=Depends(get_db),
                    usuario=Depends(requiere("ADMIN"))):
    try:
        colores = marca.validar_colores(form)
        with conn.transaction():
            conn.execute(
                """INSERT INTO configuracion (clave, valor, actualizado_por) VALUES ('colores', %s, %s)
                   ON CONFLICT (clave) DO UPDATE
                      SET valor = EXCLUDED.valor, actualizado_en = now(), actualizado_por = EXCLUDED.actualizado_por""",
                (Jsonb(colores), usuario["id"]),
            )
        conn.commit()
    except (ValueError, psycopg.Error) as e:
        return _render(request, texto_error(e), {c: form.get(f"color_{c}") for c in marca.ORIGINALES}, 422)
    marca.invalidar()
    avisar(request, "Colores guardados.")
    for aviso in marca.avisos_contraste(colores):
        avisar(request, aviso, "warning")
    return redirigir("/configuracion")


@router.post("/configuracion/colores/restaurar")
def restaurar_colores(request: Request, form=Depends(formulario), conn=Depends(get_db),
                      usuario=Depends(requiere("ADMIN"))):
    conn.execute("DELETE FROM configuracion WHERE clave = 'colores'")
    conn.commit()
    marca.invalidar()
    avisar(request, "Se restauraron los colores originales.")
    return redirigir("/configuracion")


@router.post("/configuracion/logos/{clave}")
def subir_logo(clave: str, request: Request, form=Depends(formulario), conn=Depends(get_db),
               usuario=Depends(requiere("ADMIN"))):
    if clave not in marca.LOGOS:
        raise HTTPException(404)
    archivo = form.get("archivo")
    try:
        if not getattr(archivo, "filename", ""):
            raise ValueError("Elige la imagen del logo.")
        contenido, ancho, alto = marca.procesar_logo(archivo.file.read(), archivo.filename)
        with conn.transaction():
            conn.execute(
                """INSERT INTO configuracion_imagenes (clave, contenido, tipo, ancho, alto, actualizado_por)
                   VALUES (%s, %s, 'image/png', %s, %s, %s)
                   ON CONFLICT (clave) DO UPDATE
                      SET contenido = EXCLUDED.contenido, ancho = EXCLUDED.ancho, alto = EXCLUDED.alto,
                          actualizado_en = now(), actualizado_por = EXCLUDED.actualizado_por""",
                (clave, contenido, ancho, alto, usuario["id"]),
            )
        conn.commit()
    except (ValueError, psycopg.Error) as e:
        return _render(request, texto_error(e), status_code=422)
    marca.invalidar()
    avisar(request, f"{marca.LOGOS[clave][0]} actualizado.")
    return redirigir("/configuracion")


@router.post("/configuracion/logos/{clave}/restaurar")
def restaurar_logo(clave: str, request: Request, form=Depends(formulario), conn=Depends(get_db),
                   usuario=Depends(requiere("ADMIN"))):
    if clave not in marca.LOGOS:
        raise HTTPException(404)
    conn.execute("DELETE FROM configuracion_imagenes WHERE clave = %s", (clave,))
    conn.commit()
    marca.invalidar()
    avisar(request, f"Se restauró el {marca.LOGOS[clave][0].lower()} original.")
    return redirigir("/configuracion")


@router.get("/marca/{clave}.png", include_in_schema=False)
def logo(clave: str, request: Request, conn=Depends(get_db)):
    """Público: el logo también se ve en la página de ingreso."""
    fila = conn.execute(
        "SELECT contenido, left(md5(contenido), 12) AS version FROM configuracion_imagenes WHERE clave = %s",
        (clave,),
    ).fetchone()
    if not fila:
        raise HTTPException(404)
    # La URL lleva la versión (?v=…): mientras no cambie el logo, el navegador lo guarda
    fija = request.query_params.get("v") == fila["version"]
    return Response(fila["contenido"], media_type="image/png", headers={
        "Cache-Control": "public, max-age=31536000, immutable" if fija else "no-cache",
        "X-Content-Type-Options": "nosniff",
    })
