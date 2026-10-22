-- =====================================================================
--  001 · Mantenimiento de embarcaciones — esquema base
--
--  Regla de diseño:
--   * Lo que debe ser cierto SIEMPRE vive en la base de datos: horómetro
--     sincronizado, lecturas válidas, sin OT duplicadas, el cierre de una
--     OT preventiva reinicia el ciclo de su tarea.
--   * Los PROCESOS (generar órdenes) son funciones que la aplicación llama.
--   * La aplicación indica en cada transacción quién actúa con
--     set_config('app.usuario_id', ...), así los triggers registran autoría.
-- =====================================================================

CREATE FUNCTION usuario_actual() RETURNS bigint
LANGUAGE sql STABLE AS $$
    SELECT nullif(current_setting('app.usuario_id', true), '')::bigint
$$;

-- ---------------------------------------------------------------------
-- Usuarios
-- ---------------------------------------------------------------------
CREATE TABLE usuarios (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre         text NOT NULL,
    usuario        text NOT NULL UNIQUE,
    password_hash  text NOT NULL,
    rol            text NOT NULL CHECK (rol IN ('ADMIN', 'SUPERVISOR', 'MECANICO')),
    activo         boolean NOT NULL DEFAULT true,
    creado_en      timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- Catálogos
-- ---------------------------------------------------------------------
CREATE TABLE categorias (
    id      smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre  text NOT NULL UNIQUE
);

-- ---------------------------------------------------------------------
-- Módulo 1: Lanchas y motores
-- ---------------------------------------------------------------------
CREATE TABLE lanchas (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    codigo_equipo  text NOT NULL UNIQUE,              -- "id equipo" visible (ej. LAN-001)
    nombre         text NOT NULL,
    matricula      text NOT NULL UNIQUE,
    estado         text NOT NULL DEFAULT 'OPERATIVA' CHECK (estado IN ('OPERATIVA', 'INACTIVA')),
    notas          text,
    creado_en      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE motores (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lancha_id          bigint NOT NULL REFERENCES lanchas(id),
    posicion           text NOT NULL,                  -- Babor, Estribor, Central...
    marca              text,
    modelo             text,
    serie              text,
    fecha_alta         date NOT NULL DEFAULT current_date,
    horometro_inicial  numeric(10,1) NOT NULL CHECK (horometro_inicial >= 0),
    horometro_actual   numeric(10,1) NOT NULL,         -- SOLO LECTURA: lo mantiene la bitácora
    activo             boolean NOT NULL DEFAULT true,  -- un motor reemplazado se desactiva
    notas              text
);
CREATE UNIQUE INDEX ux_motores_posicion_activa ON motores (lancha_id, posicion) WHERE activo;

-- ---------------------------------------------------------------------
-- Módulo 2: Entrada de información (catálogo de actividades)
-- ---------------------------------------------------------------------
CREATE TABLE actividades (
    id                        integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,   -- correlativo
    nombre                    text NOT NULL UNIQUE,
    categoria_id              smallint NOT NULL REFERENCES categorias(id),
    severidad                 text NOT NULL CHECK (severidad IN ('BAJA', 'MEDIA', 'ALTA', 'CRITICA')),
    -- Ventana alrededor del punto programado. 10 => con 400 h se hace entre 360 h y 440 h.
    tolerancia_pct            numeric(4,1) NOT NULL DEFAULT 10 CHECK (tolerancia_pct BETWEEN 0 AND 50),
    tiempo_estimado_h         numeric(5,2) CHECK (tiempo_estimado_h > 0),
    intervalo_horas_sugerido  numeric(8,1) CHECK (intervalo_horas_sugerido > 0),  -- precarga la tarea
    intervalo_dias_sugerido   integer CHECK (intervalo_dias_sugerido > 0),
    materiales                text,
    descripcion               text,
    activa                    boolean NOT NULL DEFAULT true
);

-- ---------------------------------------------------------------------
-- Módulo 3: Tareas (el programa de una lancha = sus tareas filtradas)
-- ---------------------------------------------------------------------
CREATE TABLE tareas (
    id                         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lancha_id                  bigint  NOT NULL REFERENCES lanchas(id),
    motor_id                   bigint  REFERENCES motores(id),      -- obligatorio si es por horas
    actividad_id               integer NOT NULL REFERENCES actividades(id),
    intervalo_horas            numeric(8,1) CHECK (intervalo_horas > 0),
    horometro_ultimo_servicio  numeric(10,1),
    intervalo_dias             integer CHECK (intervalo_dias > 0),
    fecha_ultimo_servicio      date,
    proximo_horometro          numeric(10,1)
                               GENERATED ALWAYS AS (horometro_ultimo_servicio + intervalo_horas) STORED,
    proxima_fecha              date
                               GENERATED ALWAYS AS (fecha_ultimo_servicio + intervalo_dias) STORED,
    activa                     boolean NOT NULL DEFAULT true,
    notas                      text,
    creado_en                  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT tareas_unica UNIQUE NULLS NOT DISTINCT (lancha_id, motor_id, actividad_id),
    CONSTRAINT tareas_con_intervalo CHECK (intervalo_horas IS NOT NULL OR intervalo_dias IS NOT NULL),
    CONSTRAINT tareas_horas_requieren_motor CHECK (intervalo_horas IS NULL OR motor_id IS NOT NULL),
    CONSTRAINT tareas_horas_completas CHECK ((intervalo_horas IS NULL) = (horometro_ultimo_servicio IS NULL)),
    CONSTRAINT tareas_dias_completos  CHECK ((intervalo_dias  IS NULL) = (fecha_ultimo_servicio  IS NULL))
);

-- ---------------------------------------------------------------------
-- Módulo 4: Bitácora (lecturas por motor + inspección)
-- ---------------------------------------------------------------------
CREATE TABLE items_inspeccion (
    id            smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre        text NOT NULL UNIQUE,
    categoria_id  smallint NOT NULL REFERENCES categorias(id),
    prioridad     text NOT NULL DEFAULT 'MEDIA' CHECK (prioridad IN ('BAJA', 'MEDIA', 'ALTA', 'CRITICA')),
    orden         smallint NOT NULL DEFAULT 0,       -- posición en el formulario
    activo        boolean  NOT NULL DEFAULT true     -- se desactiva, nunca se borra
);

CREATE TABLE bitacora (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,   -- correlativo
    lancha_id       bigint NOT NULL REFERENCES lanchas(id),
    fecha           date   NOT NULL DEFAULT current_date,
    origen          text   NOT NULL DEFAULT 'DIARIA' CHECK (origen IN ('DIARIA', 'OT')),
    observacion     text,
    registrado_por  bigint REFERENCES usuarios(id) DEFAULT usuario_actual(),
    registrado_en   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_bitacora_lancha_fecha ON bitacora (lancha_id, fecha DESC);
-- Una sola bitácora diaria por lancha y día (evita que dos personas la carguen doble)
CREATE UNIQUE INDEX ux_bitacora_diaria ON bitacora (lancha_id, fecha) WHERE origen = 'DIARIA';

CREATE TABLE bitacora_lecturas (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    bitacora_id  bigint NOT NULL REFERENCES bitacora(id) ON DELETE CASCADE,
    motor_id     bigint NOT NULL REFERENCES motores(id),
    horometro    numeric(10,1) NOT NULL CHECK (horometro >= 0),
    UNIQUE (bitacora_id, motor_id)
);
CREATE INDEX ix_lecturas_motor ON bitacora_lecturas (motor_id);

CREATE TABLE bitacora_respuestas (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    bitacora_id  bigint   NOT NULL REFERENCES bitacora(id) ON DELETE CASCADE,
    item_id      smallint NOT NULL REFERENCES items_inspeccion(id),
    resultado    text     NOT NULL CHECK (resultado IN ('OK', 'FALLA', 'NA')),
    comentario   text,
    UNIQUE (bitacora_id, item_id),
    CONSTRAINT respuestas_falla_con_comentario CHECK (resultado <> 'FALLA' OR comentario IS NOT NULL)
);

-- ---------------------------------------------------------------------
-- Módulo 5: Órdenes de mantenimiento
--   ABIERTA -> EN_PROCESO (mecánico la toma) -> EJECUTADA (mecánico reporta)
--   -> CERRADA (supervisor valida). El supervisor puede devolverla o cancelarla.
-- ---------------------------------------------------------------------
CREATE TABLE ordenes (
    id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,   -- correlativo OT
    lancha_id            bigint NOT NULL REFERENCES lanchas(id),
    motor_id             bigint REFERENCES motores(id),
    tipo                 text   NOT NULL CHECK (tipo IN ('PREVENTIVO', 'CORRECTIVO')),
    estado               text   NOT NULL DEFAULT 'ABIERTA'
                         CHECK (estado IN ('ABIERTA', 'EN_PROCESO', 'EJECUTADA', 'CERRADA', 'CANCELADA')),
    prioridad            text   NOT NULL DEFAULT 'MEDIA' CHECK (prioridad IN ('BAJA', 'MEDIA', 'ALTA', 'CRITICA')),
    tarea_id             bigint REFERENCES tareas(id),               -- origen si es preventiva
    falla_id             bigint REFERENCES bitacora_respuestas(id),  -- origen si es correctiva (ítem que falló)
    descripcion          text   NOT NULL,
    asignado_a           bigint REFERENCES usuarios(id),
    creado_por           bigint REFERENCES usuarios(id) DEFAULT usuario_actual(),
    fecha_apertura       timestamptz NOT NULL DEFAULT now(),
    fecha_inicio         timestamptz,
    -- Datos que reporta el mecánico al ejecutar
    fecha_ejecucion      date,
    horometro_ejecucion  numeric(10,1),
    trabajo_realizado    text,
    materiales_usados    text,
    horas_hombre         numeric(6,2) CHECK (horas_hombre > 0),
    ejecutado_por        bigint REFERENCES usuarios(id),
    -- Cierre / cancelación (supervisor)
    fecha_cierre         timestamptz,
    cerrado_por          bigint REFERENCES usuarios(id),
    CONSTRAINT ordenes_origen_preventiva CHECK (tipo <> 'PREVENTIVO' OR (tarea_id IS NOT NULL AND falla_id IS NULL)),
    CONSTRAINT ordenes_origen_correctiva CHECK (tipo <> 'CORRECTIVO' OR tarea_id IS NULL),
    CONSTRAINT ordenes_ejecucion_completa CHECK (
        estado NOT IN ('EJECUTADA', 'CERRADA')
        OR (fecha_ejecucion IS NOT NULL AND trabajo_realizado IS NOT NULL
            AND (motor_id IS NULL OR horometro_ejecucion IS NOT NULL)))
);
CREATE INDEX ix_ordenes_estado ON ordenes (estado);
CREATE INDEX ix_ordenes_lancha ON ordenes (lancha_id);
-- Nunca dos OT pendientes para la misma tarea, aunque se registre la bitácora 20 veces
CREATE UNIQUE INDEX ux_ordenes_tarea_pendiente
    ON ordenes (tarea_id) WHERE estado IN ('ABIERTA', 'EN_PROCESO', 'EJECUTADA');

CREATE TABLE ordenes_historial (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    orden_id         bigint NOT NULL REFERENCES ordenes(id),
    estado_anterior  text,
    estado_nuevo     text NOT NULL,
    usuario_id       bigint REFERENCES usuarios(id),
    comentario       text,
    fecha            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_historial_orden ON ordenes_historial (orden_id);


-- =====================================================================
--  Triggers: horómetro (por motor)
-- =====================================================================

CREATE FUNCTION recalcular_horometro(p_motor_id bigint) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM set_config('app.sync_horometro', 'on', true);
    UPDATE motores m
       SET horometro_actual = coalesce(
               (SELECT max(l.horometro) FROM bitacora_lecturas l WHERE l.motor_id = m.id),
               m.horometro_inicial)
     WHERE m.id = p_motor_id;
    PERFORM set_config('app.sync_horometro', 'off', true);
END $$;

-- El horómetro del motor no se edita a mano
CREATE FUNCTION trg_motores_proteger() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.horometro_actual := NEW.horometro_inicial;
        RETURN NEW;
    END IF;

    IF NEW.lancha_id <> OLD.lancha_id THEN
        RAISE EXCEPTION 'Un motor no se mueve de lancha: desactívalo y crea uno nuevo en la otra lancha';
    END IF;

    IF NEW.horometro_inicial IS DISTINCT FROM OLD.horometro_inicial THEN
        IF EXISTS (SELECT 1 FROM bitacora_lecturas WHERE motor_id = OLD.id) THEN
            RAISE EXCEPTION 'El horómetro inicial no se puede cambiar: el motor ya tiene lecturas en bitácora';
        END IF;
        NEW.horometro_actual := NEW.horometro_inicial;   -- corrección de un error de captura
    ELSIF NEW.horometro_actual IS DISTINCT FROM OLD.horometro_actual
          AND coalesce(current_setting('app.sync_horometro', true), '') <> 'on' THEN
        RAISE EXCEPTION 'El horómetro es de solo lectura: se actualiza registrando una lectura en la bitácora';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER motores_proteger
    BEFORE INSERT OR UPDATE ON motores
    FOR EACH ROW EXECUTE FUNCTION trg_motores_proteger();

-- La cabecera de bitácora no cambia de lancha ni de fecha (eso invalidaría sus lecturas)
CREATE FUNCTION trg_bitacora_proteger() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.fecha > current_date THEN
        RAISE EXCEPTION 'No se permiten bitácoras con fecha futura (%)', to_char(NEW.fecha, 'DD/MM/YYYY');
    END IF;
    IF TG_OP = 'UPDATE' AND (NEW.lancha_id <> OLD.lancha_id OR NEW.fecha <> OLD.fecha) THEN
        RAISE EXCEPTION 'La lancha y la fecha de una bitácora no se modifican: anúlala y regístrala de nuevo';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER bitacora_proteger
    BEFORE INSERT OR UPDATE ON bitacora
    FOR EACH ROW EXECUTE FUNCTION trg_bitacora_proteger();

-- Validación de lecturas: el motor es de la lancha, nunca hacia atrás, nunca más de 24 h/día
CREATE FUNCTION trg_lecturas_validar() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_motor   motores%ROWTYPE;
    v_bit     bitacora%ROWTYPE;
    v_prev_h  numeric;
    v_prev_f  date;
    v_next_h  numeric;
    v_dias    integer;
BEGIN
    SELECT * INTO v_motor FROM motores  WHERE id = NEW.motor_id;
    SELECT * INTO v_bit   FROM bitacora WHERE id = NEW.bitacora_id;

    IF v_motor.lancha_id <> v_bit.lancha_id THEN
        RAISE EXCEPTION 'El motor % no pertenece a la lancha de la bitácora', v_motor.posicion;
    END IF;
    IF NOT v_motor.activo THEN
        RAISE EXCEPTION 'El motor % está desactivado', v_motor.posicion;
    END IF;
    IF v_bit.fecha < v_motor.fecha_alta THEN
        RAISE EXCEPTION 'La fecha % es anterior al alta del motor % (%)',
              to_char(v_bit.fecha, 'DD/MM/YYYY'), v_motor.posicion, to_char(v_motor.fecha_alta, 'DD/MM/YYYY');
    END IF;

    SELECT l.horometro, b.fecha INTO v_prev_h, v_prev_f
      FROM bitacora_lecturas l JOIN bitacora b ON b.id = l.bitacora_id
     WHERE l.motor_id = NEW.motor_id AND b.fecha <= v_bit.fecha AND l.id <> NEW.id
     ORDER BY b.fecha DESC, l.horometro DESC
     LIMIT 1;
    IF NOT FOUND THEN
        v_prev_h := v_motor.horometro_inicial;
        v_prev_f := v_motor.fecha_alta;
    END IF;

    IF NEW.horometro < v_prev_h THEN
        RAISE EXCEPTION 'Motor %: % h es menor que la lectura anterior (% h del %)',
              v_motor.posicion, NEW.horometro, v_prev_h, to_char(v_prev_f, 'DD/MM/YYYY');
    END IF;

    v_dias := v_bit.fecha - v_prev_f + 1;
    IF NEW.horometro - v_prev_h > 24 * v_dias THEN
        RAISE EXCEPTION 'Motor %: +% h en % día(s) es imposible (máximo 24 h por día). ¿Error de digitación?',
              v_motor.posicion, NEW.horometro - v_prev_h, v_dias;
    END IF;

    -- Lectura atrasada (cargada después): no puede superar a una posterior
    SELECT l.horometro INTO v_next_h
      FROM bitacora_lecturas l JOIN bitacora b ON b.id = l.bitacora_id
     WHERE l.motor_id = NEW.motor_id AND b.fecha > v_bit.fecha AND l.id <> NEW.id
     ORDER BY b.fecha, l.horometro
     LIMIT 1;
    IF FOUND AND NEW.horometro > v_next_h THEN
        RAISE EXCEPTION 'Motor %: % h supera a una lectura posterior (% h)', v_motor.posicion, NEW.horometro, v_next_h;
    END IF;

    RETURN NEW;
END $$;

CREATE TRIGGER lecturas_validar
    BEFORE INSERT OR UPDATE ON bitacora_lecturas
    FOR EACH ROW EXECUTE FUNCTION trg_lecturas_validar();

-- Insertar, corregir o borrar una lectura recalcula el horómetro del motor
CREATE FUNCTION trg_lecturas_sincronizar() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        PERFORM recalcular_horometro(OLD.motor_id);
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        PERFORM recalcular_horometro(NEW.motor_id);
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER lecturas_sincronizar
    AFTER INSERT OR UPDATE OR DELETE ON bitacora_lecturas
    FOR EACH ROW EXECUTE FUNCTION trg_lecturas_sincronizar();

-- Coherencia de tareas
CREATE FUNCTION trg_tareas_validar() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_motor motores%ROWTYPE;
BEGIN
    IF NEW.motor_id IS NOT NULL THEN
        SELECT * INTO v_motor FROM motores WHERE id = NEW.motor_id;
        IF v_motor.lancha_id <> NEW.lancha_id THEN
            RAISE EXCEPTION 'El motor elegido no pertenece a la lancha';
        END IF;
        IF NEW.horometro_ultimo_servicio > v_motor.horometro_actual THEN
            RAISE EXCEPTION 'Motor %: el último servicio (% h) no puede ser mayor que el horómetro actual (% h)',
                  v_motor.posicion, NEW.horometro_ultimo_servicio, v_motor.horometro_actual;
        END IF;
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
--  Triggers: órdenes
-- =====================================================================

CREATE FUNCTION trg_ordenes_validar() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_tarea  tareas%ROWTYPE;
    v_lancha bigint;
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.estado IN ('CERRADA', 'CANCELADA') THEN
        RAISE EXCEPTION 'La OT #% está % y ya no se puede modificar', OLD.id, lower(OLD.estado);
    END IF;

    -- Transiciones permitidas
    IF TG_OP = 'INSERT' AND NEW.estado <> 'ABIERTA' THEN
        RAISE EXCEPTION 'Una OT nueva siempre nace ABIERTA';
    ELSIF TG_OP = 'UPDATE' AND NEW.estado <> OLD.estado
          AND NOT (OLD.estado || '>' || NEW.estado = ANY (ARRAY[
              'ABIERTA>EN_PROCESO', 'ABIERTA>CANCELADA',
              'EN_PROCESO>EJECUTADA', 'EN_PROCESO>ABIERTA', 'EN_PROCESO>CANCELADA',
              'EJECUTADA>CERRADA', 'EJECUTADA>EN_PROCESO', 'EJECUTADA>CANCELADA'])) THEN
        RAISE EXCEPTION 'La OT #% no puede pasar de % a %', NEW.id, OLD.estado, NEW.estado;
    END IF;

    -- La OT hereda lancha y motor de su origen
    IF NEW.tarea_id IS NOT NULL THEN
        SELECT * INTO v_tarea FROM tareas WHERE id = NEW.tarea_id;
        NEW.lancha_id := v_tarea.lancha_id;
        NEW.motor_id  := v_tarea.motor_id;
    ELSIF NEW.falla_id IS NOT NULL THEN
        SELECT b.lancha_id INTO v_lancha
          FROM bitacora_respuestas r JOIN bitacora b ON b.id = r.bitacora_id
         WHERE r.id = NEW.falla_id;
        IF v_lancha <> NEW.lancha_id THEN
            RAISE EXCEPTION 'La falla de origen es de otra lancha';
        END IF;
    END IF;
    IF NEW.motor_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM motores WHERE id = NEW.motor_id AND lancha_id = NEW.lancha_id) THEN
        RAISE EXCEPTION 'El motor no pertenece a la lancha de la OT';
    END IF;

    -- Sellos de tiempo y autoría según el estado
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

CREATE TRIGGER ordenes_validar
    BEFORE INSERT OR UPDATE ON ordenes
    FOR EACH ROW EXECUTE FUNCTION trg_ordenes_validar();

-- Al EJECUTARSE: si el trabajo se hizo con un horómetro mayor al último
-- registrado, esa lectura entra a la bitácora (una sola fuente de horómetro).
CREATE FUNCTION trg_ordenes_al_ejecutar() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_bitacora bigint;
BEGIN
    IF NEW.motor_id IS NOT NULL
       AND NEW.horometro_ejecucion > (SELECT horometro_actual FROM motores WHERE id = NEW.motor_id) THEN
        INSERT INTO bitacora (lancha_id, fecha, origen, observacion)
        VALUES (NEW.lancha_id, NEW.fecha_ejecucion, 'OT', format('Lectura tomada al ejecutar la OT #%s', NEW.id))
        RETURNING id INTO v_bitacora;
        INSERT INTO bitacora_lecturas (bitacora_id, motor_id, horometro)
        VALUES (v_bitacora, NEW.motor_id, NEW.horometro_ejecucion);
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER ordenes_al_ejecutar
    AFTER UPDATE OF estado ON ordenes
    FOR EACH ROW WHEN (NEW.estado = 'EJECUTADA' AND OLD.estado <> 'EJECUTADA')
    EXECUTE FUNCTION trg_ordenes_al_ejecutar();

-- Al CERRARSE una preventiva: se reinicia el ciclo de su tarea (la retroalimentación)
CREATE FUNCTION trg_ordenes_al_cerrar() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.tipo = 'PREVENTIVO' THEN
        UPDATE tareas
           SET horometro_ultimo_servicio = CASE WHEN intervalo_horas IS NOT NULL THEN NEW.horometro_ejecucion END,
               fecha_ultimo_servicio     = CASE WHEN intervalo_dias  IS NOT NULL THEN NEW.fecha_ejecucion END
         WHERE id = NEW.tarea_id;
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER ordenes_al_cerrar
    AFTER UPDATE OF estado ON ordenes
    FOR EACH ROW WHEN (NEW.estado = 'CERRADA' AND OLD.estado <> 'CERRADA')
    EXECUTE FUNCTION trg_ordenes_al_cerrar();

-- Historial de estados (quién y cuándo). El comentario llega por app.comentario.
CREATE FUNCTION trg_ordenes_historial() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO ordenes_historial (orden_id, estado_nuevo, usuario_id, comentario)
        VALUES (NEW.id, NEW.estado, usuario_actual(), 'OT creada');
    ELSIF NEW.estado <> OLD.estado THEN
        INSERT INTO ordenes_historial (orden_id, estado_anterior, estado_nuevo, usuario_id, comentario)
        VALUES (NEW.id, OLD.estado, NEW.estado, usuario_actual(),
                nullif(current_setting('app.comentario', true), ''));
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER ordenes_historial
    AFTER INSERT OR UPDATE OF estado ON ordenes
    FOR EACH ROW EXECUTE FUNCTION trg_ordenes_historial();


-- =====================================================================
--  Vistas
-- =====================================================================

-- Uso promedio de cada motor en los últimos 30 días (para pronosticar fechas)
CREATE VIEW v_uso_motores AS
SELECT l.motor_id,
       round((max(l.horometro) - min(l.horometro)) / nullif(max(b.fecha) - min(b.fecha), 0), 1) AS horas_por_dia
  FROM bitacora_lecturas l
  JOIN bitacora b ON b.id = l.bitacora_id
 WHERE b.fecha >= current_date - 30
 GROUP BY l.motor_id;

-- Semáforo del programa de mantenimiento (se calcula, no se guarda)
--   nivel 0 AL DIA · 1 PROXIMA (entró en la ventana: se genera la OT)
--   nivel 2 VENCIDA (pasó el punto, dentro de tolerancia) · 3 FUERA DE TOLERANCIA
CREATE VIEW v_estado_tareas AS
WITH base AS (
    SELECT t.id AS tarea_id, t.lancha_id, la.nombre AS lancha, la.codigo_equipo,
           t.motor_id, m.posicion AS motor,
           t.actividad_id, a.nombre AS actividad, a.categoria_id, c.nombre AS categoria, a.severidad,
           t.intervalo_horas, t.horometro_ultimo_servicio, t.proximo_horometro,
           m.horometro_actual,
           t.proximo_horometro - m.horometro_actual        AS horas_restantes,
           t.intervalo_horas * a.tolerancia_pct / 100      AS ventana_horas,
           t.intervalo_dias, t.fecha_ultimo_servicio, t.proxima_fecha,
           t.proxima_fecha - current_date                  AS dias_restantes,
           ceil(t.intervalo_dias * a.tolerancia_pct / 100) AS ventana_dias,
           u.horas_por_dia,
           t.activa AND coalesce(m.activo, true)           AS activa,
           t.notas
      FROM tareas t
      JOIN lanchas la    ON la.id = t.lancha_id
      JOIN actividades a ON a.id = t.actividad_id
      JOIN categorias c  ON c.id = a.categoria_id
      LEFT JOIN motores m       ON m.id = t.motor_id
      LEFT JOIN v_uso_motores u ON u.motor_id = t.motor_id
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
--  Procesos que invoca la aplicación
-- =====================================================================

-- OT preventiva para cada tarea que entró en su ventana y no tiene OT pendiente.
-- Sin parámetro revisa toda la flota: es el job periódico (las tareas por
-- calendario vencen aunque nadie registre lecturas).
CREATE FUNCTION generar_ordenes_preventivas(p_lancha_id bigint DEFAULT NULL)
RETURNS SETOF ordenes
LANGUAGE sql AS $$
    INSERT INTO ordenes (lancha_id, motor_id, tipo, prioridad, tarea_id, descripcion)
    SELECT v.lancha_id, v.motor_id, 'PREVENTIVO', v.severidad, v.tarea_id,
           format('%s%s (programada: %s)', v.actividad, ' · motor ' || v.motor,
                  concat_ws(' / ', v.proximo_horometro || ' h', to_char(v.proxima_fecha, 'DD/MM/YYYY')))
      FROM v_estado_tareas v
     WHERE v.nivel >= 1
       AND (p_lancha_id IS NULL OR v.lancha_id = p_lancha_id)
    ON CONFLICT (tarea_id) WHERE estado IN ('ABIERTA', 'EN_PROCESO', 'EJECUTADA') DO NOTHING
    RETURNING *;
$$;

-- Se llama al final de la transacción que guarda una bitácora con sus lecturas
-- y respuestas. No es un trigger: cuando se inserta la cabecera las respuestas
-- aún no existen, y una importación histórica no debe abrir órdenes.
CREATE FUNCTION procesar_bitacora(p_bitacora_id bigint)
RETURNS SETOF ordenes
LANGUAGE plpgsql AS $$
DECLARE
    v_lancha bigint;
BEGIN
    SELECT lancha_id INTO v_lancha FROM bitacora WHERE id = p_bitacora_id;

    -- Correctivas: una OT por ítem en FALLA, salvo que ese ítem ya tenga una OT pendiente
    RETURN QUERY
    INSERT INTO ordenes (lancha_id, tipo, prioridad, falla_id, descripcion)
    SELECT v_lancha, 'CORRECTIVO', i.prioridad, r.id, format('%s: %s', i.nombre, r.comentario)
      FROM bitacora_respuestas r
      JOIN items_inspeccion i ON i.id = r.item_id
     WHERE r.bitacora_id = p_bitacora_id
       AND r.resultado = 'FALLA'
       AND NOT EXISTS (SELECT 1 FROM ordenes o WHERE o.falla_id = r.id)
       AND NOT EXISTS (
           SELECT 1
             FROM ordenes o
             JOIN bitacora_respuestas r2 ON r2.id = o.falla_id
            WHERE o.lancha_id = v_lancha
              AND o.estado IN ('ABIERTA', 'EN_PROCESO', 'EJECUTADA')
              AND r2.item_id = r.item_id)
     ORDER BY i.orden
    RETURNING *;

    -- Preventivas: los nuevos horómetros pudieron meter tareas en su ventana
    RETURN QUERY SELECT * FROM generar_ordenes_preventivas(v_lancha);
END $$;
