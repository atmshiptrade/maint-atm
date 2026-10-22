-- =====================================================================
--  Prueba del flujo completo: lanchas Pacific y Atlántico, y un generador
--  independiente que pasa de una lancha a otra.
--  NO se ejecuta sobre la base real: db/pruebas/correr.sh crea una base
--  temporal, aplica las migraciones, corre esta prueba y la elimina.
--  Las fechas son relativas a hoy (Día 30 = hoy).
-- =====================================================================
\set QUIET on
\set ON_ERROR_STOP on
\pset footer off
\pset null '—'

\set semaforo 'SELECT unidad, actividad, intervalo_horas AS cada_h, horometro_ultimo_servicio AS ultimo, proximo_horometro AS proximo, horometro_actual AS actual, horas_restantes AS faltan_h, estado, ot_pendiente AS ot FROM v_estado_tareas WHERE intervalo_horas IS NOT NULL ORDER BY tipo_equipo DESC, unidad, actividad;'

-- Simula la pantalla de bitácora: todos los ítems en OK salvo las fallas indicadas
CREATE TEMP TABLE fallas_demo (item text, comentario text);
CREATE FUNCTION pg_temp.inspeccion(p_bitacora bigint, p_con_generador boolean) RETURNS void
LANGUAGE sql AS $$
    INSERT INTO bitacora_respuestas (bitacora_id, item_id, resultado, comentario)
    SELECT p_bitacora, i.id, CASE WHEN f.item IS NULL THEN 'OK' ELSE 'FALLA' END, f.comentario
      FROM items_inspeccion i LEFT JOIN fallas_demo f ON f.item = i.nombre
     WHERE i.activo AND (i.aplica_a = 'LANCHA' OR p_con_generador);
$$;

INSERT INTO items_inspeccion (nombre, categoria_id, prioridad, aplica_a)
SELECT 'Nivel de refrigerante del generador', id, 'ALTA', 'GENERADOR' FROM categorias WHERE nombre = 'Generador';

\echo
\echo '================  Catálogo de actividades (compartido por lanchas y generadores)  ================'
INSERT INTO actividades (nombre, categoria_id, severidad, tolerancia_pct, tiempo_estimado_h,
                         intervalo_horas_sugerido, intervalo_dias_sugerido, materiales)
SELECT v.nombre, c.id, v.severidad, 10, v.horas, v.cada_h, v.cada_d, v.materiales
  FROM (VALUES
        ('Cambio de aceite',          'Lubricación',        'ALTA',  1.0, 500,  NULL, 'Aceite 15W-40, filtro de aceite'),
        ('Cambio de correa',          'Mecánica',           'MEDIA', 1.5, 400,  NULL, 'Correa de alternador'),
        ('Cambio de ánodos de zinc',  'Casco y estructura', 'MEDIA', 2.0, NULL, 180,  'Ánodos de zinc (x3)')
       ) AS v(nombre, categoria, severidad, horas, cada_h, cada_d, materiales)
  JOIN categorias c ON c.nombre = v.categoria;

\echo
\echo '================  DÍA 1 · Dos lanchas y un generador independiente  ================'
INSERT INTO lanchas (nombre, matricula) VALUES ('Pacific', 'PA-1234-X'), ('Atlántico', 'PA-5678-Y');
INSERT INTO equipos (lancha_id, tipo, nombre, fecha_alta, horometro_inicial) VALUES
    (1,    'PROPULSION', 'Motores',        current_date - 29, 5000),
    (2,    'PROPULSION', 'Motores',        current_date - 29, 1200),
    (NULL, 'GENERADOR',  'Generador Onan', current_date - 29, 1200);
SELECT coalesce(l.codigo, e.codigo) AS codigo, nombre_unidad(e) AS unidad, e.horometro_actual
  FROM equipos e LEFT JOIN lanchas l ON l.id = e.lancha_id ORDER BY e.id;

\echo '>> Un generador no se puede atar a una lancha:'
DO $$ BEGIN
    INSERT INTO equipos (lancha_id, tipo, nombre, horometro_inicial) VALUES (1, 'GENERADOR', 'Otro', 0);
EXCEPTION WHEN others THEN RAISE NOTICE 'RECHAZADO → %', SQLERRM;
END $$;

\echo
\echo '================  DÍA 1 · Programa: la misma actividad en la lancha y en el generador  ================'
INSERT INTO tareas (equipo_id, actividad_id, intervalo_horas, horometro_ultimo_servicio)
SELECT e.id, a.id, v.cada, e.horometro_inicial
  FROM (VALUES (1, 'Cambio de correa', 400), (1, 'Cambio de aceite', 500), (3, 'Cambio de aceite', 150))
       AS v(equipo, actividad, cada)
  JOIN equipos e ON e.id = v.equipo
  JOIN actividades a ON a.nombre = v.actividad;
:semaforo

\echo
\echo '================  DÍA 15 · Pacific 5370 h (como en el ejemplo original)  ================'
DO $$
DECLARE v_id bigint;
BEGIN
    INSERT INTO bitacora (lancha_id, fecha) VALUES (1, current_date - 15) RETURNING id INTO v_id;
    INSERT INTO bitacora_lecturas (bitacora_id, equipo_id, horometro) VALUES (v_id, 1, 5370);
EXCEPTION WHEN others THEN RAISE NOTICE 'RECHAZADO → %', SQLERRM;
END $$;

\echo
\echo '================  DÍA 25 · Pacific 5370 h con el generador a bordo (1290 h), falla luz de babor  ================'
INSERT INTO bitacora (lancha_id, fecha) VALUES (1, current_date - 5) RETURNING id AS bitacora_id \gset
INSERT INTO bitacora_lecturas (bitacora_id, equipo_id, horometro) VALUES (:bitacora_id, 1, 5370), (:bitacora_id, 3, 1290);
INSERT INTO fallas_demo VALUES ('Luz de navegación babor', 'No enciende');
SELECT pg_temp.inspeccion(:bitacora_id, true) \g /dev/null
SELECT id AS ot, tipo, descripcion FROM procesar_bitacora(:bitacora_id);

\echo
\echo '================  DÍA 28 · El generador pasa a la Atlántico (1340 h) y le falta refrigerante  ================'
INSERT INTO bitacora (lancha_id, fecha) VALUES (1, current_date - 2) RETURNING id AS bitacora_id \gset
INSERT INTO bitacora_lecturas (bitacora_id, equipo_id, horometro) VALUES (:bitacora_id, 1, 5405);
SELECT pg_temp.inspeccion(:bitacora_id, false) \g /dev/null
SELECT id AS ot, tipo, descripcion FROM procesar_bitacora(:bitacora_id);
INSERT INTO bitacora (lancha_id, fecha) VALUES (2, current_date - 2) RETURNING id AS bitacora_id \gset
INSERT INTO bitacora_lecturas (bitacora_id, equipo_id, horometro) VALUES (:bitacora_id, 2, 1215), (:bitacora_id, 3, 1340);
TRUNCATE fallas_demo;
INSERT INTO fallas_demo VALUES ('Nivel de refrigerante del generador', 'Bajo nivel');
SELECT pg_temp.inspeccion(:bitacora_id, true) \g /dev/null
\echo '>> La falla del generador abre OT para el generador; su aceite entra en ventana:'
SELECT o.id AS ot, o.tipo, nombre_unidad(e) AS para, o.descripcion
  FROM procesar_bitacora(:bitacora_id) o LEFT JOIN equipos e ON e.id = o.equipo_id;
:semaforo
\echo '>> Historial del generador (dónde estuvo):'
SELECT b.fecha, la.nombre AS lancha, l.horometro
  FROM bitacora_lecturas l JOIN bitacora b ON b.id = l.bitacora_id LEFT JOIN lanchas la ON la.id = b.lancha_id
 WHERE l.equipo_id = 3 ORDER BY b.fecha;

\echo
\echo '================  DÍA 30 · Aceite del generador ejecutado a 1345 h y cerrado  ================'
SELECT id AS ot_aceite FROM ordenes WHERE equipo_id = 3 AND tipo = 'PREVENTIVO' \gset
UPDATE ordenes SET estado = 'EN_PROCESO' WHERE id = :ot_aceite;
UPDATE ordenes SET estado = 'EJECUTADA', horas_reparacion = 1, fuera_servicio = false, fecha_ejecucion = current_date, horometro_ejecucion = 1345,
                   trabajo_realizado = 'Cambio de aceite y filtro', servicio_contratado = false WHERE id = :ot_aceite;
UPDATE ordenes SET estado = 'CERRADA' WHERE id = :ot_aceite;
:semaforo

\echo
\echo '================  EXTRA · Tarea por calendario + job periódico  ================'
INSERT INTO tareas (equipo_id, actividad_id, intervalo_dias, fecha_ultimo_servicio)
SELECT 1, id, 180, current_date - 170 FROM actividades WHERE nombre = 'Cambio de ánodos de zinc';
SELECT id AS ot, tipo, descripcion FROM generar_ordenes_preventivas();
SELECT count(*) AS ot_nuevas_al_repetir FROM generar_ordenes_preventivas();

\echo
\echo '================  Órdenes: consecutivas, de lancha o de generador  ================'
SELECT o.id AS ot, o.tipo, o.estado, coalesce(la.nombre, '—') AS lancha, coalesce(e.codigo, '—') AS generador, o.descripcion
  FROM ordenes o LEFT JOIN lanchas la ON la.id = o.lancha_id
  LEFT JOIN equipos e ON e.id = o.equipo_id AND e.tipo = 'GENERADOR'
 ORDER BY o.id;

\echo
\echo '================  Corrección de una bitácora  ================'
\echo '>> Quitar una falla que ya generó su ODS se rechaza:'
DO $$ BEGIN
    UPDATE bitacora_respuestas SET resultado = 'OK', comentario = NULL WHERE id = (SELECT falla_id FROM ordenes WHERE id = 1);
EXCEPTION WHEN others THEN RAISE NOTICE 'RECHAZADO → %', SQLERRM;
END $$;
\echo '>> Un ítem que se dejó en OK por error se corrige a FALLA y la corrección abre su ODS:'
UPDATE bitacora_respuestas SET resultado = 'FALLA', comentario = 'Bomba no arranca'
 WHERE bitacora_id = :bitacora_id AND item_id = (SELECT id FROM items_inspeccion WHERE nombre = 'Bomba de achique');
INSERT INTO bitacora_cambios (bitacora_id, motivo, detalle)
VALUES (:bitacora_id, 'Se digitó OK por error', 'Bomba de achique: OK → Falla');
SELECT id AS ods, tipo, descripcion FROM procesar_bitacora(:bitacora_id);

\echo
\echo '================  ODS: servicio contratado, proveedor y fotos  ================'
INSERT INTO proveedores (nombre, rif, especialidad) VALUES ('Taller Marino del Caribe', 'J-12345678-9', 'Motores fuera de borda');
SELECT id AS ods_correctiva FROM ordenes WHERE estado = 'ABIERTA' AND tipo = 'CORRECTIVO' ORDER BY id LIMIT 1 \gset
UPDATE ordenes SET estado = 'EN_PROCESO' WHERE id = :ods_correctiva;
UPDATE ordenes SET diagnostico = 'Bombillo quemado y conector sulfatado',
                   servicio_requerido = 'Cambio de bombillo y conector' WHERE id = :ods_correctiva;
\echo '>> Reportar sin indicar si fue contratado se rechaza:'
DO $$ BEGIN
    UPDATE ordenes SET estado = 'EJECUTADA', horas_reparacion = 1, fuera_servicio = false, fecha_ejecucion = current_date, trabajo_realizado = 'Listo', pertenece_programa = false
     WHERE estado = 'EN_PROCESO' AND diagnostico IS NOT NULL;
EXCEPTION WHEN others THEN RAISE NOTICE 'RECHAZADO → %', SQLERRM;
END $$;
\echo '>> Contratado sin proveedor se rechaza:'
DO $$ BEGIN
    UPDATE ordenes SET estado = 'EJECUTADA', horas_reparacion = 1, fuera_servicio = false, fecha_ejecucion = current_date, trabajo_realizado = 'Listo',
                       servicio_contratado = true, pertenece_programa = false
     WHERE estado = 'EN_PROCESO' AND diagnostico IS NOT NULL;
EXCEPTION WHEN others THEN RAISE NOTICE 'RECHAZADO → %', SQLERRM;
END $$;
UPDATE ordenes SET estado = 'EJECUTADA', horas_reparacion = 1, fuera_servicio = false, fecha_ejecucion = current_date, trabajo_realizado = 'Se cambió bombillo y conector',
                   servicio_contratado = true, proveedor_id = 1, observaciones = 'Garantía de 3 meses', pertenece_programa = false
 WHERE id = :ods_correctiva;
INSERT INTO ordenes_fotos (orden_id, archivo, nombre_original) VALUES (:ods_correctiva, 'prueba/foto1.jpg', 'antes.jpg');
UPDATE ordenes SET estado = 'CERRADA' WHERE id = :ods_correctiva;
\echo '>> Agregar fotos a una ODS cerrada se rechaza:'
DO $$ BEGIN
    INSERT INTO ordenes_fotos (orden_id, archivo) SELECT id, 'prueba/foto2.jpg' FROM ordenes WHERE estado = 'CERRADA' AND proveedor_id IS NOT NULL;
EXCEPTION WHEN others THEN RAISE NOTICE 'RECHAZADO → %', SQLERRM;
END $$;
SELECT o.id AS ods, o.estado, p.nombre AS proveedor, o.diagnostico, (SELECT count(*) FROM ordenes_fotos f WHERE f.orden_id = o.id) AS fotos
  FROM ordenes o JOIN proveedores p ON p.id = o.proveedor_id;

\echo
\echo '================  ODS correctiva que cumple una tarea del programa  ================'
\echo '>> Se rompió la correa de la Pacific: una ODS correctiva hace el cambio de correa.'
\echo '>> Antes: la tarea de correa y su ODS preventiva #2 esperando:'
SELECT unidad, actividad, ultimo, proximo, ot_pendiente AS ods_pendiente
  FROM (SELECT unidad, actividad, horometro_ultimo_servicio AS ultimo, proximo_horometro AS proximo, ot_pendiente
          FROM v_estado_tareas WHERE lancha = 'Pacific' AND actividad = 'Cambio de correa') x;
SELECT t.id AS tarea_correa FROM tareas t JOIN actividades a ON a.id = t.actividad_id
 WHERE a.nombre = 'Cambio de correa' AND t.equipo_id = 1 \gset
INSERT INTO ordenes (lancha_id, tipo, descripcion) VALUES (1, 'CORRECTIVO', 'Se rompió la correa') RETURNING id AS ods_correa \gset
UPDATE ordenes SET estado = 'EN_PROCESO' WHERE id = :ods_correa;
\echo '>> Reportar "del programa" sin marcar tareas se rechaza:'
DO $$ BEGIN
    UPDATE ordenes SET estado = 'EJECUTADA', horas_reparacion = 1, fuera_servicio = false, fecha_ejecucion = current_date, trabajo_realizado = 'Cambio de correa',
                       servicio_contratado = false, pertenece_programa = true, equipo_id = 1, horometro_ejecucion = 5406
     WHERE id = (SELECT max(id) FROM ordenes WHERE descripcion = 'Se rompió la correa');
EXCEPTION WHEN others THEN RAISE NOTICE 'RECHAZADO → %', SQLERRM;
END $$;
INSERT INTO ordenes_tareas (orden_id, tarea_id) VALUES (:ods_correa, :tarea_correa);
\echo '>> Horómetro menor al último servicio de la tarea se rechaza:'
DO $$ BEGIN
    UPDATE ordenes SET estado = 'EJECUTADA', horas_reparacion = 1, fuera_servicio = false, fecha_ejecucion = current_date, trabajo_realizado = 'Cambio de correa',
                       servicio_contratado = false, pertenece_programa = true, equipo_id = 1, horometro_ejecucion = 4000
     WHERE id = (SELECT max(id) FROM ordenes WHERE descripcion = 'Se rompió la correa');
EXCEPTION WHEN others THEN RAISE NOTICE 'RECHAZADO → %', SQLERRM;
END $$;
UPDATE ordenes SET estado = 'EJECUTADA', horas_reparacion = 1, fuera_servicio = false, fecha_ejecucion = current_date, trabajo_realizado = 'Cambio de correa rota',
                   servicio_contratado = false, pertenece_programa = true, equipo_id = 1, horometro_ejecucion = 5406
 WHERE id = :ods_correa;
SELECT set_config('app.comentario', 'Aprobada por el supervisor', false) \g /dev/null
UPDATE ordenes SET estado = 'CERRADA' WHERE id = :ods_correa;
\echo '>> Después de aprobar: la correa se reprogramó desde 5406 h y la preventiva #2 se canceló sola:'
SELECT actividad, horometro_ultimo_servicio AS ultimo, proximo_horometro AS proximo, estado, ot_pendiente AS ods_pendiente
  FROM v_estado_tareas WHERE tarea_id = :tarea_correa;
SELECT h.orden_id AS ods, h.estado_nuevo, h.comentario FROM ordenes_historial h
 WHERE h.orden_id IN (:ods_correa, 2) AND h.estado_nuevo IN ('CERRADA', 'CANCELADA') ORDER BY h.orden_id;
