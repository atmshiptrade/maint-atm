-- =====================================================================
--  008 · El historial de las órdenes dice "ODS creada" (quedaba "OT creada")
-- =====================================================================

CREATE OR REPLACE FUNCTION trg_ordenes_historial() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO ordenes_historial (orden_id, estado_nuevo, usuario_id, comentario)
        VALUES (NEW.id, NEW.estado, usuario_actual(), 'ODS creada');
    ELSIF NEW.estado <> OLD.estado THEN
        INSERT INTO ordenes_historial (orden_id, estado_anterior, estado_nuevo, usuario_id, comentario)
        VALUES (NEW.id, OLD.estado, NEW.estado, usuario_actual(),
                nullif(current_setting('app.comentario', true), ''));
    END IF;
    RETURN NULL;
END $$;

UPDATE ordenes_historial SET comentario = 'ODS creada' WHERE comentario = 'OT creada';
