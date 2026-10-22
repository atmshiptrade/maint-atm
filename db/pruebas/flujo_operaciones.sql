-- =====================================================================
--  Prueba de las reglas del módulo Operaciones (Reporte de Viaje).
--  NO se ejecuta sobre la base real: db/pruebas/correr.sh la corre en una
--  base temporal. Cada comprobación imprime «OK»; la primera que falle
--  detiene la prueba con «FALLA».
-- =====================================================================
\set QUIET on
\set ON_ERROR_STOP on
\o /dev/null

CREATE TEMP TABLE ctx (clave text PRIMARY KEY, id bigint);
CREATE FUNCTION pg_temp.id(p_clave text) RETURNS bigint LANGUAGE sql AS $$ SELECT id FROM ctx WHERE clave = p_clave $$;
-- Horas del día de la prueba: pg_temp.h('2 hours') = hoy 06:00 + 2 h
CREATE FUNCTION pg_temp.h(p_desde text) RETURNS timestamptz LANGUAGE sql AS $$
    SELECT date_trunc('day', now()) + interval '6 hours' + p_desde::interval $$;

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

\echo '== Catálogos'
SELECT pg_temp.ok(imo_valido('9074729') AND NOT imo_valido('9074728') AND NOT imo_valido('90747'), 'dígito verificador del IMO');
SELECT pg_temp.rechaza($$INSERT INTO buques (nombre, imo) VALUES ('Mal escrito', '9074728')$$, 'buques_imo_valido', 'buque con IMO inválido');
INSERT INTO buques (nombre, imo, bandera, tipo, arqueo_bruto, eslora, manga)
VALUES ('MV Caribe Star', '9074729', 'Panamá', 'Granelero', 25000, 180, 30);
INSERT INTO ctx SELECT 'buque', id FROM buques WHERE imo = '9074729';
SELECT pg_temp.ok((SELECT count(*) FROM ubicaciones WHERE tipo = 'AREA') = 6 AND (SELECT count(*) FROM tipos_servicio) = 3,
                  'áreas del formato y tipos de servicio iniciales');
INSERT INTO ubicaciones (nombre, tipo) VALUES ('Muelle de prueba', 'MUELLE');
INSERT INTO ctx SELECT 'muelle', id FROM ubicaciones WHERE nombre = 'Muelle de prueba';
INSERT INTO ctx SELECT 'pozuelos', id FROM ubicaciones WHERE nombre = 'Bahía de Pozuelos';
INSERT INTO clientes (nombre) VALUES ('Agencia Naviera de Prueba');
INSERT INTO ctx SELECT 'cliente', id FROM clientes;

INSERT INTO usuarios (nombre, usuario, password_hash) VALUES
    ('Patrón Uno', 'p1', 'x'), ('Patrón Dos', 'p2', 'x'), ('Mecánico', 'm1', 'x');
INSERT INTO usuarios_roles (usuario_id, modulo, rol)
SELECT id, CASE WHEN usuario = 'm1' THEN 'MANTENIMIENTO' ELSE 'OPERACIONES' END,
       CASE WHEN usuario = 'm1' THEN 'MECANICO' ELSE 'PATRON' END
  FROM usuarios WHERE usuario IN ('p1', 'p2', 'm1');
INSERT INTO ctx SELECT usuario, id FROM usuarios WHERE usuario IN ('p1', 'p2', 'm1');
INSERT INTO lanchas (nombre, matricula, estado) VALUES ('Ops Uno', 'OPS-1', 'OPERATIVA'), ('Ops Dos', 'OPS-2', 'OPERATIVA'),
                                                       ('Ops Parada', 'OPS-3', 'INACTIVA');
INSERT INTO ctx SELECT CASE matricula WHEN 'OPS-1' THEN 'l1' WHEN 'OPS-2' THEN 'l2' ELSE 'l3' END, id
  FROM lanchas WHERE matricula LIKE 'OPS-%';

\echo '== Asignación'
SELECT pg_temp.rechaza($$INSERT INTO viajes (fecha, lancha_id, patron_id) VALUES (current_date, pg_temp.id('l3'), pg_temp.id('p1'))$$,
                       'está inactiva', 'una lancha inactiva no se asigna');
SELECT pg_temp.rechaza($$INSERT INTO viajes (fecha, lancha_id, patron_id) VALUES (current_date, pg_temp.id('l1'), pg_temp.id('m1'))$$,
                       'usuario activo de Operaciones', 'un mecánico no es patrón');
INSERT INTO viajes (fecha, lancha_id, patron_id) VALUES (current_date, pg_temp.id('l1'), pg_temp.id('p1'));
INSERT INTO viajes (fecha, lancha_id, patron_id) VALUES (current_date, pg_temp.id('l1'), pg_temp.id('p2'));
INSERT INTO ctx SELECT CASE numero WHEN 1 THEN 'v1' ELSE 'v2' END, id FROM viajes;
SELECT pg_temp.ok((SELECT string_agg(codigo, ',' ORDER BY numero) FROM viajes) = 'PLC-0001,PLC-0002',
                  'códigos correlativos PLC-0001, PLC-0002 (dos borradores para la misma lancha)');

\echo '== Trayecto del PLC-0001'
SELECT pg_temp.rechaza($$INSERT INTO viajes_tramos (viaje_id, salida) VALUES (pg_temp.id('v1'), pg_temp.h('0'))$$,
                       'Indica de dónde zarpó', 'zarpar sin indicar el origen');
UPDATE viajes SET origen_id = pg_temp.id('muelle') WHERE id = pg_temp.id('v1');
INSERT INTO viajes_tramos (viaje_id, salida, destino_id) VALUES (pg_temp.id('v1'), pg_temp.h('0'), pg_temp.id('pozuelos'));
SELECT pg_temp.ok((SELECT estado FROM viajes WHERE id = pg_temp.id('v1')) = 'EN_CURSO', 'el primer zarpe pone el viaje en curso');
UPDATE viajes SET origen_id = pg_temp.id('muelle') WHERE id = pg_temp.id('v2');
SELECT pg_temp.rechaza($$INSERT INTO viajes_tramos (viaje_id, salida) VALUES (pg_temp.id('v2'), pg_temp.h('10 min'))$$,
                       'ux_viajes_lancha_en_curso', 'la misma lancha no tiene dos viajes en curso');
SELECT pg_temp.rechaza($$INSERT INTO viajes_tramos (viaje_id, salida) VALUES (pg_temp.id('v1'), pg_temp.h('10 min'))$$,
                       'ux_tramos_navegando', 'no se zarpa dos veces sin llegar');
SELECT pg_temp.rechaza($$INSERT INTO viajes_esperas (viaje_id, inicio) VALUES (pg_temp.id('v1'), pg_temp.h('20 min'))$$,
                       'tramo navegando', 'no hay espera mientras navega');
SELECT pg_temp.rechaza($$UPDATE viajes_tramos SET llegada = pg_temp.h('-5 min') WHERE viaje_id = pg_temp.id('v1')$$,
                       'tramos_llegada_despues_de_salida', 'la llegada no es anterior a la salida');
SELECT pg_temp.rechaza($$UPDATE viajes_tramos SET destino_id = NULL, destino_buque = true, llegada = pg_temp.h('30 min')
                          WHERE viaje_id = pg_temp.id('v1')$$,
                       'indica primero el buque', '«a bordo del buque» exige el buque del servicio');
UPDATE viajes SET buque_id = pg_temp.id('buque') WHERE id = pg_temp.id('v1');
UPDATE viajes_tramos SET destino_id = NULL, destino_buque = true, llegada = pg_temp.h('30 min') WHERE viaje_id = pg_temp.id('v1');
INSERT INTO viajes_esperas (viaje_id, inicio, fin, motivo)
VALUES (pg_temp.id('v1'), pg_temp.h('40 min'), pg_temp.h('1 hour 10 min'), 'Esperando al práctico');
SELECT pg_temp.rechaza($$INSERT INTO viajes_esperas (viaje_id, inicio, fin) VALUES (pg_temp.id('v1'), pg_temp.h('1 hour'), pg_temp.h('1 hour 20 min'))$$,
                       'se superponen', 'dos esperas no se superponen');
SELECT pg_temp.rechaza($$INSERT INTO viajes_tramos (viaje_id, salida) VALUES (pg_temp.id('v1'), pg_temp.h('20 min'))$$,
                       'anterior a la llegada', 'no se zarpa antes de haber llegado');
INSERT INTO viajes_tramos (viaje_id, salida, destino_id, llegada)
VALUES (pg_temp.id('v1'), pg_temp.h('1 hour 15 min'), pg_temp.id('muelle'), pg_temp.h('1 hour 45 min'));
SELECT pg_temp.ok((SELECT espera_total = interval '30 minutes' AND tramos = 2 FROM v_viajes WHERE id = pg_temp.id('v1')),
                  'dos tramos y 30 min de espera calculados');

\echo '== Finalizar y cerrar'
SELECT pg_temp.rechaza($$UPDATE viajes SET estado = 'CERRADO' WHERE id = pg_temp.id('v1')$$,
                       'no puede pasar de en_curso a cerrado', 'no se cierra sin finalizar');
UPDATE viajes SET estado = 'FINALIZADO' WHERE id = pg_temp.id('v1');
SELECT pg_temp.rechaza($$UPDATE viajes SET estado = 'CERRADO' WHERE id = pg_temp.id('v1')$$,
                       'Para cerrar el reporte falta: a quién se prestó el servicio (cliente), el tipo de servicio, el área donde se ejecutó, la descripción del servicio',
                       'cerrar avisa todo lo que falta');
UPDATE viajes SET cliente_id = pg_temp.id('cliente'), descripcion = 'Traslado del práctico al MV Caribe Star'
 WHERE id = pg_temp.id('v1');
INSERT INTO viajes_servicios (viaje_id, tipo_servicio_id)
SELECT pg_temp.id('v1'), id FROM tipos_servicio WHERE nombre = 'Traslado de pasajeros';
INSERT INTO viajes_areas (viaje_id, ubicacion_id) VALUES (pg_temp.id('v1'), pg_temp.id('pozuelos'));
UPDATE viajes SET estado = 'CERRADO' WHERE id = pg_temp.id('v1');
SELECT pg_temp.ok((SELECT estado = 'CERRADO' AND cerrado_en IS NOT NULL FROM viajes WHERE id = pg_temp.id('v1')), 'reporte cerrado');

\echo '== Reporte cerrado: solo enmiendas'
SELECT pg_temp.rechaza($$UPDATE viajes SET descripcion = 'Otra cosa' WHERE id = pg_temp.id('v1')$$,
                       'usa «Enmendar»', 'un reporte cerrado no se edita');
SELECT pg_temp.rechaza($$UPDATE viajes_tramos SET llegada = llegada + interval '5 min' WHERE viaje_id = pg_temp.id('v1')$$,
                       'usa «Enmendar»', 'su trayecto tampoco');
SELECT pg_temp.rechaza($$UPDATE viajes SET estado = 'FINALIZADO' WHERE id = pg_temp.id('v1')$$,
                       'no cambia de estado', 'un reporte cerrado no se reabre');
BEGIN;
SELECT set_config('app.enmienda', 'La llegada al muelle se anotó tarde', true);
UPDATE viajes_tramos SET llegada = pg_temp.h('1 hour 40 min') WHERE viaje_id = pg_temp.id('v1') AND destino_id = pg_temp.id('muelle');
COMMIT;
SELECT pg_temp.ok((SELECT count(*) = 1 AND bool_and(tabla = 'viajes_tramos' AND accion = 'UPDATE'
                          AND motivo = 'La llegada al muelle se anotó tarde'
                          AND (SELECT array_agg(k) FROM jsonb_object_keys(despues) k) = '{llegada}')
                     FROM viajes_enmiendas WHERE viaje_id = pg_temp.id('v1')),
                  'la enmienda queda registrada solo con lo que cambió');

\echo '== Patrones, cancelación y fallas de Mantenimiento'
INSERT INTO viajes_tramos (viaje_id, salida) VALUES (pg_temp.id('v2'), pg_temp.h('2 hours'));
SELECT pg_temp.ok((SELECT estado FROM viajes WHERE id = pg_temp.id('v2')) = 'EN_CURSO', 'la lancha ya libre zarpa en el PLC-0002');
INSERT INTO viajes (fecha, lancha_id, patron_id, origen_id) VALUES (current_date, pg_temp.id('l2'), pg_temp.id('p2'), pg_temp.id('muelle'));
INSERT INTO ctx SELECT 'v3', id FROM viajes WHERE numero = 3;
SELECT pg_temp.rechaza($$INSERT INTO viajes_tramos (viaje_id, salida) VALUES (pg_temp.id('v3'), pg_temp.h('2 hours'))$$,
                       'ux_viajes_patron_en_curso', 'el mismo patrón no tiene dos viajes en curso');
SELECT pg_temp.rechaza($$UPDATE viajes SET estado = 'CANCELADO' WHERE id = pg_temp.id('v3')$$,
                       'viajes_cancelado_con_motivo', 'cancelar exige el motivo');
UPDATE viajes SET estado = 'CANCELADO', motivo_cancelacion = 'El cliente suspendió el servicio' WHERE id = pg_temp.id('v3');
SELECT pg_temp.rechaza($$UPDATE viajes SET descripcion = 'x' WHERE id = pg_temp.id('v3')$$,
                       'está cancelado', 'un reporte cancelado no se modifica');
SELECT pg_temp.ok(NOT lancha_falla_critica(pg_temp.id('l2')), 'sin fallas críticas pendientes');
INSERT INTO ordenes (lancha_id, tipo, prioridad, descripcion) VALUES (pg_temp.id('l2'), 'CORRECTIVO', 'CRITICA', 'Fuga en el casco');
SELECT pg_temp.ok(lancha_falla_critica(pg_temp.id('l2')), 'Operaciones ve la falla crítica pendiente de la lancha');
\echo 'Operaciones: todas las comprobaciones OK'
