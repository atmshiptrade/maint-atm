-- =====================================================================
--  005 · Módulo Data, edición de bitácoras y "ODS"
--
--  * En la empresa se dice ODS (Orden de Servicio): los mensajes de la base
--    usan ese término. Internamente la tabla sigue siendo "ordenes".
--  * Categorías editables desde el módulo Data: se desactivan, no se borran
--    si ya se usaron.
--  * Una bitácora diaria se puede corregir; cada corrección queda registrada
--    (quién, cuándo, qué cambió y por qué). Una falla que ya tiene ODS no se
--    puede quitar: primero se cancela la ODS.
-- =====================================================================

ALTER TABLE categorias ADD COLUMN activa boolean NOT NULL DEFAULT true;

CREATE TABLE bitacora_cambios (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    bitacora_id  bigint NOT NULL REFERENCES bitacora(id) ON DELETE CASCADE,
    usuario_id   bigint REFERENCES usuarios(id) DEFAULT usuario_actual(),
    fecha        timestamptz NOT NULL DEFAULT now(),
    motivo       text NOT NULL,
    detalle      text NOT NULL
);
CREATE INDEX ix_bitacora_cambios ON bitacora_cambios (bitacora_id);

-- Una falla con ODS no se puede convertir en OK / N.A. desde la edición
CREATE FUNCTION trg_respuestas_proteger() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_orden bigint;
BEGIN
    IF NEW.resultado <> 'FALLA' THEN
        SELECT id INTO v_orden FROM ordenes WHERE falla_id = OLD.id LIMIT 1;
        IF FOUND THEN
            RAISE EXCEPTION 'Esa falla ya generó la ODS #%: si fue un error, cancela la ODS en lugar de editar la bitácora', v_orden;
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER respuestas_proteger
    BEFORE UPDATE OF resultado ON bitacora_respuestas
    FOR EACH ROW WHEN (OLD.resultado = 'FALLA') EXECUTE FUNCTION trg_respuestas_proteger();

-- Mensajes con "ODS"
UPDATE bitacora SET observacion = replace(observacion, 'la OT #', 'la ODS #') WHERE origen = 'OT';

CREATE OR REPLACE FUNCTION trg_ordenes_validar() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_tarea   tareas%ROWTYPE;
    v_equipo  equipos%ROWTYPE;
    v_lancha  bigint;
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.estado IN ('CERRADA', 'CANCELADA') THEN
        RAISE EXCEPTION 'La ODS #% está % y ya no se puede modificar', OLD.id, lower(OLD.estado);
    END IF;

    IF TG_OP = 'INSERT' AND NEW.estado <> 'ABIERTA' THEN
        RAISE EXCEPTION 'Una ODS nueva siempre nace ABIERTA';
    ELSIF TG_OP = 'UPDATE' AND NEW.estado <> OLD.estado
          AND NOT (OLD.estado || '>' || NEW.estado = ANY (ARRAY[
              'ABIERTA>EN_PROCESO', 'ABIERTA>CANCELADA',
              'EN_PROCESO>EJECUTADA', 'EN_PROCESO>ABIERTA', 'EN_PROCESO>CANCELADA',
              'EJECUTADA>CERRADA', 'EJECUTADA>EN_PROCESO', 'EJECUTADA>CANCELADA'])) THEN
        RAISE EXCEPTION 'La ODS #% no puede pasar de % a %', NEW.id, OLD.estado, NEW.estado;
    END IF;

    -- Origen: la tarea define el equipo; la falla, la lancha donde se reportó
    IF NEW.tarea_id IS NOT NULL THEN
        SELECT * INTO v_tarea FROM tareas WHERE id = NEW.tarea_id;
        NEW.equipo_id := v_tarea.equipo_id;
    ELSIF NEW.falla_id IS NOT NULL THEN
        SELECT b.lancha_id INTO v_lancha
          FROM bitacora_respuestas r JOIN bitacora b ON b.id = r.bitacora_id
         WHERE r.id = NEW.falla_id;
        IF v_lancha IS DISTINCT FROM NEW.lancha_id THEN
            RAISE EXCEPTION 'La falla de origen es de otra lancha';
        END IF;
    END IF;
    -- Si el equipo es el horómetro de una lancha, la ODS es de esa lancha
    IF NEW.equipo_id IS NOT NULL THEN
        SELECT * INTO v_equipo FROM equipos WHERE id = NEW.equipo_id;
        IF v_equipo.tipo = 'PROPULSION' THEN
            IF NEW.lancha_id IS NOT NULL AND NEW.lancha_id <> v_equipo.lancha_id THEN
                RAISE EXCEPTION 'El equipo no corresponde a la lancha de la ODS';
            END IF;
            NEW.lancha_id := v_equipo.lancha_id;
        END IF;
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
    v_equipo   equipos%ROWTYPE;
    v_bitacora bigint;
BEGIN
    SELECT * INTO v_equipo FROM equipos WHERE id = NEW.equipo_id;
    IF NEW.horometro_ejecucion > v_equipo.horometro_actual THEN
        INSERT INTO bitacora (lancha_id, fecha, origen, observacion)
        VALUES (v_equipo.lancha_id, NEW.fecha_ejecucion, 'OT', format('Lectura tomada al ejecutar la ODS #%s', NEW.id))
        RETURNING id INTO v_bitacora;
        INSERT INTO bitacora_lecturas (bitacora_id, equipo_id, horometro)
        VALUES (v_bitacora, NEW.equipo_id, NEW.horometro_ejecucion);
    END IF;
    RETURN NULL;
END $$;
