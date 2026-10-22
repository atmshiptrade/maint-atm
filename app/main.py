import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import config
from .db import pool
from .routers import (actividades, bitacora, catalogos, configuracion, data, generadores, inicio, items, lanchas,
                      operaciones, ordenes, programa, sesion, usuarios, viajes)
from .seguridad import NoAutenticado
from .web import redirigir, render

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("mantenimiento")


def _generar_preventivas() -> None:
    with pool.connection() as conn:
        creadas = conn.execute("SELECT id FROM generar_ordenes_preventivas()").fetchall()
    if creadas:
        log.info("Job periódico: %s ODS preventivas generadas", len(creadas))


async def job_periodico() -> None:
    """Las tareas por calendario vencen aunque nadie registre bitácora."""
    while True:
        try:
            await asyncio.to_thread(_generar_preventivas)
        except Exception:
            log.exception("Falló la generación periódica de ODS preventivas")
        await asyncio.sleep(config.INTERVALO_JOB_SEGUNDOS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool.open(wait=True)
    tarea = asyncio.create_task(job_periodico())
    yield
    tarea.cancel()
    pool.close()


app = FastAPI(
    title="Mantenimiento de embarcaciones",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SECRET_KEY,
    session_cookie="sesion",
    max_age=12 * 3600,
    same_site="lax",
    https_only=config.COOKIE_SEGURA,
)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

# catalogos va al final: /data/<clave> solo atiende lo que no tiene pantalla propia
for modulo in (sesion, inicio, lanchas, generadores, actividades, programa, bitacora, ordenes, items, data,
               usuarios, configuracion, operaciones, viajes, catalogos):
    app.include_router(modulo.router)


@app.exception_handler(NoAutenticado)
async def no_autenticado(request: Request, exc: NoAutenticado):
    return redirigir("/login")


@app.exception_handler(StarletteHTTPException)
async def error_http(request: Request, exc: StarletteHTTPException):
    detalle = {404: "La página o el registro no existe."}.get(exc.status_code, exc.detail)
    return render(request, "error.html", {"codigo": exc.status_code, "detalle": detalle}, exc.status_code)


@app.get("/actividades", include_in_schema=False)
def actividades_movidas():
    """Las actividades ahora viven en el módulo Data."""
    return RedirectResponse("/data/actividades", status_code=301)


@app.get("/maquinaria", include_in_schema=False)
def maquinaria():
    """Flota → Maquinaria: lanchas y generadores en un solo módulo (empieza en lanchas)."""
    return RedirectResponse("/lanchas", status_code=303)


@app.get("/salud", include_in_schema=False)
def salud():
    return {"ok": True}
