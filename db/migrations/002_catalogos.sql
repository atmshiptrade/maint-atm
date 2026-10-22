-- =====================================================================
--  002 · Datos base: categorías e ítems de inspección de la bitácora.
--  Los ítems son de ejemplo: el supervisor los ajusta desde la pantalla
--  "Ítems de inspección" (agregar, reordenar, desactivar) sin tocar código.
-- =====================================================================

INSERT INTO categorias (nombre) VALUES
    ('Eléctrica'),
    ('Lubricación'),
    ('Mecánica'),
    ('Casco y estructura'),
    ('Seguridad');

INSERT INTO items_inspeccion (nombre, categoria_id, prioridad, orden)
SELECT v.nombre, c.id, v.prioridad, v.orden
  FROM (VALUES
        ('Luz de navegación babor',               'Eléctrica',          'MEDIA',  10),
        ('Luz de navegación estribor',            'Eléctrica',          'MEDIA',  11),
        ('Luz de tope / alcance',                 'Eléctrica',          'MEDIA',  12),
        ('Batería y bornes',                      'Eléctrica',          'ALTA',   13),
        ('Arranque de motores',                   'Eléctrica',          'ALTA',   14),
        ('Bocina',                                'Eléctrica',          'BAJA',   15),
        ('Radio VHF',                             'Eléctrica',          'ALTA',   16),
        ('Tablero e instrumentos',                'Eléctrica',          'MEDIA',  17),
        ('Nivel de aceite de motor',              'Lubricación',        'ALTA',   20),
        ('Nivel de aceite de pata / cola',        'Lubricación',        'ALTA',   21),
        ('Aceite de dirección / trim',            'Lubricación',        'MEDIA',  22),
        ('Fugas de combustible',                  'Mecánica',           'CRITICA',30),
        ('Fugas de aceite',                       'Mecánica',           'ALTA',   31),
        ('Filtro separador de agua',              'Mecánica',           'MEDIA',  32),
        ('Agua de enfriamiento (chorro testigo)', 'Mecánica',           'ALTA',   33),
        ('Correas',                               'Mecánica',           'MEDIA',  34),
        ('Hélices',                               'Mecánica',           'MEDIA',  35),
        ('Sistema de dirección',                  'Mecánica',           'ALTA',   36),
        ('Trim / tilt',                           'Mecánica',           'BAJA',   37),
        ('Sentina (agua / aceite acumulado)',     'Casco y estructura', 'ALTA',   40),
        ('Bomba de achique',                      'Casco y estructura', 'CRITICA',41),
        ('Casco (golpes / fisuras)',              'Casco y estructura', 'ALTA',   42),
        ('Defensas y cabos',                      'Casco y estructura', 'BAJA',   43),
        ('Chalecos salvavidas',                   'Seguridad',          'ALTA',   50),
        ('Extintor',                              'Seguridad',          'ALTA',   51),
        ('Botiquín',                              'Seguridad',          'MEDIA',  52),
        ('Bengalas / señales',                    'Seguridad',          'MEDIA',  53)
       ) AS v(nombre, categoria, prioridad, orden)
  JOIN categorias c ON c.nombre = v.categoria;
