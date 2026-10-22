-- =====================================================================
--  Datos FICTICIOS para la demo en Render (nada de esto es real).
--  Lo carga app/migrate.py una sola vez, con CARGAR_DEMO=true y la base
--  sin lanchas, dentro de una transacción y con app.usuario_id = mecánico.
--  Las fechas son relativas a hoy: 30 días de bitácoras hasta ayer, para
--  que hoy se pueda mostrar cómo se carga una bitácora nueva.
-- =====================================================================

-- Ítems del generador (la base trae solo ítems de lancha)
INSERT INTO items_inspeccion (nombre, categoria_id, prioridad, aplica_a, orden)
SELECT v.nombre, c.id, v.prioridad, 'GENERADOR', v.orden
  FROM (VALUES ('Nivel de refrigerante del generador', 'Mecánica',  'ALTA',  60),
               ('Nivel de aceite del generador',       'Lubricación', 'ALTA', 61),
               ('Voltaje de salida del generador',     'Eléctrica', 'MEDIA', 62)) v(nombre, categoria, prioridad, orden)
  JOIN categorias c ON c.nombre = v.categoria
 WHERE NOT EXISTS (SELECT 1 FROM items_inspeccion i WHERE i.nombre = v.nombre);

INSERT INTO materiales (nombre, tipo, unidad, codigo) VALUES
    ('Filtro de aceite DEMO-01',        'REPUESTO',    'unidad', 'DEMO-FA-01'),
    ('Filtro de combustible DEMO-02',   'REPUESTO',    'unidad', 'DEMO-FC-02'),
    ('Aceite 15W-40',                   'INSUMO',      'litro',  NULL),
    ('Correa de alternador',            'REPUESTO',    'unidad', 'DEMO-CA-03'),
    ('Ánodo de zinc',                   'REPUESTO',    'unidad', 'DEMO-AZ-04'),
    ('Impulsor de bomba de agua',       'REPUESTO',    'unidad', 'DEMO-IB-05'),
    ('Bombillo de navegación 12 V',     'REPUESTO',    'unidad', NULL),
    ('Llave de filtro',                 'HERRAMIENTA', NULL,     NULL),
    ('Multímetro',                      'HERRAMIENTA', NULL,     NULL);

INSERT INTO actividades (nombre, categoria_id, severidad, tolerancia_pct, tiempo_estimado_h,
                         intervalo_horas_sugerido, intervalo_dias_sugerido, materiales)
SELECT v.nombre, c.id, v.severidad, 10, v.horas, v.cada_h, v.cada_d, NULL
  FROM (VALUES
        ('Cambio de aceite y filtro',       'Lubricación',        'ALTA',  1.5, 250,  NULL),
        ('Cambio de filtros de combustible','Mecánica',           'ALTA',  1.0, 300,  NULL),
        ('Cambio de correa de alternador',  'Mecánica',           'MEDIA', 1.0, 400,  NULL),
        ('Cambio de impulsor de bomba de agua', 'Mecánica',       'ALTA',  2.0, 500,  NULL),
        ('Cambio de ánodos de zinc',        'Casco y estructura', 'MEDIA', 2.0, NULL, 180)
       ) AS v(nombre, categoria, severidad, horas, cada_h, cada_d)
  JOIN categorias c ON c.nombre = v.categoria;

INSERT INTO actividades_materiales (actividad_id, material_id, cantidad)
SELECT a.id, m.id, v.cantidad
  FROM (VALUES ('Cambio de aceite y filtro', 'Filtro de aceite DEMO-01', 1),
               ('Cambio de aceite y filtro', 'Aceite 15W-40', 12),
               ('Cambio de aceite y filtro', 'Llave de filtro', NULL),
               ('Cambio de filtros de combustible', 'Filtro de combustible DEMO-02', 2),
               ('Cambio de correa de alternador', 'Correa de alternador', 1),
               ('Cambio de impulsor de bomba de agua', 'Impulsor de bomba de agua', 1),
               ('Cambio de ánodos de zinc', 'Ánodo de zinc', 3)) v(actividad, material, cantidad)
  JOIN actividades a ON a.nombre = v.actividad
  JOIN materiales m ON m.nombre = v.material;

INSERT INTO proveedores (nombre, rif, especialidad, telefono)
VALUES ('Taller Náutico Demo', 'J-00000000-0', 'Motores y electricidad marina', '0000-0000000');

-- Lanchas, sus motores y un generador independiente (los códigos los pone la base)
INSERT INTO lanchas (nombre, matricula) VALUES
    ('Gaviota Demo',  'DEMO-001'),
    ('Pelícano Demo', 'DEMO-002'),
    ('Alcatraz Demo', 'DEMO-003');
INSERT INTO equipos (lancha_id, tipo, nombre, fecha_alta, horometro_inicial)
SELECT l.id, 'PROPULSION', 'Motores', current_date - 45, v.horometro
  FROM (VALUES ('DEMO-001', 3200), ('DEMO-002', 1850), ('DEMO-003', 640)) v(matricula, horometro)
  JOIN lanchas l ON l.matricula = v.matricula;
INSERT INTO equipos (lancha_id, tipo, nombre, fecha_alta, horometro_inicial)
VALUES (NULL, 'GENERADOR', 'Generador Demo 7 kW', current_date - 45, 900);

-- Programa: algunas tareas cerca de vencer para que se vean el semáforo y las preventivas
INSERT INTO tareas (equipo_id, actividad_id, intervalo_horas, horometro_ultimo_servicio)
SELECT e.id, a.id, a.intervalo_horas_sugerido, e.horometro_inicial - v.atraso
  FROM (VALUES ('DEMO-001', 'Cambio de aceite y filtro', 40),
               ('DEMO-001', 'Cambio de filtros de combustible', 20),
               ('DEMO-001', 'Cambio de correa de alternador', 100),
               ('DEMO-002', 'Cambio de aceite y filtro', 10),
               ('DEMO-002', 'Cambio de impulsor de bomba de agua', 150),
               ('DEMO-003', 'Cambio de aceite y filtro', 0),
               ('DEMO-003', 'Cambio de filtros de combustible', 0)) v(matricula, actividad, atraso)
  JOIN lanchas l ON l.matricula = v.matricula
  JOIN equipos e ON e.lancha_id = l.id AND e.tipo = 'PROPULSION'
  JOIN actividades a ON a.nombre = v.actividad;
INSERT INTO tareas (equipo_id, actividad_id, intervalo_horas, horometro_ultimo_servicio)
SELECT e.id, a.id, 150, e.horometro_inicial - 10
  FROM equipos e, actividades a WHERE e.tipo = 'GENERADOR' AND a.nombre = 'Cambio de aceite y filtro';
INSERT INTO tareas (equipo_id, actividad_id, intervalo_dias, fecha_ultimo_servicio)
SELECT e.id, a.id, 180, current_date - v.dias
  FROM (VALUES ('DEMO-001', 175), ('DEMO-002', 60), ('DEMO-003', 200)) v(matricula, dias)
  JOIN lanchas l ON l.matricula = v.matricula
  JOIN equipos e ON e.lancha_id = l.id AND e.tipo = 'PROPULSION'
  JOIN actividades a ON a.nombre = 'Cambio de ánodos de zinc';

-- Fallas que aparecen en las bitácoras (días atrás desde hoy)
CREATE TEMP TABLE demo_fallas (matricula text, dias int, item text, comentario text) ON COMMIT DROP;
INSERT INTO demo_fallas VALUES
    ('DEMO-001', 27, 'Luz de navegación babor',               'No enciende'),
    ('DEMO-002', 24, 'Agua de enfriamiento (chorro testigo)', 'Chorro débil, motor calienta'),
    ('DEMO-003', 21, 'Batería y bornes',                      'Bornes sulfatados, arranque lento'),
    ('DEMO-001', 18, 'Nivel de refrigerante del generador',   'Bajo nivel'),
    ('DEMO-002', 15, 'Hélices',                               'Vibración, posible golpe en una pala'),
    ('DEMO-003', 12, 'Fugas de aceite',                       'Goteo en el cárter'),
    ('DEMO-001',  9, 'Radio VHF',                             'Sin transmisión'),
    ('DEMO-002',  6, 'Bomba de achique',                      'No arranca'),
    ('DEMO-003',  4, 'Luz de navegación estribor',            'Intermitente'),
    ('DEMO-001',  2, 'Fugas de combustible',                  'Olor a gasolina en sentina');

-- 30 días de bitácora hasta ayer; el generador va a bordo de la Gaviota
DO $$
DECLARE
    v_dia       int;
    v_bitacora  bigint;
    v_lancha    record;
    v_gen       bigint := (SELECT id FROM equipos WHERE tipo = 'GENERADOR' LIMIT 1);
    v_horas     numeric;
BEGIN
    FOR v_dia IN REVERSE 30..1 LOOP
        FOR v_lancha IN
            SELECT l.id, l.matricula, e.id AS equipo_id, e.horometro_inicial, row_number() OVER (ORDER BY l.id) AS n
              FROM lanchas l JOIN equipos e ON e.lancha_id = l.id AND e.tipo = 'PROPULSION' ORDER BY l.id
        LOOP
            INSERT INTO bitacora (lancha_id, fecha) VALUES (v_lancha.id, current_date - v_dia)
            RETURNING id INTO v_bitacora;
            v_horas := v_lancha.horometro_inicial + (31 - v_dia) * (5 + v_lancha.n) + (v_dia % 3);
            INSERT INTO bitacora_lecturas (bitacora_id, equipo_id, horometro)
            VALUES (v_bitacora, v_lancha.equipo_id, (SELECT max(x) FROM (VALUES (v_horas),
                        ((SELECT horometro_actual FROM equipos WHERE id = v_lancha.equipo_id))) t(x)));
            IF v_lancha.n = 1 THEN
                INSERT INTO bitacora_lecturas (bitacora_id, equipo_id, horometro)
                VALUES (v_bitacora, v_gen, 900 + (31 - v_dia) * 4);
            END IF;
            INSERT INTO bitacora_respuestas (bitacora_id, item_id, resultado, comentario)
            SELECT v_bitacora, i.id, CASE WHEN f.item IS NULL THEN 'OK' ELSE 'FALLA' END, f.comentario
              FROM items_inspeccion i
              LEFT JOIN demo_fallas f ON f.item = i.nombre AND f.matricula = v_lancha.matricula AND f.dias = v_dia
             WHERE i.activo AND (i.aplica_a = 'LANCHA' OR v_lancha.n = 1);
            PERFORM procesar_bitacora(v_bitacora);
        END LOOP;
    END LOOP;
END $$;

-- Una ODS manual (no vino de la bitácora), clasificada por lo que falló
INSERT INTO ordenes (lancha_id, tipo, prioridad, descripcion, item_id)
SELECT l.id, 'CORRECTIVO', 'ALTA', 'Dirección dura al virar a estribor', i.id
  FROM lanchas l, items_inspeccion i WHERE l.matricula = 'DEMO-003' AND i.nombre = 'Sistema de dirección';

-- Trabajo de los mecánicos: las fallas más viejas ya se repararon y aprobaron
DO $$
DECLARE
    v_ods     record;
    v_desde   timestamptz;
    v_fuera   numeric;
    v_trabajo numeric;
    v_n       int := 0;
    v_motivo_repuesto bigint := (SELECT id FROM motivos_inoperatividad WHERE nombre = 'Repuesto no disponible');
    v_motivo_horario  bigint := (SELECT id FROM motivos_inoperatividad WHERE nombre = 'Fuera del horario laboral');
BEGIN
    FOR v_ods IN
        SELECT o.id, b.fecha, f.dias
          FROM ordenes o
          JOIN bitacora_respuestas r ON r.id = o.falla_id
          JOIN bitacora b ON b.id = r.bitacora_id
          JOIN lanchas l ON l.id = b.lancha_id
          JOIN demo_fallas f ON f.matricula = l.matricula AND f.dias = current_date - b.fecha
         WHERE o.tipo = 'CORRECTIVO' AND f.dias >= 9
         ORDER BY b.fecha
    LOOP
        v_n := v_n + 1;
        v_desde := v_ods.fecha + time '07:30';
        v_fuera := CASE WHEN v_n % 2 = 0 THEN 30 ELSE 6 END;   -- unas se resuelven en el día, otras esperan repuesto
        v_trabajo := CASE WHEN v_n % 2 = 0 THEN 4 ELSE 3 END;
        UPDATE ordenes SET estado = 'EN_PROCESO' WHERE id = v_ods.id;
        UPDATE ordenes SET diagnostico = 'Revisado en muelle por el mecánico de guardia' WHERE id = v_ods.id;
        IF v_n % 2 = 0 THEN
            INSERT INTO ordenes_demoras (orden_id, motivo_id, horas) VALUES
                (v_ods.id, v_motivo_repuesto, 14), (v_ods.id, v_motivo_horario, v_fuera - v_trabajo - 14);
        END IF;
        UPDATE ordenes SET estado = 'EJECUTADA', fecha_ejecucion = v_ods.fecha + CASE WHEN v_n % 2 = 0 THEN 1 ELSE 0 END,
                           trabajo_realizado = 'Reparación realizada y probada en navegación',
                           horas_reparacion = v_trabajo, horas_hombre = v_trabajo * 2,
                           fuera_servicio = true, fuera_servicio_desde = v_desde,
                           fuera_servicio_hasta = v_desde + make_interval(hours => v_fuera::int),
                           servicio_contratado = (v_n = 3), proveedor_id = CASE WHEN v_n = 3 THEN (SELECT min(id) FROM proveedores) END,
                           pertenece_programa = false,
                           horometro_ejecucion = (SELECT horometro_actual FROM equipos WHERE id = ordenes.equipo_id)
         WHERE id = v_ods.id;
        -- La más reciente queda «Por aprobar» para que el supervisor la apruebe en la demo
        IF v_ods.dias > 9 THEN
            PERFORM set_config('app.comentario', 'Aprobada por el supervisor', true);
            UPDATE ordenes SET estado = 'CERRADA' WHERE id = v_ods.id;
        END IF;
    END LOOP;

    -- La falla de la bomba de achique: la Pelícano sigue parada (sin «volvió a operar»)
    UPDATE ordenes o SET estado = 'EN_PROCESO'
      FROM bitacora_respuestas r JOIN items_inspeccion i ON i.id = r.item_id
     WHERE r.id = o.falla_id AND i.nombre = 'Bomba de achique';
    UPDATE ordenes o SET diagnostico = 'Motor de la bomba quemado; se pidió el repuesto',
                         fuera_servicio = true, fuera_servicio_desde = (current_date - 6) + time '08:00'
      FROM bitacora_respuestas r JOIN items_inspeccion i ON i.id = r.item_id
     WHERE r.id = o.falla_id AND i.nombre = 'Bomba de achique';
END $$;

-- Una preventiva hecha y aprobada (reprograma su tarea); las demás quedan pendientes
DO $$
DECLARE
    v_ods record;
BEGIN
    SELECT o.id, e.horometro_actual INTO v_ods
      FROM ordenes o JOIN tareas t ON t.id = o.tarea_id JOIN equipos e ON e.id = t.equipo_id
     WHERE o.tipo = 'PREVENTIVO' AND o.estado = 'ABIERTA' AND t.intervalo_horas IS NOT NULL
     ORDER BY o.id LIMIT 1;
    IF v_ods.id IS NULL THEN
        RETURN;
    END IF;
    UPDATE ordenes SET estado = 'EN_PROCESO' WHERE id = v_ods.id;
    INSERT INTO ordenes_materiales (orden_id, material_id, cantidad)
    SELECT v_ods.id, m.id, v.cantidad
      FROM (VALUES ('Filtro de aceite DEMO-01', 1), ('Aceite 15W-40', 12)) v(material, cantidad)
      JOIN materiales m ON m.nombre = v.material;
    UPDATE ordenes SET estado = 'EJECUTADA', fecha_ejecucion = current_date - 1, horometro_ejecucion = v_ods.horometro_actual,
                       trabajo_realizado = 'Servicio preventivo según el programa', horas_reparacion = 1.5, horas_hombre = 1.5,
                       fuera_servicio = false, servicio_contratado = false
     WHERE id = v_ods.id;
    PERFORM set_config('app.comentario', 'Aprobada por el supervisor', true);
    UPDATE ordenes SET estado = 'CERRADA' WHERE id = v_ods.id;
END $$;

-- Fallas que se repiten (después de reparadas), para el indicador de recurrencia
INSERT INTO ordenes (lancha_id, tipo, prioridad, descripcion, item_id)
SELECT l.id, 'CORRECTIVO', v.prioridad, v.descripcion, i.id
  FROM (VALUES ('DEMO-001', 'Luz de navegación babor', 'MEDIA', 'Volvió a fallar la luz de babor'),
               ('DEMO-003', 'Batería y bornes', 'ALTA', 'Arranque lento de nuevo')) v(matricula, item, prioridad, descripcion)
  JOIN lanchas l ON l.matricula = v.matricula
  JOIN items_inspeccion i ON i.nombre = v.item;

SELECT generar_ordenes_preventivas();
