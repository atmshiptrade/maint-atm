-- =====================================================================
--  012 · Herramientas y repuestos, tiempos de la ODS y análisis de fallas
--
--  * Materiales (repuestos, herramientas e insumos): lista que administra
--    el supervisor en Data. Las actividades indican los que se prevén y las
--    ODS los que se usaron, con su cantidad. El texto libre que ya había
--    queda como «otros materiales».
--  * Tiempos de una ODS (se registran al reportarla):
--      - fuera de servicio: desde cuándo y hasta cuándo la lancha o el
--        generador no estuvo operativo, si la falla o el servicio lo paró;
--      - duración del trabajo: horas de reparación efectiva, de reloj (no
--        por persona: las horas-hombre siguen aparte);
--      - factores externos = fuera de servicio − trabajo, repartido por
--        motivo (repuesto no disponible, fuera del horario laboral…; lista
--        administrada en Data).
--  * Qué falló: las ODS correctivas se clasifican con un ítem de
--    inspección. Las de la bitácora lo heredan de la falla; en las manuales
--    lo elige quien la crea o la reporta. Así se cuenta la falla más
--    recurrente.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Materiales
-- ---------------------------------------------------------------------
CREATE TABLE materiales (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre      text NOT NULL,
    tipo        text NOT NULL CONSTRAINT materiales_tipo_check CHECK (tipo IN ('REPUESTO', 'HERRAMIENTA', 'INSUMO')),
    unidad      text,                                         -- unidad de medida: unidad, litro, galón…
    codigo      text CONSTRAINT materiales_codigo_key UNIQUE,  -- número de parte o referencia
    notas       text,
    activo      boolean NOT NULL DEFAULT true,
    creado_en   timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ux_materiales_nombre ON materiales (lower(nombre));

-- Lo que prevé una actividad (precarga la ODS preventiva)
CREATE TABLE actividades_materiales (
    actividad_id  integer NOT NULL REFERENCES actividades(id) ON DELETE CASCADE,
    material_id   bigint  NOT NULL REFERENCES materiales(id),
    cantidad      numeric(10,2) CONSTRAINT actividades_materiales_cantidad CHECK (cantidad > 0),
    PRIMARY KEY (actividad_id, material_id)
);
CREATE INDEX ix_actividades_materiales_material ON actividades_materiales (material_id);

-- Lo que se usó en una ODS
CREATE TABLE ordenes_materiales (
    orden_id     bigint NOT NULL REFERENCES ordenes(id) ON DELETE CASCADE,
    material_id  bigint NOT NULL REFERENCES materiales(id),
    cantidad     numeric(10,2) CONSTRAINT ordenes_materiales_cantidad CHECK (cantidad > 0),
    PRIMARY KEY (orden_id, material_id)
);
CREATE INDEX ix_ordenes_materiales_material ON ordenes_materiales (material_id);

-- ---------------------------------------------------------------------
-- Motivos del tiempo fuera de servicio que no es reparación
-- ---------------------------------------------------------------------
CREATE TABLE motivos_inoperatividad (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre      text NOT NULL CONSTRAINT motivos_inoperatividad_nombre_key UNIQUE,
    activo      boolean NOT NULL DEFAULT true,
    creado_en   timestamptz NOT NULL DEFAULT now()
);
INSERT INTO motivos_inoperatividad (nombre) VALUES
    ('Repuesto no disponible'),
    ('Fuera del horario laboral'),
    ('Esperando proveedor o servicio contratado'),
    ('Esperando aprobación o presupuesto'),
    ('Sin mecánico disponible'),
    ('Condiciones del mar o del clima'),
    ('Otro');

CREATE TABLE ordenes_demoras (
    orden_id   bigint NOT NULL REFERENCES ordenes(id) ON DELETE CASCADE,
    motivo_id  bigint NOT NULL REFERENCES motivos_inoperatividad(id),
    horas      numeric(7,2) NOT NULL CONSTRAINT ordenes_demoras_horas CHECK (horas > 0),
    PRIMARY KEY (orden_id, motivo_id)
);
CREATE INDEX ix_ordenes_demoras_motivo ON ordenes_demoras (motivo_id);

-- Materiales y demoras se modifican mientras la ODS está pendiente de reportar
CREATE FUNCTION trg_ordenes_detalle_abierta() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_estado text;
BEGIN
    SELECT estado INTO v_estado FROM ordenes WHERE id = coalesce(NEW.orden_id, OLD.orden_id);
    IF v_estado NOT IN ('ABIERTA', 'EN_PROCESO') THEN
        RAISE EXCEPTION 'La ODS #% ya fue reportada: sus materiales y tiempos ya no se modifican',
              coalesce(NEW.orden_id, OLD.orden_id);
    END IF;
    RETURN coalesce(NEW, OLD);
END $$;

CREATE TRIGGER materiales_orden_abierta
    BEFORE INSERT OR UPDATE OR DELETE ON ordenes_materiales
    FOR EACH ROW EXECUTE FUNCTION trg_ordenes_detalle_abierta();
CREATE TRIGGER demoras_orden_abierta
    BEFORE INSERT OR UPDATE OR DELETE ON ordenes_demoras
    FOR EACH ROW EXECUTE FUNCTION trg_ordenes_detalle_abierta();

-- ---------------------------------------------------------------------
-- ODS: qué falló y tiempos
-- ---------------------------------------------------------------------
ALTER TABLE ordenes
    ADD COLUMN item_id              smallint REFERENCES items_inspeccion(id),
    ADD COLUMN fuera_servicio       boolean,
    ADD COLUMN fuera_servicio_desde timestamptz,
    ADD COLUMN fuera_servicio_hasta timestamptz,
    ADD COLUMN horas_reparacion     numeric(7,2) CONSTRAINT ordenes_horas_reparacion_check CHECK (horas_reparacion > 0);
CREATE INDEX ix_ordenes_item ON ordenes (item_id);

-- Las correctivas de la bitácora ya sabían qué falló
ALTER TABLE ordenes DISABLE TRIGGER ordenes_validar;
UPDATE ordenes o SET item_id = r.item_id FROM bitacora_respuestas r WHERE r.id = o.falla_id;
ALTER TABLE ordenes ENABLE TRIGGER ordenes_validar;

ALTER TABLE ordenes
    ADD CONSTRAINT ordenes_item_solo_correctiva CHECK (item_id IS NULL OR tipo = 'CORRECTIVO'),
    ADD CONSTRAINT ordenes_fuera_servicio_fechas
        CHECK (fuera_servicio IS TRUE OR (fuera_servicio_desde IS NULL AND fuera_servicio_hasta IS NULL)),
    ADD CONSTRAINT ordenes_fuera_servicio_orden
        CHECK (fuera_servicio_hasta IS NULL
               OR (fuera_servicio_desde IS NOT NULL AND fuera_servicio_hasta >= fuera_servicio_desde));

-- Qué falló: el de la bitácora es el ítem reportado; el elegido a mano debe
-- ser de la misma clase de maquinaria (ítem de lancha o de generador)
CREATE FUNCTION trg_ordenes_validar_item() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_item    items_inspeccion%ROWTYPE;
    v_unidad  text;
BEGIN
    IF NEW.falla_id IS NOT NULL THEN
        SELECT item_id INTO NEW.item_id FROM bitacora_respuestas WHERE id = NEW.falla_id;
    ELSIF NEW.item_id IS NOT NULL THEN
        SELECT * INTO v_item FROM items_inspeccion WHERE id = NEW.item_id;
        v_unidad := CASE WHEN (SELECT tipo FROM equipos WHERE id = NEW.equipo_id) = 'GENERADOR'
                         THEN 'GENERADOR' ELSE 'LANCHA' END;
        IF v_item.aplica_a <> v_unidad THEN
            RAISE EXCEPTION '«%» es un ítem %, y esta ODS es de %', v_item.nombre,
                  CASE WHEN v_item.aplica_a = 'GENERADOR' THEN 'del generador' ELSE 'de la lancha' END,
                  CASE WHEN v_unidad = 'GENERADOR' THEN 'un generador' ELSE 'una lancha' END;
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER ordenes_validar_item
    BEFORE INSERT OR UPDATE ON ordenes
    FOR EACH ROW EXECUTE FUNCTION trg_ordenes_validar_item();

-- Tiempos: nada en el futuro; al reportar, todo indicado y coherente
CREATE FUNCTION trg_ordenes_validar_tiempos() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_horas_fuera  numeric;
    v_externas     numeric;
    v_demoras      numeric;
BEGIN
    IF NEW.fuera_servicio_desde > now() + interval '5 minutes' THEN
        RAISE EXCEPTION 'La hora en que quedó fuera de servicio no puede ser futura';
    END IF;
    IF NEW.fuera_servicio_hasta > now() + interval '5 minutes' THEN
        RAISE EXCEPTION 'La hora en que volvió a operar no puede ser futura';
    END IF;

    IF NEW.estado <> 'EJECUTADA' OR (TG_OP = 'UPDATE' AND OLD.estado = 'EJECUTADA') THEN
        RETURN NEW;
    END IF;

    IF NEW.horas_reparacion IS NULL THEN
        RAISE EXCEPTION 'Indica la duración del trabajo (horas de reparación)';
    END IF;
    IF NEW.fuera_servicio IS NULL THEN
        RAISE EXCEPTION 'Indica si la maquinaria quedó fuera de servicio por esta falla o servicio';
    END IF;
    SELECT coalesce(sum(horas), 0) INTO v_demoras FROM ordenes_demoras WHERE orden_id = NEW.id;
    IF NOT NEW.fuera_servicio THEN
        IF v_demoras > 0 THEN
            RAISE EXCEPTION 'Si no quedó fuera de servicio, no hay tiempo por factores externos que repartir';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.fuera_servicio_desde IS NULL OR NEW.fuera_servicio_hasta IS NULL THEN
        RAISE EXCEPTION 'Indica desde cuándo estuvo fuera de servicio y cuándo volvió a operar';
    END IF;
    v_horas_fuera := extract(epoch FROM NEW.fuera_servicio_hasta - NEW.fuera_servicio_desde) / 3600;
    IF NEW.horas_reparacion > v_horas_fuera + 0.01 THEN
        RAISE EXCEPTION 'La duración del trabajo (% h) no puede ser mayor que el tiempo fuera de servicio (% h)',
              round(NEW.horas_reparacion, 2), round(v_horas_fuera, 2);
    END IF;
    v_externas := greatest(v_horas_fuera - NEW.horas_reparacion, 0);
    IF v_demoras > v_externas + 0.01 THEN
        RAISE EXCEPTION 'Los motivos suman % h, pero el tiempo por factores externos es de % h',
              round(v_demoras, 2), round(v_externas, 2);
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER ordenes_validar_tiempos
    BEFORE INSERT OR UPDATE ON ordenes
    FOR EACH ROW EXECUTE FUNCTION trg_ordenes_validar_tiempos();


-- =====================================================================
--  Análisis: cada ODS con su maquinaria, qué falló y sus horas
--
--  * La unidad es el generador si la ODS es de un generador; si no, la
--    lancha. Una falla del generador reportada en la bitácora de una lancha
--    cuenta para el generador.
--  * fecha: el día en que se detectó (el de la bitácora) o se abrió la ODS.
--  * Una ODS que sigue fuera de servicio cuenta hasta ahora.
--  * Las canceladas no cuentan.
-- =====================================================================
CREATE VIEW v_ordenes_analisis AS
SELECT o.id, o.tipo, o.estado, o.prioridad, o.descripcion, o.lancha_id, o.equipo_id,
       CASE WHEN g.id IS NOT NULL THEN 'G' || g.id ELSE 'L' || o.lancha_id END AS unidad_clave,
       CASE WHEN g.id IS NOT NULL THEN 'GENERADOR' ELSE 'LANCHA' END            AS tipo_unidad,
       CASE WHEN g.id IS NOT NULL THEN nombre_unidad(g) ELSE l.nombre END       AS unidad,
       coalesce(b.fecha, o.fecha_apertura::date)                                AS fecha,
       o.item_id, i.nombre AS item, c.nombre AS categoria, a.nombre AS actividad,
       o.fuera_servicio, o.fuera_servicio_desde, o.fuera_servicio_hasta,
       CASE WHEN o.fuera_servicio AND o.fuera_servicio_desde IS NOT NULL
            THEN tstzrange(o.fuera_servicio_desde, greatest(coalesce(o.fuera_servicio_hasta, now()), o.fuera_servicio_desde))
       END AS periodo_fuera,
       CASE WHEN o.fuera_servicio AND o.fuera_servicio_desde IS NOT NULL
            THEN round(extract(epoch FROM greatest(coalesce(o.fuera_servicio_hasta, now()) - o.fuera_servicio_desde,
                                                   interval '0')) / 3600, 2)
       END AS horas_fuera,
       o.horas_reparacion, o.horas_hombre
  FROM ordenes o
  LEFT JOIN equipos g  ON g.id = o.equipo_id AND g.tipo = 'GENERADOR'
  LEFT JOIN lanchas l  ON l.id = o.lancha_id
  LEFT JOIN bitacora_respuestas r ON r.id = o.falla_id
  LEFT JOIN bitacora b ON b.id = r.bitacora_id
  LEFT JOIN items_inspeccion i ON i.id = o.item_id
  LEFT JOIN categorias c ON c.id = i.categoria_id
  LEFT JOIN tareas t ON t.id = o.tarea_id
  LEFT JOIN actividades a ON a.id = t.actividad_id
 WHERE o.estado <> 'CANCELADA';

-- Horas fuera de servicio de cada unidad dentro de un rango: la unión de los
-- periodos de sus ODS, así dos fallas que se solapan no cuentan doble.
CREATE FUNCTION horas_fuera_servicio(p_desde timestamptz, p_hasta timestamptz)
RETURNS TABLE (unidad_clave text, horas numeric)
LANGUAGE sql STABLE AS $$
    WITH union_unidad AS (
        SELECT o.unidad_clave, range_agg(o.periodo_fuera * tstzrange(p_desde, p_hasta)) AS periodos
          FROM v_ordenes_analisis o
         WHERE o.periodo_fuera && tstzrange(p_desde, p_hasta)
         GROUP BY o.unidad_clave
    )
    SELECT u.unidad_clave,
           (SELECT round(coalesce(sum(extract(epoch FROM upper(p) - lower(p))), 0) / 3600, 2) FROM unnest(u.periodos) p)
      FROM union_unidad u
$$;
