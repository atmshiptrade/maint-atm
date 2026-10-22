-- =====================================================================
--  009 · Configuración del sistema: colores y logos
--
--  * configuracion: ajustes generales (clave → valor jsonb). Por ahora solo
--    'colores'; otros módulos pueden guardar aquí sus ajustes.
--  * configuracion_imagenes: los logos subidos, dentro de la base para que
--    viajen con el respaldo. Sin fila, se usa el logo original del sistema.
-- =====================================================================

CREATE TABLE configuracion (
    clave            text PRIMARY KEY,
    valor            jsonb NOT NULL,
    actualizado_en   timestamptz NOT NULL DEFAULT now(),
    actualizado_por  bigint REFERENCES usuarios(id)
);

CREATE TABLE configuracion_imagenes (
    clave            text PRIMARY KEY CHECK (clave IN ('logo_menu', 'logo')),
    contenido        bytea NOT NULL,
    tipo             text NOT NULL CHECK (tipo IN ('image/png')),
    ancho            integer NOT NULL,
    alto             integer NOT NULL,
    actualizado_en   timestamptz NOT NULL DEFAULT now(),
    actualizado_por  bigint REFERENCES usuarios(id)
);
