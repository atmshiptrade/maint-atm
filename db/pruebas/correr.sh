#!/bin/sh
# Prueba el esquema completo en una base TEMPORAL (tus datos reales no se tocan):
# crea "mantenimiento_prueba", aplica todas las migraciones, corre el flujo y la borra.
#   ./db/pruebas/correr.sh
set -eu
cd "$(dirname "$0")/../.."
db() { docker compose exec -T db "$@"; }

db dropdb -U mantenimiento --if-exists mantenimiento_prueba
db createdb -U mantenimiento mantenimiento_prueba
trap 'db dropdb -U mantenimiento --if-exists mantenimiento_prueba' EXIT

for migracion in db/migrations/*.sql; do
    db psql -q -U mantenimiento -d mantenimiento_prueba -v ON_ERROR_STOP=1 -1 -f - < "$migracion"
done
db psql -U mantenimiento -d mantenimiento_prueba -f - < db/pruebas/flujo_pacific.sql
db psql -U mantenimiento -d mantenimiento_prueba -v ON_ERROR_STOP=1 -f - < db/pruebas/flujo_operaciones.sql
db psql -U mantenimiento -d mantenimiento_prueba -v ON_ERROR_STOP=1 -f - < db/pruebas/flujo_tiempos.sql
