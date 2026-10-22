#!/bin/sh
# Respaldo de la base de datos. En el VPS, prográmalo a diario con cron:
#   0 3 * * * /ruta/al/proyecto/deploy/respaldo.sh
# y copia la carpeta respaldos/ FUERA del VPS (rclone a Backblaze B2, S3, Google Drive...).
# Restaurar:  docker compose exec -T db pg_restore -U mantenimiento -d mantenimiento --clean < archivo.dump
set -eu
cd "$(dirname "$0")/.."
mkdir -p respaldos
archivo="respaldos/mantenimiento-$(date +%F_%H%M).dump"
docker compose exec -T db pg_dump -U mantenimiento -Fc mantenimiento > "$archivo"
# Fotos de las ODS (restaurar: docker compose exec -T app tar -C /srv/datos -xzf - < archivo.tar.gz)
fotos="respaldos/fotos-$(date +%F_%H%M).tar.gz"
docker compose exec -T app tar -C /srv/datos -czf - fotos > "$fotos"
find respaldos \( -name '*.dump' -o -name 'fotos-*.tar.gz' \) -mtime +14 -delete
echo "Respaldo creado: $archivo y $fotos"
