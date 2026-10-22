-- =====================================================================
--  003 · La lancha como unidad, generadores y códigos automáticos
--
--  * El horómetro se maneja por LANCHA: cada lancha tiene un equipo de
--    propulsión que se crea junto con ella y no se administra aparte.
--  * Los generadores son equipos de una lancha con su propio horómetro,
--    programa de mantenimiento y órdenes.
--  * La tabla "motores" pasa a ser "equipos" (PROPULSION o GENERADOR).
--  * Códigos LAN-001 / GEN-001 automáticos y sin saltos.
--  * Las OT preventivas ya no gastan correlativos al revisar tareas que
--    ya tienen una OT pendiente.
-- =====================================================================

-- Objetos que dependen de lo que cambia: se recrean más abajo
DROP VIEW v_estado_tareas;
DROP VIEW v_uso_motores;
DROP FUNCTION generar_ordenes_preventivas(bigint);
DROP TRIGGER motores_proteger ON motores;
DROP TRIGGER lecturas_validar ON bitacora_lecturas;
DROP TRIGGER lecturas_sincronizar ON bitacora_lecturas;
DROP TRIGGER tareas_validar ON tareas;
DROP FUNCTION trg_motores_proteger();
DROP FUNCTION trg_lecturas_validar();
DROP FUNCTION trg_lecturas_sincronizar();
DROP FUNCTION trg_tareas_validar();
DROP FUNCTION recalcular_horometro(bigint);

-- ---------------------------------------------------------------------
-- Lanchas: código automático LAN-001, LAN-002...
-- ---------------------------------------------------------------------
ALTER TABLE lanchas ADD COLUMN numero integer;
UPDATE lanchas l SET numero = o.n
  FROM (SELECT id, row_number() OVER (ORDER BY id) AS n FROM lanchas) o
 WHERE o.id = l.id;
ALTER TABLE lanchas ALTER COLUMN numero SET NOT NULL;
ALTER TABLE lanchas ADD CONSTRAINT lanchas_numero_key UNIQUE (numero);
ALTER TABLE lanchas DROP COLUMN codigo_equipo;
ALTER TABLE lanchas ADD COLUMN codigo text GENERATED ALWAYS AS (
    'LAN-' || CASE WHEN numero < 1000 THEN lpad(numero::text, 3, '0') ELSE numero::text END) STORED;

-- ---------------------------------------------------------------------
-- motores -> equipos
-- ---------------------------------------------------------------------
ALTER TABLE motores RENAME TO equipos;
ALTER INDEX motores_pkey RENAME TO equipos_pkey;
ALTER TABLE equipos RENAME CONSTRAINT motores_lancha_id_fkey TO equipos_lancha_id_fkey;
ALTER TABLE equipos RENAME CONSTRAINT motores_horometro_inicial_check TO equipos_horometro_inicial_check;
DROP INDEX ux_motores_posicion_activa;
ALTER TABLE equipos RENAME COLUMN posicion TO nombre;

ALTER TABLE equipos ADD COLUMN tipo text NOT NULL DEFAULT 'PROPULSION';
ALTER TABLE equipos ALTER COLUMN tipo DROP DEFAULT;
ALTER TABLE equipos ADD CONSTRAINT equipos_tipo_check CHECK (tipo IN ('PROPULSION', 'GENERADOR'));
ALTER TABLE equipos ADD COLUMN potencia_kva numeric(7,1) CONSTRAINT equipos_potencia_check CHECK (potencia_kva > 0);
ALTER TABLE equipos ADD COLUMN numero integer;     -- correlativo de los generadores
ALTER TABLE equipos ADD COLUMN codigo text GENERATED ALWAYS AS (
    CASE WHEN tipo = 'GENERADOR'
         THEN 'GEN-' || CASE WHEN numero < 1000 THEN lpad(numero::text, 3, '0') ELSE numero::text END
    END) STORED;

-- Una sola propulsión activa por lancha (asume a lo sumo un motor activo por lancha en los datos previos)
CREATE UNIQUE INDEX ux_equipos_propulsion ON equipos (lancha_id) WHERE tipo = 'PROPULSION' AND activo;
CREATE UNIQUE INDEX ux_equipos_nombre_activo ON equipos (lancha_id, nombre) WHERE activo;
CREATE UNIQUE INDEX ux_equipos_numero_generador ON equipos (numero) WHERE tipo = 'GENERADOR';

-- Cada lancha existente recibe su equipo de propulsión (horómetro 0: se corrige al editar la lancha)
INSERT INTO equipos (lancha_id, tipo, nombre, fecha_alta, horometro_inicial, horometro_actual)
SELECT l.id, 'PROPULSION', 'Motores', l.creado_en::date, 0, 0
  FROM lanchas l
 WHERE NOT EXISTS (SELECT 1 FROM equipos e WHERE e.lancha_id = l.id AND e.tipo = 'PROPULSION' AND e.activo);

-- ---------------------------------------------------------------------
-- motor_id -> equipo_id en tareas, lecturas y órdenes
-- ---------------------------------------------------------------------
ALTER TABLE tareas RENAME COLUMN motor_id TO equipo_id;
ALTER TABLE tareas RENAME CONSTRAINT tareas_motor_id_fkey TO tareas_equipo_id_fkey;
-- Las tareas "de la lancha" pasan a su equipo de propulsión: toda tarea tiene equipo
UPDATE tareas t SET equipo_id = e.id
  FROM equipos e
 WHERE t.equipo_id IS NULL AND e.lancha_id = t.lancha_id AND e.tipo = 'PROPULSION' AND e.activo;
ALTER TABLE tareas ALTER COLUMN equipo_id SET NOT NULL;
ALTER TABLE tareas DROP CONSTRAINT tareas_horas_requieren_motor;

ALTER TABLE bitacora_lecturas RENAME COLUMN motor_id TO equipo_id;
ALTER TABLE bitacora_lecturas RENAME CONSTRAINT bitacora_lecturas_motor_id_fkey TO bitacora_lecturas_equipo_id_fkey;
ALTER TABLE bitacora_lecturas RENAME CONSTRAINT bitacora_lecturas_bitacora_id_motor_id_key TO bitacora_lecturas_bitacora_id_equipo_id_key;
ALTER INDEX ix_lecturas_motor RENAME TO ix_lecturas_equipo;

ALTER TABLE ordenes RENAME COLUMN motor_id TO equipo_id;
ALTER TABLE ordenes RENAME CONSTRAINT ordenes_motor_id_fkey TO ordenes_equipo_id_fkey;

INSERT INTO categorias (nombre) VALUES ('Generador') ON CONFLICT (nombre) DO NOTHING;


-- =====================================================================
--  Correlativos sin saltos (se asignan dentro de la transacción: si el
--  guardado falla, el número no se pierde)
-- =====================================================================
CREATE FUNCTION trg_lanchas_numero() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        PERFORM pg_advisory_xact_lock(hashtext('lanchas.numero'));
        NEW.numero := coalesce((SELECT max(numero) FROM lanchas), 0) + 1;
    ELSIF NEW.numero <> OLD.numero THEN
        RAISE EXCEPTION 'El código de la lancha no se modifica';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER lanchas_numero
    BEFORE INSERT OR UPDATE ON lanchas
    FOR EACH ROW EXECUTE FUNCTION trg_lanchas_numero();


-- =====================================================================
--  Horómetro (por equipo)
-- =====================================================================
CREATE FUNCTION recalcular_horometro(p_equipo_id bigint) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM set_config('app.sync_horometro', 'on', true);
    UPDATE equipos e
       SET horometro_actual = coalesce(
               (SELECT max(l.horometro) FROM bitacora_lecturas l WHERE l.equipo_id = e.id),
               e.horometro_inicial)
     WHERE e.id = p_equipo_id;
    PERFORM set_config('app.sync_horometro', 'off', true);
END $$;

CREATE FUNCTION etiqueta_equipo(e equipos) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN e.tipo = 'GENERADOR' THEN 'Generador ' || e.codigo ELSE 'Lancha' END
$$;

-- El horómetro no se edita a mano; el tipo, el correlativo y la lancha no cambian
CREATE FUNCTION trg_equipos_proteger() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.horometro_actual := NEW.horometro_inicial;
        IF NEW.tipo = 'GENERADOR' THEN
            PERFORM pg_advisory_xact_lock(hashtext('equipos.generador.numero'));
            NEW.numero := coalesce((SELECT max(numero) FROM equipos WHERE tipo = 'GENERADOR'), 0) + 1;
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.tipo <> OLD.tipo OR NEW.numero IS DISTINCT FROM OLD.numero THEN
        RAISE EXCEPTION 'El tipo y el código de un equipo no se modifican';
    END IF;
    IF NEW.lancha_id <> OLD.lancha_id THEN
        RAISE EXCEPTION 'Un equipo no se mueve de lancha: desactívalo y regístralo en la otra lancha';
    END IF;

    IF NEW.horometro_inicial IS DISTINCT FROM OLD.horometro_inicial THEN
        IF EXISTS (SELECT 1 FROM bitacora_lecturas WHERE equipo_id = OLD.id) THEN
            RAISE EXCEPTION 'El horómetro inicial ya no se puede cambiar: hay lecturas en la bitácora';
        END IF;
        NEW.horometro_actual := NEW.horometro_inicial;   -- corrección de un error de captura
    ELSIF NEW.horometro_actual IS DISTINCT FROM OLD.horometro_actual
          AND coalesce(current_setting('app.sync_horometro', true), '') <> 'on' THEN
        RAISE EXCEPTION 'El horómetro es de solo lectura: se actualiza registrando una lectura en la bitácora';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER equipos_proteger
    BEFORE INSERT OR UPDATE ON equipos
    FOR EACH ROW EXECUTE FUNCTION trg_equipos_proteger();

-- Validación de lecturas: el equipo es de la lancha, nunca hacia atrás, nunca más de 24 h/día
CREATE FUNCTION trg_lecturas_validar() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_equipo  equipos%ROWTYPE;
    v_bit     bitacora%ROWTYPE;
    v_nombre  text;
    v_prev_h  numeric;
    v_prev_f  date;
    v_next_h  numeric;
    v_dias    integer;
BEGIN
    SELECT * INTO v_equipo FROM equipos  WHERE id = NEW.equipo_id;
    SELECT * INTO v_bit    FROM bitacora WHERE id = NEW.bitacora_id;
    v_nombre := etiqueta_equipo(v_equipo);

    IF v_equipo.lancha_id <> v_bit.lancha_id THEN
        RAISE EXCEPTION '%: no pertenece a la lancha de la bitácora', v_nombre;
    END IF;
    IF NOT v_equipo.activo THEN
        RAISE EXCEPTION '%: está desactivado', v_nombre;
    END IF;
    IF v_bit.fecha < v_equipo.fecha_alta THEN
        RAISE EXCEPTION '%: la fecha % es anterior a su alta en el sistema (%)',
              v_nombre, to_char(v_bit.fecha, 'DD/MM/YYYY'), to_char(v_equipo.fecha_alta, 'DD/MM/YYYY');
    END IF;

    SELECT l.horometro, b.fecha INTO v_prev_h, v_prev_f
      FROM bitacora_lecturas l JOIN bitacora b ON b.id = l.bitacora_id
     WHERE l.equipo_id = NEW.equipo_id AND b.fecha <= v_bit.fecha AND l.id <> NEW.id
     ORDER BY b.fecha DESC, l.horometro DESC
     LIMIT 1;
    IF NOT FOUND THEN
        v_prev_h := v_equipo.horometro_inicial;
        v_prev_f := v_equipo.fecha_alta;
    END IF;

    IF NEW.horometro < v_prev_h THEN
        RAISE EXCEPTION '%: % h es menor que la lectura anterior (% h del %)',
              v_nombre, NEW.horometro, v_prev_h, to_char(v_prev_f, 'DD/MM/YYYY');
    END IF;

    v_dias := v_bit.fecha - v_prev_f + 1;
    IF NEW.horometro - v_prev_h > 24 * v_dias THEN
        RAISE EXCEPTION '%: +% h en % día(s) es imposible (máximo 24 h por día). ¿Error de digitación?',
              v_nombre, NEW.horometro - v_prev_h, v_dias;
    END IF;

    SELECT l.horometro INTO v_next_h
      FROM bitacora_lecturas l JOIN bitacora b ON b.id = l.bitacora_id
     WHERE l.equipo_id = NEW.equipo_id AND b.fecha > v_bit.fecha AND l.id <> NEW.id
     ORDER BY b.fecha, l.horometro
     LIMIT 1;
    IF FOUND AND NEW.horometro > v_next_h THEN
        RAISE EXCEPTION '%: % h supera a una lectura posterior (% h)', v_nombre, NEW.horometro, v_next_h;
    END IF;

    RETURN NEW;
END $$;

CREATE TRIGGER lecturas_validar
    BEFORE INSERT OR UPDATE ON bitacora_lecturas
    FOR EACH ROW EXECUTE FUNCTION trg_lecturas_validar();

CREATE FUNCTION trg_lecturas_sincronizar() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        PERFORM recalcular_horometro(OLD.equipo_id);
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        PERFORM recalcular_horometro(NEW.equipo_id);
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER lecturas_sincronizar
    AFTER INSERT OR UPDATE OR DELETE ON bitacora_lecturas
    FOR EACH ROW EXECUTE FUNCTION trg_lecturas_sincronizar();

CREATE FUNCTION trg_tareas_validar() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_equipo equipos%ROWTYPE;
BEGIN
    SELECT * INTO v_equipo FROM equipos WHERE id = NEW.equipo_id;
    IF v_equipo.lancha_id <> NEW.lancha_id THEN
        RAISE EXCEPTION 'El equipo elegido no pertenece a la lancha';
    END IF;
    IF TG_OP = 'INSERT' AND NOT v_equipo.activo THEN
        RAISE EXCEPTION '%: está desactivado', etiqueta_equipo(v_equipo);
    END IF;
    IF NEW.horometro_ultimo_servicio > v_equipo.horometro_actual THEN
        RAISE EXCEPTION '%: el último servicio (% h) no puede ser mayor que el horómetro actual (% h)',
              etiqueta_equipo(v_equipo), NEW.horometro_ultimo_servicio, v_equipo.horometro_actual;
    END IF;
    IF NEW.fecha_ultimo_servicio > current_date THEN
        RAISE EXCEPTION 'La fecha del último servicio no puede ser futura';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER tareas_validar
    BEFORE INSERT OR UPDATE ON tareas
    FOR EACH ROW EXECUTE FUNCTION trg_tareas_validar();


-- =====================================================================
--  Órdenes (mismas reglas, ahora con equipo_id)
-- =====================================================================
CREATE OR REPLACE FUNCTION trg_ordenes_validar() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_tarea  tareas%ROWTYPE;
    v_lancha bigint;
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.estado IN ('CERRADA', 'CANCELADA') THEN
        RAISE EXCEPTION 'La OT #% está % y ya no se puede modificar', OLD.id, lower(OLD.estado);
    END IF;

    IF TG_OP = 'INSERT' AND NEW.estado <> 'ABIERTA' THEN
        RAISE EXCEPTION 'Una OT nueva siempre nace ABIERTA';
    ELSIF TG_OP = 'UPDATE' AND NEW.estado <> OLD.estado
          AND NOT (OLD.estado || '>' || NEW.estado = ANY (ARRAY[
              'ABIERTA>EN_PROCESO', 'ABIERTA>CANCELADA',
              'EN_PROCESO>EJECUTADA', 'EN_PROCESO>ABIERTA', 'EN_PROCESO>CANCELADA',
              'EJECUTADA>CERRADA', 'EJECUTADA>EN_PROCESO', 'EJECUTADA>CANCELADA'])) THEN
        RAISE EXCEPTION 'La OT #% no puede pasar de % a %', NEW.id, OLD.estado, NEW.estado;
    END IF;

    -- La OT hereda lancha y equipo de su origen
    IF NEW.tarea_id IS NOT NULL THEN
        SELECT * INTO v_tarea FROM tareas WHERE id = NEW.tarea_id;
        NEW.lancha_id := v_tarea.lancha_id;
        NEW.equipo_id := v_tarea.equipo_id;
    ELSIF NEW.falla_id IS NOT NULL THEN
        SELECT b.lancha_id INTO v_lancha
          FROM bitacora_respuestas r JOIN bitacora b ON b.id = r.bitacora_id
         WHERE r.id = NEW.falla_id;
        IF v_lancha <> NEW.lancha_id THEN
            RAISE EXCEPTION 'La falla de origen es de otra lancha';
        END IF;
    END IF;
    IF NEW.equipo_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM equipos WHERE id = NEW.equipo_id AND lancha_id = NEW.lancha_id) THEN
        RAISE EXCEPTION 'El equipo no pertenece a la lancha de la OT';
    END IF;

    IF NEW.estado = 'EN_PROCESO' AND (TG_OP = 'INSERT' OR OLD.estado <> 'EN_PROCESO') THEN
        NEW.fecha_inicio := coalesce(NEW.fecha_inicio, now());
        NEW.asignado_a   := coalesce(NEW.asignado_a, usuario_actual());
    END IF;

    IF NEW.estado = 'EJECUTADA' AND OLD.estado <> 'EJECUTADA' THEN
        NEW.fecha_ejecucion := coalesce(NEW.fecha_ejecucion, current_date);
        NEW.ejecutado_por   := usuario_actual();
        IF NEW.fecha_ejecucion > current_date THEN
            RAISE EXCEPTION 'La fecha de ejecución no puede ser futura';
        END IF;
        IF NEW.fecha_ejecucion < NEW.fecha_apertura::date - 365 THEN
            RAISE EXCEPTION 'La fecha de ejecución es demasiado antigua';
        END IF;
        IF NEW.horometro_ejecucion < v_tarea.horometro_ultimo_servicio THEN
            RAISE EXCEPTION 'El horómetro de ejecución (% h) es menor que el del último servicio (% h)',
                  NEW.horometro_ejecucion, v_tarea.horometro_ultimo_servicio;
        END IF;
    END IF;

    IF NEW.estado IN ('CERRADA', 'CANCELADA') THEN
        NEW.fecha_cierre := now();
        NEW.cerrado_por  := usuario_actual();
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION trg_ordenes_al_ejecutar() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_bitacora bigint;
BEGIN
    IF NEW.equipo_id IS NOT NULL
       AND NEW.horometro_ejecucion > (SELECT horometro_actual FROM equipos WHERE id = NEW.equipo_id) THEN
        INSERT INTO bitacora (lancha_id, fecha, origen, observacion)
        VALUES (NEW.lancha_id, NEW.fecha_ejecucion, 'OT', format('Lectura tomada al ejecutar la OT #%s', NEW.id))
        RETURNING id INTO v_bitacora;
        INSERT INTO bitacora_lecturas (bitacora_id, equipo_id, horometro)
        VALUES (v_bitacora, NEW.equipo_id, NEW.horometro_ejecucion);
    END IF;
    RETURN NULL;
END $$;


-- =====================================================================
--  Vistas
-- =====================================================================
CREATE VIEW v_uso_equipos AS
SELECT l.equipo_id,
       round((max(l.horometro) - min(l.horometro)) / nullif(max(b.fecha) - min(b.fecha), 0), 1) AS horas_por_dia
  FROM bitacora_lecturas l
  JOIN bitacora b ON b.id = l.bitacora_id
 WHERE b.fecha >= current_date - 30
 GROUP BY l.equipo_id;

CREATE VIEW v_estado_tareas AS
WITH base AS (
    SELECT t.id AS tarea_id, t.lancha_id, la.nombre AS lancha, la.codigo AS codigo_lancha,
           t.equipo_id, e.tipo AS tipo_equipo, e.codigo AS codigo_equipo, etiqueta_equipo(e) AS equipo,
           t.actividad_id, a.nombre AS actividad, a.categoria_id, c.nombre AS categoria, a.severidad,
           t.intervalo_horas, t.horometro_ultimo_servicio, t.proximo_horometro,
           e.horometro_actual,
           t.proximo_horometro - e.horometro_actual        AS horas_restantes,
           t.intervalo_horas * a.tolerancia_pct / 100      AS ventana_horas,
           t.intervalo_dias, t.fecha_ultimo_servicio, t.proxima_fecha,
           t.proxima_fecha - current_date                  AS dias_restantes,
           ceil(t.intervalo_dias * a.tolerancia_pct / 100) AS ventana_dias,
           u.horas_por_dia,
           t.activa AND e.activo                           AS activa,
           t.notas
      FROM tareas t
      JOIN lanchas la    ON la.id = t.lancha_id
      JOIN equipos e     ON e.id = t.equipo_id
      JOIN actividades a ON a.id = t.actividad_id
      JOIN categorias c  ON c.id = a.categoria_id
      LEFT JOIN v_uso_equipos u ON u.equipo_id = t.equipo_id
), niveles AS (
    SELECT b.*,
           CASE WHEN NOT activa THEN NULL
                ELSE greatest(
                    CASE WHEN horas_restantes IS NULL          THEN 0
                         WHEN horas_restantes < -ventana_horas THEN 3
                         WHEN horas_restantes <= 0             THEN 2
                         WHEN horas_restantes <= ventana_horas THEN 1
                         ELSE 0 END,
                    CASE WHEN dias_restantes IS NULL           THEN 0
                         WHEN dias_restantes < -ventana_dias   THEN 3
                         WHEN dias_restantes <= 0              THEN 2
                         WHEN dias_restantes <= ventana_dias   THEN 1
                         ELSE 0 END)
           END AS nivel
      FROM base b
)
SELECT n.*,
       coalesce((ARRAY['AL DIA', 'PROXIMA', 'VENCIDA', 'FUERA DE TOLERANCIA'])[n.nivel + 1], 'INACTIVA') AS estado,
       least(CASE WHEN n.horas_restantes <= 0 THEN current_date
                  WHEN n.horas_por_dia > 0 THEN current_date + ceil(n.horas_restantes / n.horas_por_dia)::int
             END,
             n.proxima_fecha) AS fecha_estimada,
       (SELECT o.id FROM ordenes o
         WHERE o.tarea_id = n.tarea_id AND o.estado IN ('ABIERTA', 'EN_PROCESO', 'EJECUTADA')) AS ot_pendiente
  FROM niveles n;


-- =====================================================================
--  Generación de OT preventivas: solo intenta las tareas sin OT pendiente,
--  así no se gastan correlativos (el índice único sigue de respaldo)
-- =====================================================================
CREATE FUNCTION generar_ordenes_preventivas(p_lancha_id bigint DEFAULT NULL)
RETURNS SETOF ordenes
LANGUAGE sql AS $$
    INSERT INTO ordenes (lancha_id, equipo_id, tipo, prioridad, tarea_id, descripcion)
    SELECT v.lancha_id, v.equipo_id, 'PREVENTIVO', v.severidad, v.tarea_id,
           format('%s%s (programada: %s)', v.actividad,
                  CASE WHEN v.tipo_equipo = 'GENERADOR' THEN ' · ' || v.equipo END,
                  concat_ws(' / ', v.proximo_horometro || ' h', to_char(v.proxima_fecha, 'DD/MM/YYYY')))
      FROM v_estado_tareas v
     WHERE v.nivel >= 1
       AND v.ot_pendiente IS NULL
       AND (p_lancha_id IS NULL OR v.lancha_id = p_lancha_id)
     ORDER BY v.lancha, v.tarea_id
    ON CONFLICT (tarea_id) WHERE estado IN ('ABIERTA', 'EN_PROCESO', 'EJECUTADA') DO NOTHING
    RETURNING *;
$$;
