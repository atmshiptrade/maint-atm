-- =====================================================================
--  004 · Generadores independientes
--
--  * Un generador ya no pertenece a una lancha: se mueve de una a otra.
--    En la bitácora se elige la lancha y el generador que lleva ese día;
--    así queda el historial de dónde estuvo cada generador.
--  * Las tareas y órdenes de un generador son del generador (sin lancha).
--  * Los ítems de inspección indican si se revisan en la LANCHA o en el
--    GENERADOR; las fallas del generador abren OT para ese generador.
-- =====================================================================

DROP VIEW v_estado_tareas;
DROP FUNCTION generar_ordenes_preventivas(bigint);
DROP FUNCTION procesar_bitacora(bigint);

-- ---------------------------------------------------------------------
-- Equipos: solo la propulsión tiene lancha
-- ---------------------------------------------------------------------
ALTER TABLE equipos ALTER COLUMN lancha_id DROP NOT NULL;
UPDATE equipos SET lancha_id = NULL WHERE tipo = 'GENERADOR';
ALTER TABLE equipos ADD CONSTRAINT equipos_lancha_solo_propulsion
    CHECK ((tipo = 'PROPULSION') = (lancha_id IS NOT NULL));
DROP INDEX ux_equipos_nombre_activo;

CREATE OR REPLACE FUNCTION trg_equipos_proteger() RETURNS trigger
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
    IF NEW.lancha_id IS DISTINCT FROM OLD.lancha_id THEN
        RAISE EXCEPTION 'El horómetro de una lancha no se mueve a otra lancha';
    END IF;

    IF NEW.horometro_inicial IS DISTINCT FROM OLD.horometro_inicial THEN
        IF EXISTS (SELECT 1 FROM bitacora_lecturas WHERE equipo_id = OLD.id) THEN
            RAISE EXCEPTION 'El horómetro inicial ya no se puede cambiar: hay lecturas en la bitácora';
        END IF;
        NEW.horometro_actual := NEW.horometro_inicial;
    ELSIF NEW.horometro_actual IS DISTINCT FROM OLD.horometro_actual
          AND coalesce(current_setting('app.sync_horometro', true), '') <> 'on' THEN
        RAISE EXCEPTION 'El horómetro es de solo lectura: se actualiza registrando una lectura en la bitácora';
    END IF;
    RETURN NEW;
END $$;

-- Nombre para mostrar: la lancha por su nombre, el generador por su código y nombre
CREATE FUNCTION nombre_unidad(e equipos) RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT CASE WHEN e.tipo = 'GENERADOR' THEN e.codigo || ' · ' || e.nombre
                ELSE (SELECT nombre FROM lanchas WHERE id = e.lancha_id) END
$$;

-- ---------------------------------------------------------------------
-- Tareas: la lancha sale del equipo (las del generador no tienen lancha)
-- ---------------------------------------------------------------------
ALTER TABLE tareas DROP COLUMN lancha_id;     -- arrastra tareas_unica
ALTER TABLE tareas ADD CONSTRAINT tareas_unica UNIQUE (equipo_id, actividad_id);

CREATE OR REPLACE FUNCTION trg_tareas_validar() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_equipo equipos%ROWTYPE;
BEGIN
    SELECT * INTO v_equipo FROM equipos WHERE id = NEW.equipo_id;
    IF TG_OP = 'INSERT' AND NOT v_equipo.activo THEN
        RAISE EXCEPTION '%: está desactivado', nombre_unidad(v_equipo);
    END IF;
    IF NEW.horometro_ultimo_servicio > v_equipo.horometro_actual THEN
        RAISE EXCEPTION '%: el último servicio (% h) no puede ser mayor que el horómetro actual (% h)',
              nombre_unidad(v_equipo), NEW.horometro_ultimo_servicio, v_equipo.horometro_actual;
    END IF;
    IF NEW.fecha_ultimo_servicio > current_date THEN
        RAISE EXCEPTION 'La fecha del último servicio no puede ser futura';
    END IF;
    RETURN NEW;
END $$;

-- ---------------------------------------------------------------------
-- Bitácora: la diaria es de una lancha; la que genera una OT de un
-- generador puede no tener lancha
-- ---------------------------------------------------------------------
ALTER TABLE bitacora ALTER COLUMN lancha_id DROP NOT NULL;
ALTER TABLE bitacora ADD CONSTRAINT bitacora_diaria_con_lancha CHECK (origen = 'OT' OR lancha_id IS NOT NULL);

CREATE OR REPLACE FUNCTION trg_bitacora_proteger() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.fecha > current_date THEN
        RAISE EXCEPTION 'No se permiten bitácoras con fecha futura (%)', to_char(NEW.fecha, 'DD/MM/YYYY');
    END IF;
    IF TG_OP = 'UPDATE' AND (NEW.lancha_id IS DISTINCT FROM OLD.lancha_id OR NEW.fecha <> OLD.fecha) THEN
        RAISE EXCEPTION 'La lancha y la fecha de una bitácora no se modifican: anúlala y regístrala de nuevo';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION trg_lecturas_validar() RETURNS trigger
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
    v_nombre := nombre_unidad(v_equipo);

    -- El horómetro de una lancha solo se registra en la bitácora de esa lancha;
    -- un generador puede leerse en la bitácora de cualquier lancha
    IF v_equipo.tipo = 'PROPULSION' AND v_equipo.lancha_id IS DISTINCT FROM v_bit.lancha_id THEN
        RAISE EXCEPTION '%: su horómetro no corresponde a la lancha de esta bitácora', v_nombre;
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

-- ---------------------------------------------------------------------
-- Ítems de inspección: se revisan en la lancha o en el generador
-- ---------------------------------------------------------------------
ALTER TABLE items_inspeccion ADD COLUMN aplica_a text NOT NULL DEFAULT 'LANCHA'
    CONSTRAINT items_aplica_a_check CHECK (aplica_a IN ('LANCHA', 'GENERADOR'));

-- ---------------------------------------------------------------------
-- Órdenes: pueden ser de una lancha o de un generador
-- ---------------------------------------------------------------------
ALTER TABLE ordenes ALTER COLUMN lancha_id DROP NOT NULL;
UPDATE ordenes o SET lancha_id = NULL
  FROM equipos e
 WHERE e.id = o.equipo_id AND e.tipo = 'GENERADOR' AND o.falla_id IS NULL;
ALTER TABLE ordenes ADD CONSTRAINT ordenes_con_unidad CHECK (lancha_id IS NOT NULL OR equipo_id IS NOT NULL);

CREATE OR REPLACE FUNCTION trg_ordenes_validar() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_tarea   tareas%ROWTYPE;
    v_equipo  equipos%ROWTYPE;
    v_lancha  bigint;
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
    -- Si el equipo es el horómetro de una lancha, la OT es de esa lancha
    IF NEW.equipo_id IS NOT NULL THEN
        SELECT * INTO v_equipo FROM equipos WHERE id = NEW.equipo_id;
        IF v_equipo.tipo = 'PROPULSION' THEN
            IF NEW.lancha_id IS NOT NULL AND NEW.lancha_id <> v_equipo.lancha_id THEN
                RAISE EXCEPTION 'El equipo no corresponde a la lancha de la OT';
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

-- La lectura tomada al ejecutar una OT va a la lancha del horómetro (o sin lancha si es un generador)
CREATE OR REPLACE FUNCTION trg_ordenes_al_ejecutar() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_equipo   equipos%ROWTYPE;
    v_bitacora bigint;
BEGIN
    SELECT * INTO v_equipo FROM equipos WHERE id = NEW.equipo_id;
    IF NEW.horometro_ejecucion > v_equipo.horometro_actual THEN
        INSERT INTO bitacora (lancha_id, fecha, origen, observacion)
        VALUES (v_equipo.lancha_id, NEW.fecha_ejecucion, 'OT', format('Lectura tomada al ejecutar la OT #%s', NEW.id))
        RETURNING id INTO v_bitacora;
        INSERT INTO bitacora_lecturas (bitacora_id, equipo_id, horometro)
        VALUES (v_bitacora, NEW.equipo_id, NEW.horometro_ejecucion);
    END IF;
    RETURN NULL;
END $$;


-- =====================================================================
--  Vista del programa: "unidad" = la lancha o el generador
-- =====================================================================
CREATE VIEW v_estado_tareas AS
WITH base AS (
    SELECT t.id AS tarea_id, e.lancha_id, la.nombre AS lancha, la.codigo AS codigo_lancha,
           t.equipo_id, e.tipo AS tipo_equipo, e.codigo AS codigo_equipo, nombre_unidad(e) AS unidad,
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
      JOIN equipos e     ON e.id = t.equipo_id
      LEFT JOIN lanchas la ON la.id = e.lancha_id
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
--  Procesos
-- =====================================================================

-- Revisa toda la flota (lanchas y generadores). Solo intenta las tareas sin
-- OT pendiente, así no se gastan correlativos.
CREATE FUNCTION generar_ordenes_preventivas()
RETURNS SETOF ordenes
LANGUAGE sql AS $$
    INSERT INTO ordenes (equipo_id, tipo, prioridad, tarea_id, descripcion)
    SELECT v.equipo_id, 'PREVENTIVO', v.severidad, v.tarea_id,
           format('%s%s (programada: %s)', v.actividad,
                  CASE WHEN v.tipo_equipo = 'GENERADOR' THEN ' · ' || v.unidad END,
                  concat_ws(' / ', v.proximo_horometro || ' h', to_char(v.proxima_fecha, 'DD/MM/YYYY')))
      FROM v_estado_tareas v
     WHERE v.nivel >= 1
       AND v.ot_pendiente IS NULL
     ORDER BY v.unidad, v.tarea_id
    ON CONFLICT (tarea_id) WHERE estado IN ('ABIERTA', 'EN_PROCESO', 'EJECUTADA') DO NOTHING
    RETURNING *;
$$;

-- Al final de la transacción que guarda una bitácora. Las fallas de ítems
-- del GENERADOR abren OT para el generador que iba a bordo ese día.
CREATE FUNCTION procesar_bitacora(p_bitacora_id bigint)
RETURNS SETOF ordenes
LANGUAGE plpgsql AS $$
DECLARE
    v_lancha    bigint;
    v_generador bigint;
BEGIN
    SELECT lancha_id INTO v_lancha FROM bitacora WHERE id = p_bitacora_id;
    SELECT l.equipo_id INTO v_generador
      FROM bitacora_lecturas l JOIN equipos e ON e.id = l.equipo_id
     WHERE l.bitacora_id = p_bitacora_id AND e.tipo = 'GENERADOR'
     ORDER BY l.id LIMIT 1;

    RETURN QUERY
    WITH fallas AS (
        SELECT r.id AS falla_id, r.item_id, i.prioridad, i.nombre, r.comentario,
               CASE WHEN i.aplica_a = 'GENERADOR' THEN v_generador END AS equipo_id
          FROM bitacora_respuestas r
          JOIN items_inspeccion i ON i.id = r.item_id
         WHERE r.bitacora_id = p_bitacora_id AND r.resultado = 'FALLA'
           AND NOT EXISTS (SELECT 1 FROM ordenes o WHERE o.falla_id = r.id)
         ORDER BY i.orden
    )
    INSERT INTO ordenes (lancha_id, equipo_id, tipo, prioridad, falla_id, descripcion)
    SELECT v_lancha, f.equipo_id, 'CORRECTIVO', f.prioridad, f.falla_id, format('%s: %s', f.nombre, f.comentario)
      FROM fallas f
     -- No repetir una falla que ya tiene OT pendiente (del mismo generador, o de la misma lancha)
     WHERE NOT EXISTS (
           SELECT 1
             FROM ordenes o
             JOIN bitacora_respuestas r2 ON r2.id = o.falla_id
            WHERE o.estado IN ('ABIERTA', 'EN_PROCESO', 'EJECUTADA')
              AND r2.item_id = f.item_id
              AND CASE WHEN f.equipo_id IS NOT NULL THEN o.equipo_id = f.equipo_id
                       ELSE o.lancha_id = v_lancha AND o.equipo_id IS NULL END)
    RETURNING *;

    RETURN QUERY SELECT * FROM generar_ordenes_preventivas();
END $$;

-- Reemplazada por nombre_unidad()
DROP FUNCTION etiqueta_equipo(equipos);

-- Dónde está cada generador: la lancha de su última lectura en bitácora
CREATE VIEW v_ubicacion_generadores AS
SELECT DISTINCT ON (l.equipo_id) l.equipo_id, b.lancha_id, b.fecha
  FROM bitacora_lecturas l
  JOIN bitacora b ON b.id = l.bitacora_id
  JOIN equipos e  ON e.id = l.equipo_id
 WHERE e.tipo = 'GENERADOR' AND b.lancha_id IS NOT NULL
 ORDER BY l.equipo_id, b.fecha DESC, b.id DESC;
