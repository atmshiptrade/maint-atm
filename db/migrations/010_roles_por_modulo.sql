-- =====================================================================
--  010 · Roles por módulo
--
--  El sistema crece con módulos (Mantenimiento, Operaciones, Comercial…).
--  Cada usuario tiene a lo sumo un rol en cada módulo, y el administrador
--  pasa a ser un indicador global. Un rol de un módulo no ve los demás.
--  Los códigos de rol son únicos en todo el sistema.
-- =====================================================================

ALTER TABLE usuarios ADD COLUMN es_admin boolean NOT NULL DEFAULT false;

CREATE TABLE usuarios_roles (
    usuario_id  bigint NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    modulo      text NOT NULL,
    rol         text NOT NULL,
    PRIMARY KEY (usuario_id, modulo),
    CONSTRAINT usuarios_roles_validos CHECK (
        (modulo = 'MANTENIMIENTO' AND rol IN ('SUPERVISOR', 'MECANICO'))
        OR (modulo = 'OPERACIONES' AND rol IN ('SUPERVISOR_OPS', 'PATRON')))
);

-- Los usuarios actuales conservan lo que tenían
UPDATE usuarios SET es_admin = true WHERE rol = 'ADMIN';
INSERT INTO usuarios_roles (usuario_id, modulo, rol)
SELECT id, 'MANTENIMIENTO', rol FROM usuarios WHERE rol IN ('SUPERVISOR', 'MECANICO');

ALTER TABLE usuarios DROP COLUMN rol;
