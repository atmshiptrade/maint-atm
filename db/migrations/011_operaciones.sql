-- =====================================================================
--  011 · Módulo Operaciones: Reporte de Viaje (ATM-RG-OP-002 rev. 04)
--
--  Una lancha de ATM presta un servicio a un buque cliente. El patrón
--  registra el trayecto con botones (la hora la pone el sistema) y el
--  supervisor de operaciones cierra el reporte. Reglas:
--    * Código PLC-0001 correlativo sin huecos (P.L.C. = Puerto La Cruz).
--    * BORRADOR → EN_CURSO (primer evento) → FINALIZADO → CERRADO; CANCELADO.
--    * Una lancha o un patrón no tienen dos viajes en curso; una lancha
--      inactiva no se asigna.
--    * El origen de un tramo no se guarda: es el destino del anterior (el
--      primero sale del origen del viaje), así que siempre coinciden.
--    * El tiempo de espera se calcula de las esperas, nunca se digita.
--    * Cerrado, el reporte solo cambia por enmienda con motivo, que queda
--      registrada con su autor y el antes y después.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Catálogos (Data → Operaciones). Clientes y buques los reutilizará Comercial.
-- ---------------------------------------------------------------------
CREATE TABLE clientes (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre      text NOT NULL UNIQUE,
    rif         text UNIQUE,
    contacto    text,
    telefono    text,
    correo      text,
    notas       text,
    activo      boolean NOT NULL DEFAULT true,
    creado_en   timestamptz NOT NULL DEFAULT now()
);

-- IMO: 7 dígitos; el último es verificador (suma de los 6 primeros por 7..2, módulo 10)
CREATE FUNCTION imo_valido(imo text) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN imo ~ '^[0-9]{7}$' THEN
        (SELECT sum(substr(imo, i, 1)::int * (8 - i)) FROM generate_series(1, 6) AS i) % 10 = substr(imo, 7, 1)::int
    ELSE false END
$$;

CREATE TABLE buques (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre        text NOT NULL,
    imo           text UNIQUE CONSTRAINT buques_imo_valido CHECK (imo IS NULL OR imo_valido(imo)),
    bandera       text,
    tipo          text,
    arqueo_bruto  numeric(10,1) CONSTRAINT buques_arqueo_positivo CHECK (arqueo_bruto > 0),
    eslora        numeric(7,2) CONSTRAINT buques_eslora_positiva CHECK (eslora > 0),
    manga         numeric(7,2) CONSTRAINT buques_manga_positiva CHECK (manga > 0),
    notas         text,
    activo        boolean NOT NULL DEFAULT true,
    creado_en     timestamptz NOT NULL DEFAULT now()
);
-- Sin IMO, al menos que no se repita el nombre
CREATE UNIQUE INDEX ux_buques_nombre_sin_imo ON buques (lower(nombre)) WHERE imo IS NULL;

-- Áreas (bahías y zonas) y muelles: la misma lista para el área del servicio y el trayecto
CREATE TABLE ubicaciones (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre      text NOT NULL UNIQUE,
    tipo        text NOT NULL CHECK (tipo IN ('AREA', 'MUELLE')),
    activa      boolean NOT NULL DEFAULT true,
    creado_en   timestamptz NOT NULL DEFAULT now()
);
INSERT INTO ubicaciones (nombre, tipo) VALUES
    ('Bahía de Barcelona (Jose)', 'AREA'),
    ('Bahía de Barcelona (Isletas de Píritu)', 'AREA'),
    ('Zona de bunkers', 'AREA'),
    ('Bahía de Pozuelos', 'AREA'),
    ('Bahía de Guanta', 'AREA'),
    ('Bahía de Pertigalete', 'AREA');

CREATE TABLE tipos_servicio (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre      text NOT NULL UNIQUE,
    activo      boolean NOT NULL DEFAULT true,
    creado_en   timestamptz NOT NULL DEFAULT now()
);
INSERT INTO tipos_servicio (nombre) VALUES
    ('Traslado de pasajeros'), ('Traslado de materiales'), ('Traslado de documentos');

-- Marinos de la tripulación: se eligen en el reporte, no entran al sistema
CREATE TABLE marinos (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre      text NOT NULL,
    cedula      text UNIQUE,
    telefono    text,
    activo      boolean NOT NULL DEFAULT true,
    creado_en   timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- Viajes (Reporte de Viaje)
-- ---------------------------------------------------------------------
CREATE TABLE viajes (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    numero              integer NOT NULL CONSTRAINT viajes_numero_key UNIQUE,
    codigo              text GENERATED ALWAYS AS (
                            'PLC-' || CASE WHEN numero < 10000 THEN lpad(numero::text, 4, '0') ELSE numero::text END
                        ) STORED,
    fecha               date NOT NULL,
    lancha_id           bigint NOT NULL REFERENCES lanchas(id),
    patron_id           bigint NOT NULL REFERENCES usuarios(id),
    estado              text NOT NULL DEFAULT 'BORRADOR'
                        CHECK (estado IN ('BORRADOR', 'EN_CURSO', 'FINALIZADO', 'CERRADO', 'CANCELADO')),
    cliente_id          bigint REFERENCES clientes(id),          -- «Al servicio de»
    buque_id            bigint REFERENCES buques(id),
    requisicion         text,                                    -- N° de requisición comercial
    solicitado_por      text,
    servicio_otro       text,                                    -- «Otros» del tipo de servicio
    descripcion         text,                                    -- descripción del servicio
    -- Punto de partida del primer tramo: una ubicación o a bordo del buque
    origen_id           bigint REFERENCES ubicaciones(id),
    origen_buque        boolean NOT NULL DEFAULT false,
    motivo_cancelacion  text,
    creado_por          bigint REFERENCES usuarios(id),
    creado_en           timestamptz NOT NULL DEFAULT now(),
    finalizado_en       timestamptz,
    cerrado_por         bigint REFERENCES usuarios(id),
    cerrado_en          timestamptz,
    CONSTRAINT viajes_origen_unico CHECK (NOT (origen_id IS NOT NULL AND origen_buque)),
    CONSTRAINT viajes_cancelado_con_motivo CHECK (estado <> 'CANCELADO' OR nullif(trim(motivo_cancelacion), '') IS NOT NULL)
);
CREATE INDEX ix_viajes_fecha ON viajes (fecha);
CREATE INDEX ix_viajes_lancha ON viajes (lancha_id);
CREATE UNIQUE INDEX ux_viajes_lancha_en_curso ON viajes (lancha_id) WHERE estado = 'EN_CURSO';
CREATE UNIQUE INDEX ux_viajes_patron_en_curso ON viajes (patron_id) WHERE estado = 'EN_CURSO';

CREATE TABLE viajes_servicios (
    viaje_id          bigint NOT NULL REFERENCES viajes(id) ON DELETE CASCADE,
    tipo_servicio_id  bigint NOT NULL REFERENCES tipos_servicio(id),
    PRIMARY KEY (viaje_id, tipo_servicio_id)
);
CREATE TABLE viajes_areas (
    viaje_id      bigint NOT NULL REFERENCES viajes(id) ON DELETE CASCADE,
    ubicacion_id  bigint NOT NULL REFERENCES ubicaciones(id),
    PRIMARY KEY (viaje_id, ubicacion_id)
);
CREATE TABLE viajes_marinos (
    viaje_id   bigint NOT NULL REFERENCES viajes(id) ON DELETE CASCADE,
    marino_id  bigint NOT NULL REFERENCES marinos(id),
    PRIMARY KEY (viaje_id, marino_id)
);

-- Un tramo: zarpe y llegada. El destino puede anotarse al zarpar (rumbo) y se confirma al llegar.
CREATE TABLE viajes_tramos (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    viaje_id        bigint NOT NULL REFERENCES viajes(id) ON DELETE CASCADE,
    salida          timestamptz NOT NULL,
    destino_id      bigint REFERENCES ubicaciones(id),
    destino_buque   boolean NOT NULL DEFAULT false,
    llegada         timestamptz,
    registrado_por  bigint REFERENCES usuarios(id),
    registrado_en   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT tramos_destino_unico CHECK (NOT (destino_id IS NOT NULL AND destino_buque)),
    CONSTRAINT tramos_llegada_con_destino CHECK (llegada IS NULL OR destino_id IS NOT NULL OR destino_buque),
    CONSTRAINT tramos_llegada_despues_de_salida CHECK (llegada IS NULL OR llegada >= salida)
);
CREATE INDEX ix_viajes_tramos_viaje ON viajes_tramos (viaje_id, salida);
CREATE UNIQUE INDEX ux_tramos_navegando ON viajes_tramos (viaje_id) WHERE llegada IS NULL;

CREATE TABLE viajes_esperas (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    viaje_id        bigint NOT NULL REFERENCES viajes(id) ON DELETE CASCADE,
    inicio          timestamptz NOT NULL,
    fin             timestamptz,
    motivo          text,
    registrado_por  bigint REFERENCES usuarios(id),
    registrado_en   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT esperas_fin_despues_de_inicio CHECK (fin IS NULL OR fin >= inicio)
);
CREATE INDEX ix_viajes_esperas_viaje ON viajes_esperas (viaje_id, inicio);
CREATE UNIQUE INDEX ux_esperas_en_curso ON viajes_esperas (viaje_id) WHERE fin IS NULL;

-- Cada cambio hecho a un reporte cerrado: quién, cuándo, por qué y qué cambió
CREATE TABLE viajes_enmiendas (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    viaje_id    bigint NOT NULL REFERENCES viajes(id) ON DELETE CASCADE,
    usuario_id  bigint REFERENCES usuarios(id),
    fecha       timestamptz NOT NULL DEFAULT now(),
    motivo      text NOT NULL,
    tabla       text NOT NULL,
    accion      text NOT NULL CHECK (accion IN ('INSERT', 'UPDATE', 'DELETE')),
    antes       jsonb,
    despues     jsonb
);
CREATE INDEX ix_viajes_enmiendas_viaje ON viajes_enmiendas (viaje_id, fecha);

-- ---------------------------------------------------------------------
-- Correlativo sin huecos
-- ---------------------------------------------------------------------
CREATE FUNCTION trg_viajes_numero() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        PERFORM pg_advisory_xact_lock(hashtext('viajes.numero'));
        NEW.numero := coalesce((SELECT max(numero) FROM viajes), 0) + 1;
    ELSIF NEW.numero <> OLD.numero THEN
        RAISE EXCEPTION 'El código del reporte no se puede cambiar';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER viajes_numero
    BEFORE INSERT OR UPDATE OF numero ON viajes
    FOR EACH ROW EXECUTE FUNCTION trg_viajes_numero();

-- ---------------------------------------------------------------------
-- Datos que Operaciones toma de Mantenimiento por debajo (sin mostrar sus módulos)
-- ---------------------------------------------------------------------
CREATE FUNCTION lancha_falla_critica(p_lancha bigint) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT EXISTS (SELECT 1 FROM ordenes
                    WHERE lancha_id = p_lancha AND prioridad = 'CRITICA' AND estado IN ('ABIERTA', 'EN_PROCESO'))
$$;

-- ---------------------------------------------------------------------
-- Trayecto: orden cronológico, una sola cosa a la vez
-- ---------------------------------------------------------------------
CREATE FUNCTION validar_trayecto(p_viaje bigint) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_viaje   viajes;
    v_tramo   record;
    v_previo  record;
    v_hay     boolean := false;
BEGIN
    SELECT * INTO v_viaje FROM viajes WHERE id = p_viaje;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    FOR v_tramo IN SELECT * FROM viajes_tramos WHERE viaje_id = p_viaje ORDER BY salida, id LOOP
        IF v_hay THEN
            IF v_previo.llegada IS NULL THEN
                RAISE EXCEPTION 'Hay un tramo sin llegada (zarpe de las %) antes de otro zarpe',
                      to_char(v_previo.salida, 'HH24:MI');
            END IF;
            IF v_tramo.salida < v_previo.llegada THEN
                RAISE EXCEPTION 'El zarpe del % es anterior a la llegada del tramo anterior (%)',
                      to_char(v_tramo.salida, 'DD/MM HH24:MI'), to_char(v_previo.llegada, 'DD/MM HH24:MI');
            END IF;
        END IF;
        IF v_tramo.destino_buque AND v_viaje.buque_id IS NULL THEN
            RAISE EXCEPTION 'Para usar «a bordo del buque», indica primero el buque del servicio';
        END IF;
        v_previo := v_tramo;
        v_hay := true;
    END LOOP;

    IF v_hay AND v_viaje.origen_id IS NULL AND NOT v_viaje.origen_buque THEN
        RAISE EXCEPTION 'Indica de dónde zarpó la lancha en el primer tramo';
    END IF;
    IF v_viaje.origen_buque AND v_viaje.buque_id IS NULL THEN
        RAISE EXCEPTION 'Para usar «a bordo del buque», indica primero el buque del servicio';
    END IF;

    IF EXISTS (SELECT 1 FROM viajes_esperas a JOIN viajes_esperas b ON b.viaje_id = a.viaje_id AND b.id > a.id
                WHERE a.viaje_id = p_viaje
                  AND tstzrange(a.inicio, coalesce(a.fin, 'infinity')) && tstzrange(b.inicio, coalesce(b.fin, 'infinity'))) THEN
        RAISE EXCEPTION 'Dos esperas se superponen en el tiempo';
    END IF;
    IF EXISTS (SELECT 1 FROM viajes_esperas e JOIN viajes_tramos t ON t.viaje_id = e.viaje_id
                WHERE e.viaje_id = p_viaje
                  AND tstzrange(e.inicio, coalesce(e.fin, 'infinity')) && tstzrange(t.salida, coalesce(t.llegada, 'infinity'))) THEN
        RAISE EXCEPTION 'Una espera coincide con un tramo navegando: la espera se registra con la lancha detenida';
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- Reporte cerrado: solo cambia por enmienda (app.enmienda = motivo)
-- ---------------------------------------------------------------------
CREATE FUNCTION motivo_enmienda() RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT nullif(trim(current_setting('app.enmienda', true)), '')
$$;

-- Guardián de las tablas hijas: reporte cancelado o cerrado sin enmienda no se toca
CREATE FUNCTION trg_viajes_hijas_proteger() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_viaje     viajes;
    v_viaje_id  bigint;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_viaje_id := OLD.viaje_id;
    ELSE
        v_viaje_id := NEW.viaje_id;
    END IF;
    SELECT * INTO v_viaje FROM viajes WHERE id = v_viaje_id;
    IF v_viaje.estado = 'CANCELADO' THEN
        RAISE EXCEPTION 'El reporte % está cancelado y ya no se modifica', v_viaje.codigo;
    END IF;
    IF v_viaje.estado = 'CERRADO' AND motivo_enmienda() IS NULL THEN
        RAISE EXCEPTION 'El reporte % está cerrado: para corregirlo usa «Enmendar» e indica el motivo', v_viaje.codigo;
    END IF;
    -- Revisando o corrigiendo un reporte ya terminado, no hay navegación ni esperas abiertas
    IF TG_OP <> 'DELETE' AND v_viaje.estado IN ('FINALIZADO', 'CERRADO') THEN
        -- (condiciones anidadas: cada tabla tiene sus propias columnas)
        IF TG_TABLE_NAME = 'viajes_tramos' THEN
            IF NEW.llegada IS NULL THEN
                RAISE EXCEPTION 'El viaje ya terminó: el tramo necesita su hora de llegada';
            END IF;
        ELSIF TG_TABLE_NAME = 'viajes_esperas' THEN
            IF NEW.fin IS NULL THEN
                RAISE EXCEPTION 'El viaje ya terminó: la espera necesita su hora de fin';
            END IF;
        END IF;
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF TG_TABLE_NAME IN ('viajes_tramos', 'viajes_esperas') THEN
            NEW.registrado_por := coalesce(NEW.registrado_por, usuario_actual());
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END $$;

-- Registro de enmiendas: solo lo que cambió
CREATE FUNCTION trg_viajes_registrar_enmienda() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_viaje_id  bigint;
    v_antes     jsonb := CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END;
    v_despues   jsonb := CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END;
BEGIN
    IF TG_TABLE_NAME = 'viajes' THEN
        IF OLD.estado <> 'CERRADO' THEN
            RETURN NULL;
        END IF;
        v_viaje_id := OLD.id;
    ELSE
        v_viaje_id := coalesce(v_despues, v_antes) ->> 'viaje_id';
        IF (SELECT estado FROM viajes WHERE id = v_viaje_id) IS DISTINCT FROM 'CERRADO' THEN
            RETURN NULL;
        END IF;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        SELECT jsonb_object_agg(a.key, a.value), jsonb_object_agg(a.key, d.value)
          INTO v_antes, v_despues
          FROM jsonb_each(v_antes) a JOIN jsonb_each(v_despues) d USING (key)
         WHERE a.value IS DISTINCT FROM d.value AND a.key NOT IN ('registrado_en');
        IF v_antes IS NULL THEN
            RETURN NULL;
        END IF;
    END IF;
    INSERT INTO viajes_enmiendas (viaje_id, usuario_id, motivo, tabla, accion, antes, despues)
    VALUES (v_viaje_id, usuario_actual(), motivo_enmienda(), TG_TABLE_NAME, TG_OP, v_antes, v_despues);
    RETURN NULL;
END $$;

-- Un tramo o una espera nueva pone el viaje en curso; cualquier cambio revalida el trayecto
CREATE FUNCTION trg_viajes_trayecto_cambio() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_viaje_id bigint;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_viaje_id := OLD.viaje_id;
    ELSE
        v_viaje_id := NEW.viaje_id;
    END IF;
    IF TG_OP = 'INSERT' THEN
        UPDATE viajes SET estado = 'EN_CURSO' WHERE id = v_viaje_id AND estado = 'BORRADOR';
    END IF;
    PERFORM validar_trayecto(v_viaje_id);
    RETURN NULL;
END $$;

DO $$
DECLARE
    v_tabla text;
BEGIN
    FOREACH v_tabla IN ARRAY ARRAY['viajes_tramos', 'viajes_esperas', 'viajes_servicios', 'viajes_areas', 'viajes_marinos'] LOOP
        EXECUTE format('CREATE TRIGGER %1$s_proteger BEFORE INSERT OR UPDATE OR DELETE ON %1$s
                        FOR EACH ROW EXECUTE FUNCTION trg_viajes_hijas_proteger()', v_tabla);
        EXECUTE format('CREATE TRIGGER %1$s_enmienda AFTER INSERT OR UPDATE OR DELETE ON %1$s
                        FOR EACH ROW EXECUTE FUNCTION trg_viajes_registrar_enmienda()', v_tabla);
    END LOOP;
    FOREACH v_tabla IN ARRAY ARRAY['viajes_tramos', 'viajes_esperas'] LOOP
        EXECUTE format('CREATE TRIGGER %1$s_cambio AFTER INSERT OR UPDATE OR DELETE ON %1$s
                        FOR EACH ROW EXECUTE FUNCTION trg_viajes_trayecto_cambio()', v_tabla);
    END LOOP;
END $$;

-- ---------------------------------------------------------------------
-- El viaje: estados, asignación y requisitos de cada paso
-- ---------------------------------------------------------------------
CREATE FUNCTION trg_viajes_validar() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_lancha          lanchas;
    v_faltan          text[] := '{}';
    v_revisar_lancha  boolean;
    v_revisar_patron  boolean;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.estado <> 'BORRADOR' THEN
            RAISE EXCEPTION 'Un reporte de viaje nuevo siempre nace en borrador';
        END IF;
        NEW.creado_por := coalesce(NEW.creado_por, usuario_actual());
    ELSE
        IF OLD.estado = 'CANCELADO' THEN
            RAISE EXCEPTION 'El reporte % está cancelado y ya no se modifica', OLD.codigo;
        END IF;
        IF OLD.estado = 'CERRADO' THEN
            IF NEW.estado <> 'CERRADO' THEN
                RAISE EXCEPTION 'Un reporte cerrado no cambia de estado';
            ELSIF motivo_enmienda() IS NULL THEN
                RAISE EXCEPTION 'El reporte % está cerrado: para corregirlo usa «Enmendar» e indica el motivo', OLD.codigo;
            END IF;
        END IF;
        IF NEW.estado <> OLD.estado AND NOT (OLD.estado, NEW.estado) IN (
                ('BORRADOR', 'EN_CURSO'), ('BORRADOR', 'CANCELADO'), ('EN_CURSO', 'FINALIZADO'),
                ('EN_CURSO', 'CANCELADO'), ('FINALIZADO', 'EN_CURSO'), ('FINALIZADO', 'CERRADO')) THEN
            RAISE EXCEPTION 'El reporte % no puede pasar de % a %', OLD.codigo, lower(OLD.estado), lower(NEW.estado);
        END IF;
        -- Una vez que zarpó, la lancha y el patrón quedan fijos (salvo enmienda)
        IF OLD.estado IN ('EN_CURSO', 'FINALIZADO')
           AND (NEW.lancha_id <> OLD.lancha_id OR NEW.patron_id <> OLD.patron_id) THEN
            RAISE EXCEPTION 'La lancha y el patrón solo se cambian antes del primer zarpe';
        END IF;
    END IF;

    -- Asignación: lancha operativa y patrón con rol en Operaciones (una enmienda corrige la historia sin revisarlo)
    IF TG_OP = 'INSERT' THEN
        v_revisar_lancha := true;
        v_revisar_patron := true;
    ELSE
        v_revisar_lancha := OLD.estado <> 'CERRADO'
                            AND (NEW.lancha_id <> OLD.lancha_id OR (OLD.estado = 'BORRADOR' AND NEW.estado = 'EN_CURSO'));
        v_revisar_patron := OLD.estado <> 'CERRADO' AND NEW.patron_id <> OLD.patron_id;
    END IF;
    IF v_revisar_lancha THEN
        SELECT * INTO v_lancha FROM lanchas WHERE id = NEW.lancha_id;
        IF v_lancha.estado <> 'OPERATIVA' THEN
            RAISE EXCEPTION 'La lancha % está inactiva: no se le pueden asignar viajes', v_lancha.nombre;
        END IF;
    END IF;
    IF v_revisar_patron AND NOT EXISTS (
            SELECT 1 FROM usuarios u JOIN usuarios_roles r ON r.usuario_id = u.id AND r.modulo = 'OPERACIONES'
             WHERE u.id = NEW.patron_id AND u.activo) THEN
        RAISE EXCEPTION 'El patrón debe ser un usuario activo de Operaciones';
    END IF;

    IF TG_OP = 'UPDATE' AND NEW.estado <> OLD.estado THEN
        IF NEW.estado = 'FINALIZADO' THEN
            IF NOT EXISTS (SELECT 1 FROM viajes_tramos WHERE viaje_id = NEW.id) THEN
                RAISE EXCEPTION 'Para finalizar el viaje, registra al menos un zarpe y su llegada';
            ELSIF EXISTS (SELECT 1 FROM viajes_tramos WHERE viaje_id = NEW.id AND llegada IS NULL) THEN
                RAISE EXCEPTION 'La lancha sigue navegando: registra la llegada antes de finalizar';
            ELSIF EXISTS (SELECT 1 FROM viajes_esperas WHERE viaje_id = NEW.id AND fin IS NULL) THEN
                RAISE EXCEPTION 'Hay una espera en curso: registra su fin antes de finalizar';
            END IF;
            NEW.finalizado_en := now();
        ELSIF NEW.estado = 'EN_CURSO' AND OLD.estado = 'FINALIZADO' THEN
            NEW.finalizado_en := NULL;
        ELSIF NEW.estado = 'CERRADO' THEN
            IF NEW.cliente_id IS NULL THEN
                v_faltan := v_faltan || 'a quién se prestó el servicio (cliente)'::text;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM viajes_servicios WHERE viaje_id = NEW.id)
               AND nullif(trim(NEW.servicio_otro), '') IS NULL THEN
                v_faltan := v_faltan || 'el tipo de servicio'::text;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM viajes_areas WHERE viaje_id = NEW.id) THEN
                v_faltan := v_faltan || 'el área donde se ejecutó'::text;
            END IF;
            IF nullif(trim(NEW.descripcion), '') IS NULL THEN
                v_faltan := v_faltan || 'la descripción del servicio'::text;
            END IF;
            IF cardinality(v_faltan) > 0 THEN
                RAISE EXCEPTION 'Para cerrar el reporte falta: %', array_to_string(v_faltan, ', ');
            END IF;
            NEW.cerrado_en := now();
            NEW.cerrado_por := usuario_actual();
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER viajes_validar
    BEFORE INSERT OR UPDATE ON viajes
    FOR EACH ROW EXECUTE FUNCTION trg_viajes_validar();

CREATE TRIGGER viajes_enmienda
    AFTER UPDATE ON viajes
    FOR EACH ROW EXECUTE FUNCTION trg_viajes_registrar_enmienda();

-- Cambiar el buque o el origen revalida los puntos «a bordo del buque»
CREATE FUNCTION trg_viajes_revalidar() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM validar_trayecto(NEW.id);
    RETURN NULL;
END $$;

CREATE TRIGGER viajes_revalidar
    AFTER UPDATE OF buque_id, origen_id, origen_buque ON viajes
    FOR EACH ROW EXECUTE FUNCTION trg_viajes_revalidar();

-- ---------------------------------------------------------------------
-- Resumen de cada viaje para listas y tablero
-- ---------------------------------------------------------------------
CREATE VIEW v_viajes AS
SELECT v.*,
       l.nombre AS lancha, l.codigo AS codigo_lancha,
       p.nombre AS patron, c.nombre AS cliente, b.nombre AS buque,
       t.tramos, t.primer_zarpe, t.ultima_llegada,
       coalesce(e.espera, interval '0') AS espera_total
  FROM viajes v
  JOIN lanchas l ON l.id = v.lancha_id
  JOIN usuarios p ON p.id = v.patron_id
  LEFT JOIN clientes c ON c.id = v.cliente_id
  LEFT JOIN buques b ON b.id = v.buque_id
  LEFT JOIN LATERAL (SELECT count(*) AS tramos, min(salida) AS primer_zarpe, max(llegada) AS ultima_llegada
                       FROM viajes_tramos WHERE viaje_id = v.id) t ON true
  LEFT JOIN LATERAL (SELECT sum(fin - inicio) AS espera
                       FROM viajes_esperas WHERE viaje_id = v.id AND fin IS NOT NULL) e ON true;
