-- =====================================================================
--  Prueba de materiales, tiempos de las ODS y análisis de fallas (migración 012).
--  NO se ejecuta sobre la base real: db/pruebas/correr.sh la corre en una
--  base temporal, después de los otros flujos. Cada comprobación imprime
--  «OK»; la primera que falle detiene la prueba con «FALLA».
-- =====================================================================
\set QUIET on
\set ON_ERROR_STOP on
\o /dev/null

CREATE TEMP TABLE ctx (clave text PRIMARY KEY, id bigint);
CREATE FUNCTION pg_temp.id(p_clave text) RETURNS bigint LANGUAGE sql AS $$ SELECT id FROM ctx WHERE clave = p_clave $$;
-- Horas de referencia: pg_temp.h('2 hours') = anteayer 06:00 + 2 h
CREATE FUNCTION pg_temp.h(p_desde text) RETURNS timestamptz LANGUAGE sql AS $$
    SELECT date_trunc('day', now()) - interval '2 days' + interval '6 hours' + p_desde::interval $$;

CREATE FUNCTION pg_temp.ok(p_condicion boolean, p_nombre text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    IF p_condicion IS NOT TRUE THEN
        RAISE EXCEPTION 'FALLA: %', p_nombre;
    END IF;
    RAISE NOTICE 'OK     %', p_nombre;
END $$;

CREATE FUNCTION pg_temp.rechaza(p_sql text, p_fragmento text, p_nombre text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    BEGIN
        EXECUTE p_sql;
    EXCEPTION WHEN others THEN
        IF position(p_fragmento IN SQLERRM) > 0 THEN
            RAISE NOTICE 'OK     % → %', p_nombre, SQLERRM;
            RETURN;
        END IF;
        RAISE EXCEPTION 'FALLA: % (rechazado por otra razón: %)', p_nombre, SQLERRM;
    END;
    RAISE EXCEPTION 'FALLA: % (no se rechazó)', p_nombre;
END $$;

-- Reporta una ODS con lo mínimo más sus tiempos
CREATE FUNCTION pg_temp.reportar(p_orden bigint, p_reparacion numeric, p_fuera boolean,
                                 p_desde timestamptz DEFAULT NULL, p_hasta timestamptz DEFAULT NULL,
                                 p_horometro numeric DEFAULT NULL) RETURNS void LANGUAGE sql AS $$
    UPDATE ordenes SET estado = 'EJECUTADA', fecha_ejecucion = current_date, trabajo_realizado = 'Listo',
                       servicio_contratado = false, pertenece_programa = false, horas_reparacion = p_reparacion,
                       fuera_servicio = p_fuera, fuera_servicio_desde = p_desde, fuera_servicio_hasta = p_hasta,
                       horometro_ejecucion = p_horometro
     WHERE id = p_orden;
$$;

\echo '== Herramientas y repuestos'
INSERT INTO materiales (nombre, tipo, unidad, codigo) VALUES
    ('Filtro de aceite T', 'REPUESTO', 'unidad', 'FT-001'),
    ('Aceite 15W-40 T', 'INSUMO', 'litro', NULL),
    ('Llave de filtro T', 'HERRAMIENTA', NULL, NULL);
INSERT INTO ctx SELECT 'filtro', id FROM materiales WHERE nombre = 'Filtro de aceite T';
INSERT INTO ctx SELECT 'aceite', id FROM materiales WHERE nombre = 'Aceite 15W-40 T';
INSERT INTO ctx SELECT 'llave', id FROM materiales WHERE nombre = 'Llave de filtro T';
SELECT pg_temp.rechaza($$INSERT INTO materiales (nombre, tipo) VALUES ('FILTRO DE ACEITE t', 'REPUESTO')$$,
                       'ux_materiales_nombre', 'el nombre no se repite (sin importar mayúsculas)');
SELECT pg_temp.rechaza($$INSERT INTO materiales (nombre, tipo) VALUES ('Otro', 'CHATARRA')$$,
                       'materiales_tipo_check', 'solo repuesto, herramienta o insumo');
SELECT pg_temp.rechaza($$INSERT INTO materiales (nombre, tipo, codigo) VALUES ('Otro filtro', 'REPUESTO', 'FT-001')$$,
                       'materiales_codigo_key', 'el número de parte no se repite');
SELECT pg_temp.ok((SELECT count(*) FROM motivos_inoperatividad) = 7, 'motivos de inoperatividad iniciales');

INSERT INTO actividades (nombre, categoria_id, severidad)
SELECT 'Cambio de aceite T', id, 'ALTA' FROM categorias WHERE nombre = 'Lubricación';
INSERT INTO ctx SELECT 'actividad', id FROM actividades WHERE nombre = 'Cambio de aceite T';
INSERT INTO actividades_materiales (actividad_id, material_id, cantidad) VALUES
    (pg_temp.id('actividad'), pg_temp.id('filtro'), 1), (pg_temp.id('actividad'), pg_temp.id('aceite'), 4.5),
    (pg_temp.id('actividad'), pg_temp.id('llave'), NULL);
SELECT pg_temp.ok((SELECT count(*) FROM actividades_materiales WHERE actividad_id = pg_temp.id('actividad')) = 3,
                  'la actividad prevé 3 materiales (la herramienta sin cantidad)');
SELECT pg_temp.rechaza(format($$INSERT INTO actividades_materiales VALUES (%s, %s, 0)$$, pg_temp.id('actividad'), pg_temp.id('llave')),
                       'actividades_materiales', 'cantidad cero o repetido se rechaza');
SELECT pg_temp.rechaza(format($$DELETE FROM materiales WHERE id = %s$$, pg_temp.id('filtro')),
                       'actividades_materiales', 'un material en uso no se elimina');

\echo '== Maquinaria, ítems y ODS'
INSERT INTO lanchas (nombre, matricula) VALUES ('Tiempos Uno', 'TT-1'), ('Tiempos Dos', 'TT-2');
INSERT INTO ctx SELECT CASE nombre WHEN 'Tiempos Uno' THEN 'l1' ELSE 'l2' END, id FROM lanchas WHERE nombre LIKE 'Tiempos%';
INSERT INTO equipos (lancha_id, tipo, nombre, fecha_alta, horometro_inicial)
SELECT id, 'PROPULSION', 'Motores', current_date - 10, 100 FROM lanchas WHERE nombre LIKE 'Tiempos%';
INSERT INTO equipos (tipo, nombre, fecha_alta, horometro_inicial) VALUES ('GENERADOR', 'Generador T', current_date - 10, 50);
INSERT INTO ctx SELECT 'gen', id FROM equipos WHERE nombre = 'Generador T';
INSERT INTO items_inspeccion (nombre, categoria_id, prioridad, aplica_a)
SELECT v.nombre, c.id, 'ALTA', v.aplica FROM (VALUES ('Radio T', 'LANCHA'), ('Escape del generador T', 'GENERADOR')) v(nombre, aplica)
  JOIN categorias c ON c.nombre = 'Mecánica';
INSERT INTO ctx SELECT 'radio', id FROM items_inspeccion WHERE nombre = 'Radio T';
INSERT INTO ctx SELECT 'escape', id FROM items_inspeccion WHERE nombre = 'Escape del generador T';

SELECT pg_temp.rechaza(format($$INSERT INTO ordenes (lancha_id, tipo, descripcion, item_id) VALUES (%s, 'CORRECTIVO', 'x', %s)$$,
                              pg_temp.id('l1'), pg_temp.id('escape')),
                       'es un ítem del generador', 'una ODS de lancha no se clasifica con un ítem del generador');
INSERT INTO ordenes (lancha_id, tipo, descripcion, item_id) VALUES (pg_temp.id('l1'), 'CORRECTIVO', 'No transmite', pg_temp.id('radio'));
INSERT INTO ctx SELECT 'ods_radio', max(id) FROM ordenes;
INSERT INTO ordenes (equipo_id, tipo, descripcion, item_id) VALUES (pg_temp.id('gen'), 'CORRECTIVO', 'Humo negro', pg_temp.id('escape'));
INSERT INTO ctx SELECT 'ods_escape', max(id) FROM ordenes;
SELECT pg_temp.ok((SELECT item_id FROM ordenes WHERE id = pg_temp.id('ods_escape')) = pg_temp.id('escape'),
                  'una ODS de generador se clasifica con un ítem del generador');

-- Bitácora de la lancha 2 con el generador a bordo: falla del radio y del escape del generador
INSERT INTO bitacora (lancha_id, fecha) VALUES (pg_temp.id('l2'), current_date);
INSERT INTO ctx SELECT 'bitacora', max(id) FROM bitacora;
INSERT INTO bitacora_lecturas (bitacora_id, equipo_id, horometro) VALUES (pg_temp.id('bitacora'), pg_temp.id('gen'), 60);
INSERT INTO bitacora_respuestas (bitacora_id, item_id, resultado, comentario) VALUES
    (pg_temp.id('bitacora'), pg_temp.id('radio'), 'FALLA', 'Sin señal'),
    (pg_temp.id('bitacora'), pg_temp.id('escape'), 'FALLA', 'Humo blanco');
SELECT count(*) FROM procesar_bitacora(pg_temp.id('bitacora'));
INSERT INTO ctx SELECT 'ods_bit_radio', o.id FROM ordenes o JOIN bitacora_respuestas r ON r.id = o.falla_id
 WHERE r.bitacora_id = pg_temp.id('bitacora') AND r.item_id = pg_temp.id('radio');
SELECT pg_temp.ok((SELECT item_id FROM ordenes WHERE id = pg_temp.id('ods_bit_radio')) = pg_temp.id('radio'),
                  'la ODS de la bitácora hereda qué falló');
UPDATE ordenes SET item_id = pg_temp.id('escape') WHERE id = pg_temp.id('ods_bit_radio');
SELECT pg_temp.ok((SELECT item_id FROM ordenes WHERE id = pg_temp.id('ods_bit_radio')) = pg_temp.id('radio'),
                  'y no se puede cambiar: es el ítem que se reportó');
INSERT INTO ctx SELECT 'ods_bit_escape', o.id FROM ordenes o JOIN bitacora_respuestas r ON r.id = o.falla_id
 WHERE r.bitacora_id = pg_temp.id('bitacora') AND r.item_id = pg_temp.id('escape');
SELECT pg_temp.ok((SELECT unidad_clave FROM v_ordenes_analisis WHERE id = pg_temp.id('ods_bit_escape')) = 'G' || pg_temp.id('gen'),
                  'la falla del generador reportada en la bitácora de una lancha cuenta para el generador');

\echo '== Reportar con tiempos'
UPDATE ordenes SET estado = 'EN_PROCESO' WHERE id IN (pg_temp.id('ods_radio'), pg_temp.id('ods_escape'), pg_temp.id('ods_bit_radio'));
INSERT INTO ordenes_materiales (orden_id, material_id, cantidad) VALUES (pg_temp.id('ods_radio'), pg_temp.id('llave'), NULL);
SELECT pg_temp.rechaza(format('SELECT pg_temp.reportar(%s, NULL, false)', pg_temp.id('ods_radio')),
                       'duración del trabajo', 'reportar sin la duración del trabajo');
SELECT pg_temp.rechaza(format('SELECT pg_temp.reportar(%s, 2, NULL)', pg_temp.id('ods_radio')),
                       'quedó fuera de servicio', 'reportar sin decir si quedó fuera de servicio');
SELECT pg_temp.rechaza(format('SELECT pg_temp.reportar(%s, 2, true, %L)', pg_temp.id('ods_radio'), pg_temp.h('0 hours')),
                       'cuándo volvió a operar', 'fuera de servicio sin la hora en que volvió a operar');
SELECT pg_temp.rechaza(format($$UPDATE ordenes SET fuera_servicio = true, fuera_servicio_desde = %L, fuera_servicio_hasta = %L WHERE id = %s$$,
                              pg_temp.h('5 hours'), pg_temp.h('1 hours'), pg_temp.id('ods_radio')),
                       'ordenes_fuera_servicio_orden', 'volvió a operar antes de quedar fuera de servicio');
SELECT pg_temp.rechaza(format($$UPDATE ordenes SET fuera_servicio = false, fuera_servicio_desde = %L WHERE id = %s$$,
                              pg_temp.h('0 hours'), pg_temp.id('ods_radio')),
                       'ordenes_fuera_servicio_fechas', 'si no quedó fuera de servicio no lleva horas');
SELECT pg_temp.rechaza(format($$UPDATE ordenes SET fuera_servicio = true, fuera_servicio_desde = %L WHERE id = %s$$,
                              now() + interval '1 day', pg_temp.id('ods_radio')),
                       'no puede ser futura', 'la hora de la avería no puede ser futura');
-- Fuera de servicio 10 h (06:00 a 16:00) con 2 h de trabajo: 8 h de factores externos
SELECT pg_temp.rechaza(format('SELECT pg_temp.reportar(%s, 12, true, %L, %L)', pg_temp.id('ods_radio'),
                              pg_temp.h('0 hours'), pg_temp.h('10 hours')),
                       'no puede ser mayor que el tiempo fuera de servicio', 'el trabajo no dura más que el tiempo fuera de servicio');
INSERT INTO ordenes_demoras (orden_id, motivo_id, horas)
SELECT pg_temp.id('ods_radio'), id, 6 FROM motivos_inoperatividad WHERE nombre = 'Repuesto no disponible';
INSERT INTO ordenes_demoras (orden_id, motivo_id, horas)
SELECT pg_temp.id('ods_radio'), id, 3 FROM motivos_inoperatividad WHERE nombre = 'Fuera del horario laboral';
SELECT pg_temp.rechaza(format('SELECT pg_temp.reportar(%s, 2, true, %L, %L)', pg_temp.id('ods_radio'),
                              pg_temp.h('0 hours'), pg_temp.h('10 hours')),
                       'Los motivos suman 9.00 h', 'los motivos no suman más que los factores externos');
UPDATE ordenes_demoras SET horas = 2 WHERE orden_id = pg_temp.id('ods_radio') AND horas = 3;
SELECT pg_temp.reportar(pg_temp.id('ods_radio'), 2, true, pg_temp.h('0 hours'), pg_temp.h('10 hours'));
SELECT pg_temp.ok((SELECT estado FROM ordenes WHERE id = pg_temp.id('ods_radio')) = 'EJECUTADA',
                  'reportada: 10 h fuera de servicio = 2 h de trabajo + 6 h repuesto + 2 h horario (+ 0 sin motivo)');
SELECT pg_temp.rechaza(format($$INSERT INTO ordenes_materiales VALUES (%s, %s, 1)$$, pg_temp.id('ods_radio'), pg_temp.id('filtro')),
                       'ya fue reportada', 'ya reportada no cambian sus materiales');
SELECT pg_temp.rechaza(format($$DELETE FROM ordenes_demoras WHERE orden_id = %s$$, pg_temp.id('ods_radio')),
                       'ya fue reportada', 'ni sus motivos');
UPDATE ordenes SET estado = 'EN_PROCESO' WHERE id = pg_temp.id('ods_radio');   -- el supervisor la devuelve
DELETE FROM ordenes_materiales WHERE orden_id = pg_temp.id('ods_radio');
SELECT pg_temp.ok(NOT EXISTS (SELECT 1 FROM ordenes_materiales WHERE orden_id = pg_temp.id('ods_radio')),
                  'devuelta al mecánico, sus materiales se pueden corregir');
SELECT pg_temp.reportar(pg_temp.id('ods_radio'), 2, true, pg_temp.h('0 hours'), pg_temp.h('10 hours'));
UPDATE ordenes SET estado = 'CERRADA' WHERE id = pg_temp.id('ods_radio');

INSERT INTO ordenes_demoras (orden_id, motivo_id, horas)
SELECT pg_temp.id('ods_escape'), id, 1 FROM motivos_inoperatividad WHERE nombre = 'Otro';
SELECT pg_temp.rechaza(format('SELECT pg_temp.reportar(%s, 1, false, NULL, NULL, 60)', pg_temp.id('ods_escape')),
                       'no hay tiempo por factores externos', 'sin fuera de servicio no hay motivos que repartir');
DELETE FROM ordenes_demoras WHERE orden_id = pg_temp.id('ods_escape');
SELECT pg_temp.reportar(pg_temp.id('ods_escape'), 1.5, false, NULL, NULL, 60);
SELECT pg_temp.ok((SELECT estado FROM ordenes WHERE id = pg_temp.id('ods_escape')) = 'EJECUTADA',
                  'reportada sin quedar fuera de servicio');

\echo '== Análisis'
-- Lancha 1: la ODS del radio (06:00 a 16:00) y otra que se solapa (11:00 a 18:00) → 12 h, no 17 h
INSERT INTO ordenes (lancha_id, tipo, descripcion, item_id) VALUES (pg_temp.id('l1'), 'CORRECTIVO', 'Otra vez sin señal', pg_temp.id('radio'));
INSERT INTO ctx SELECT 'ods_radio2', max(id) FROM ordenes;
UPDATE ordenes SET estado = 'EN_PROCESO' WHERE id = pg_temp.id('ods_radio2');
SELECT pg_temp.reportar(pg_temp.id('ods_radio2'), 3, true, pg_temp.h('5 hours'), pg_temp.h('12 hours'));
SELECT pg_temp.ok((SELECT horas FROM horas_fuera_servicio(pg_temp.h('-6 hours'), now()) WHERE unidad_clave = 'L' || pg_temp.id('l1')) = 12,
                  'dos fallas que se solapan cuentan una sola vez (12 h, no 17 h)');
SELECT pg_temp.ok((SELECT horas FROM horas_fuera_servicio(pg_temp.h('8 hours'), pg_temp.h('11 hours')) WHERE unidad_clave = 'L' || pg_temp.id('l1')) = 3,
                  'las horas se recortan al período pedido');
SELECT pg_temp.ok((SELECT sum(horas_fuera) FROM v_ordenes_analisis WHERE unidad_clave = 'L' || pg_temp.id('l1')) = 17,
                  'por ODS, cada una suma su propio tiempo');
SELECT pg_temp.ok((SELECT count(*) FROM v_ordenes_analisis WHERE item_id = pg_temp.id('radio') AND tipo = 'CORRECTIVO') = 3,
                  'el radio falló 3 veces (dos en la lancha 1 y una en la lancha 2)');

-- La lancha 2 sigue fuera de servicio desde hace 2 h: cuenta hasta ahora
UPDATE ordenes SET fuera_servicio = true, fuera_servicio_desde = now() - interval '2 hours' WHERE id = pg_temp.id('ods_bit_radio');
SELECT pg_temp.ok((SELECT horas FROM horas_fuera_servicio(now() - interval '1 day', now()) WHERE unidad_clave = 'L' || pg_temp.id('l2'))
                  BETWEEN 1.99 AND 2.01, 'una falla que sigue abierta cuenta hasta ahora');
UPDATE ordenes SET estado = 'CANCELADA' WHERE id = pg_temp.id('ods_bit_radio');
SELECT pg_temp.ok(NOT EXISTS (SELECT 1 FROM v_ordenes_analisis WHERE id = pg_temp.id('ods_bit_radio')),
                  'las canceladas no cuentan');

\o
\echo 'Flujo de tiempos y fallas: todo OK'
