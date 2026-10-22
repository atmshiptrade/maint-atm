-- =====================================================================
--  Vacía los datos de trabajo para empezar a cargar información real.
--  BORRA: lanchas, generadores, actividades (con sus materiales previstos),
--         programa, bitácoras (con sus correcciones), ODS (con su historial,
--         materiales usados, motivos de demora y el registro de fotos) y
--         reportes de viaje (con su trayecto, esperas y enmiendas).
--  CONSERVA: usuarios (excepto los de prueba) con sus roles, categorías,
--            ítems de inspección, proveedores, herramientas y repuestos, motivos
--            de inoperatividad, clientes, buques, ubicaciones,
--            tipos de servicio, marinos y la configuración (colores y logos).
--  Los correlativos y los códigos (LAN-001, GEN-001, PLC-0001) vuelven a empezar en 1.
--  Los archivos de las fotos se borran aparte (ver README).
--
--  docker compose exec db psql -U mantenimiento -d mantenimiento -f /db/scripts/vaciar_datos.sql
-- =====================================================================
\set ON_ERROR_STOP on
BEGIN;

TRUNCATE viajes_enmiendas, viajes_esperas, viajes_tramos, viajes_marinos, viajes_areas, viajes_servicios, viajes,
         ordenes_demoras, ordenes_materiales, ordenes_fotos, ordenes_historial, ordenes,
         bitacora_cambios, bitacora_respuestas, actividades_materiales,
         bitacora_lecturas, bitacora, tareas, actividades, equipos, lanchas RESTART IDENTITY CASCADE;

-- Usuarios e ítem creados por las pruebas automáticas
DELETE FROM usuarios WHERE usuario IN ('mecanico1', 'supervisor1');
DELETE FROM items_inspeccion WHERE nombre = 'Ancla y cadena';

COMMIT;

SELECT (SELECT count(*) FROM lanchas)          AS lanchas,
       (SELECT count(*) FROM equipos)          AS generadores_y_horometros,
       (SELECT count(*) FROM ordenes)          AS ordenes,
       (SELECT count(*) FROM usuarios)         AS usuarios,
       (SELECT count(*) FROM items_inspeccion) AS items_inspeccion;
