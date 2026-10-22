-- =====================================================================
--  007 · ODS vinculadas al programa de mantenimiento
--
--  * Toda ODS indica al reportarse si el servicio corresponde al programa
--    de su lancha o generador, y qué tareas del programa se cumplieron.
--  * Al APROBARSE (cerrar), cada tarea cumplida se reprograma desde el
--    horómetro y la fecha del servicio: vale tanto para preventivas como para
--    correctivas que hicieron un trabajo del plan (ej. se rompió la correa).
--  * Las ODS preventivas que seguían abiertas para esas tareas se cancelan
--    solas ("Cumplida con la ODS #x").
-- =====================================================================

CREATE TABLE ordenes_tareas (
    orden_id  bigint NOT NULL REFERENCES ordenes(id) ON DELETE CASCADE,
    tarea_id  bigint NOT NULL REFERENCES tareas(id),
    PRIMARY KEY (orden_id, tarea_id)
);
CREATE INDEX ix_ordenes_tareas_tarea ON ordenes_tareas (tarea_id);

ALTER TABLE ordenes ADD COLUMN pertenece_programa boolean;

-- Datos previos: las preventivas son del programa; las correctivas ya hechas, no
ALTER TABLE ordenes DISABLE TRIGGER ordenes_validar;
INSERT INTO ordenes_tareas (orden_id, tarea_id) SELECT id, tarea_id FROM ordenes WHERE tarea_id IS NOT NULL;
UPDATE ordenes SET pertenece_programa = true WHERE tipo = 'PREVENTIVO';
UPDATE ordenes SET pertenece_programa = false WHERE tipo = 'CORRECTIVO' AND estado IN ('EJECUTADA', 'CERRADA');
ALTER TABLE ordenes ENABLE TRIGGER ordenes_validar;

ALTER TABLE ordenes
    ADD CONSTRAINT ordenes_programa_definido
        CHECK (estado NOT IN ('EJECUTADA', 'CERRADA') OR pertenece_programa IS NOT NULL),
    ADD CONSTRAINT ordenes_preventiva_del_programa
        CHECK (tipo <> 'PREVENTIVO' OR pertenece_programa IS NOT FALSE);

-- Una preventiva siempre es del programa y queda vinculada a su propia tarea
CREATE FUNCTION trg_ordenes_preventiva_programa() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.tipo = 'PREVENTIVO' THEN
        NEW.pertenece_programa := true;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER ordenes_preventiva_programa
    BEFORE INSERT OR UPDATE ON ordenes
    FOR EACH ROW EXECUTE FUNCTION trg_ordenes_preventiva_programa();

CREATE FUNCTION trg_ordenes_vincular_tarea() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO ordenes_tareas (orden_id, tarea_id) VALUES (NEW.id, NEW.tarea_id) ON CONFLICT DO NOTHING;
    RETURN NULL;
END $$;

CREATE TRIGGER ordenes_vincular_tarea
    AFTER INSERT ON ordenes
    FOR EACH ROW WHEN (NEW.tarea_id IS NOT NULL) EXECUTE FUNCTION trg_ordenes_vincular_tarea();

-- Al reportar: si es del programa, las tareas marcadas deben ser de la misma
-- maquinaria y el horómetro no puede ser menor que su último servicio
CREATE FUNCTION trg_ordenes_validar_programa() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_tarea record;
BEGIN
    IF NEW.pertenece_programa IS NULL THEN
        RAISE EXCEPTION 'Indica si el servicio corresponde al programa de mantenimiento';
    ELSIF NOT NEW.pertenece_programa THEN
        IF EXISTS (SELECT 1 FROM ordenes_tareas WHERE orden_id = NEW.id) THEN
            RAISE EXCEPTION 'La ODS dice que no es del programa pero tiene tareas del programa marcadas';
        END IF;
        RETURN NEW;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM ordenes_tareas WHERE orden_id = NEW.id) THEN
        RAISE EXCEPTION 'Marca qué tareas del programa se cumplieron con este servicio';
    END IF;
    IF EXISTS (SELECT 1 FROM ordenes_tareas ot JOIN tareas t ON t.id = ot.tarea_id
                WHERE ot.orden_id = NEW.id AND t.equipo_id IS DISTINCT FROM NEW.equipo_id) THEN
        RAISE EXCEPTION 'Las tareas marcadas no son del programa de esta maquinaria';
    END IF;

    SELECT a.nombre, t.horometro_ultimo_servicio INTO v_tarea
      FROM ordenes_tareas ot JOIN tareas t ON t.id = ot.tarea_id JOIN actividades a ON a.id = t.actividad_id
     WHERE ot.orden_id = NEW.id AND t.intervalo_horas IS NOT NULL
       AND (NEW.horometro_ejecucion IS NULL OR NEW.horometro_ejecucion < t.horometro_ultimo_servicio)
     LIMIT 1;
    IF FOUND THEN
        IF NEW.horometro_ejecucion IS NULL THEN
            RAISE EXCEPTION 'Indica el horómetro: «%» se programa por horas', v_tarea.nombre;
        END IF;
        RAISE EXCEPTION 'El horómetro del servicio (% h) es menor que el último servicio de «%» (% h)',
              NEW.horometro_ejecucion, v_tarea.nombre, v_tarea.horometro_ultimo_servicio;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER ordenes_validar_programa
    BEFORE UPDATE OF estado ON ordenes
    FOR EACH ROW WHEN (NEW.estado = 'EJECUTADA' AND OLD.estado <> 'EJECUTADA')
    EXECUTE FUNCTION trg_ordenes_validar_programa();

-- Al aprobar: se reprograman todas las tareas cumplidas (no solo la de una preventiva)
CREATE OR REPLACE FUNCTION trg_ordenes_al_cerrar() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_comentario text := current_setting('app.comentario', true);
BEGIN
    UPDATE tareas t
       SET horometro_ultimo_servicio = CASE WHEN t.intervalo_horas IS NOT NULL THEN NEW.horometro_ejecucion END,
           fecha_ultimo_servicio     = CASE WHEN t.intervalo_dias  IS NOT NULL THEN NEW.fecha_ejecucion END
      FROM ordenes_tareas ot
     WHERE ot.orden_id = NEW.id AND ot.tarea_id = t.id;

    -- Las preventivas que esperaban por esas tareas ya no hacen falta
    PERFORM set_config('app.comentario', format('Cumplida con la ODS #%s', NEW.id), true);
    UPDATE ordenes o
       SET estado = 'CANCELADA'
      FROM ordenes_tareas ot
     WHERE ot.orden_id = NEW.id AND o.tarea_id = ot.tarea_id AND o.id <> NEW.id AND o.estado = 'ABIERTA';
    PERFORM set_config('app.comentario', coalesce(v_comentario, ''), true);
    RETURN NULL;
END $$;
