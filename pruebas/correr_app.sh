#!/bin/sh
# Prueba la aplicación completa SIN tocar tus datos: levanta una segunda instancia en el
# puerto 8003 (copia piloto: el proyecto original usa el 8001) sobre una base temporal, corre pruebas/humo_app.py con los tres roles y
# borra todo al terminar.   ./pruebas/correr_app.sh
set -eu
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
BASE_PRUEBA=mantenimiento_prueba
CLAVE=$(python3 -c "import secrets; print(secrets.token_urlsafe(12))")

limpiar() {
    docker rm -f app-prueba-piloto >/dev/null 2>&1 || true
    docker compose exec -T db dropdb -U mantenimiento --if-exists "$BASE_PRUEBA" >/dev/null 2>&1 || true
}
limpiar
TEMPORAL=$(mktemp -d)
# DEJAR=1 ./pruebas/correr_app.sh  deja la instancia de prueba encendida para revisarla en http://localhost:8003
[ "${DEJAR:-}" = "1" ] || trap 'limpiar; rm -rf "$TEMPORAL"' EXIT

docker compose exec -T db createdb -U mantenimiento "$BASE_PRUEBA"
# Las fotos de prueba van a una carpeta temporal dentro del contenedor, no al volumen real
docker compose run -d --rm --name app-prueba-piloto -p 127.0.0.1:8003:8000 \
    -e DATABASE_URL="postgresql://mantenimiento:${POSTGRES_PASSWORD}@db:5432/${BASE_PRUEBA}" \
    -e ADMIN_USUARIO=admin -e ADMIN_PASSWORD="$CLAVE" -e FOTOS_DIR=/tmp/fotos-prueba \
    app >/dev/null
for _ in $(seq 1 60); do curl -sf http://127.0.0.1:8003/salud >/dev/null && break; sleep 1; done

# Foto de prueba: 4000x3000, pesada y girada (orientación EXIF 6), como las de una tablet
docker exec app-prueba-piloto python -c "
import io, sys
from PIL import Image
img = Image.effect_noise((4000, 3000), 60).convert('RGB')
exif = img.getexif(); exif[0x0112] = 6
buf = io.BytesIO(); img.save(buf, 'JPEG', quality=95, exif=exif); sys.stdout.buffer.write(buf.getvalue())
" > "$TEMPORAL/foto.jpg"

python3 pruebas/humo_app.py "$CLAVE" "$TEMPORAL/foto.jpg" "$BASE_PRUEBA" | grep -vE "^  OK  " || true
echo "errores del servidor: $(docker logs app-prueba-piloto 2>&1 | grep -c 'Exception in ASGI' || true)"
docker logs app-prueba-piloto 2>&1 | grep -E "^[A-Za-z]*Error" | tail -3 || true
