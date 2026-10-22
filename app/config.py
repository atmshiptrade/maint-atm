import os

DATABASE_URL = os.environ["DATABASE_URL"]
SECRET_KEY = os.environ["SECRET_KEY"]
ZONA_HORARIA = os.environ.get("TZ", "UTC")
# true en producción (HTTPS): la cookie de sesión solo viaja cifrada
COOKIE_SEGURA = os.environ.get("COOKIE_SEGURA", "false").lower() == "true"
# Cada cuánto se revisan las tareas por calendario para abrir ODS
INTERVALO_JOB_SEGUNDOS = int(os.environ.get("INTERVALO_JOB_SEGUNDOS", "3600"))
# Dónde se guardan las fotos de las ODS (volumen de Docker)
FOTOS_DIR = os.environ.get("FOTOS_DIR", "/srv/datos/fotos")
