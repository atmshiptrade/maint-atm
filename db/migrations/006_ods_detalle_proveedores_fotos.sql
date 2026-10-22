-- =====================================================================
--  006 · Detalle del servicio en las ODS, proveedores y fotos
--
--  * Al reportar una ODS se indica si el servicio fue hecho por los
--    mecánicos de la empresa o contratado (con su proveedor), el
--    diagnóstico, el servicio requerido, el servicio realizado y
--    observaciones.
--  * Proveedores: lista administrada en el módulo Data.
--  * Fotos del servicio: los archivos van a disco (volumen "fotos"); aquí
--    queda el registro. No se agregan ni se borran fotos de una ODS cerrada.
-- =====================================================================

CREATE TABLE proveedores (
    id            integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre        text NOT NULL UNIQUE,
    rif           text UNIQUE,
    especialidad  text,
    contacto      text,
    telefono      text,
    correo        text,
    notas         text,
    activo        boolean NOT NULL DEFAULT true,
    creado_en     timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE ordenes
    ADD COLUMN diagnostico          text,
    ADD COLUMN servicio_requerido   text,
    ADD COLUMN servicio_contratado  boolean,
    ADD COLUMN proveedor_id         integer REFERENCES proveedores(id),
    ADD COLUMN observaciones        text;

-- Las ODS ya ejecutadas o cerradas las hicieron los mecánicos de la empresa
ALTER TABLE ordenes DISABLE TRIGGER ordenes_validar;
UPDATE ordenes SET servicio_contratado = false WHERE estado IN ('EJECUTADA', 'CERRADA');
ALTER TABLE ordenes ENABLE TRIGGER ordenes_validar;

ALTER TABLE ordenes
    ADD CONSTRAINT ordenes_servicio_definido
        CHECK (estado NOT IN ('EJECUTADA', 'CERRADA') OR servicio_contratado IS NOT NULL),
    ADD CONSTRAINT ordenes_contratado_con_proveedor
        CHECK (servicio_contratado IS NOT TRUE OR proveedor_id IS NOT NULL),
    ADD CONSTRAINT ordenes_proveedor_solo_contratado
        CHECK (proveedor_id IS NULL OR servicio_contratado IS TRUE);

CREATE TABLE ordenes_fotos (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    orden_id         bigint NOT NULL REFERENCES ordenes(id),
    archivo          text NOT NULL UNIQUE,      -- ruta relativa en el volumen de fotos
    nombre_original  text,
    ancho            integer,
    alto             integer,
    bytes            integer,
    subido_por       bigint REFERENCES usuarios(id) DEFAULT usuario_actual(),
    subido_en        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_ordenes_fotos_orden ON ordenes_fotos (orden_id);

CREATE FUNCTION trg_fotos_orden_abierta() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_estado text;
BEGIN
    SELECT estado INTO v_estado FROM ordenes WHERE id = coalesce(NEW.orden_id, OLD.orden_id);
    IF v_estado IN ('CERRADA', 'CANCELADA') THEN
        RAISE EXCEPTION 'La ODS está %: sus fotos ya no se modifican', lower(v_estado);
    END IF;
    RETURN coalesce(NEW, OLD);
END $$;

CREATE TRIGGER fotos_orden_abierta
    BEFORE INSERT OR DELETE ON ordenes_fotos
    FOR EACH ROW EXECUTE FUNCTION trg_fotos_orden_abierta();
