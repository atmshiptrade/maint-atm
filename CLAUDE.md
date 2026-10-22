# Sistema de gestión · Grupo de Transporte ATM, C.A.

Sistema web interno, pensado para crecer con varios módulos:
- **Mantenimiento** de la flota (lanchas y generadores), en uso. Lo usan mecánicos en tablets en el
  muelle y supervisores en oficina.
- **Operaciones**, **en pausa**: la primera versión está terminada y funciona (ver más abajo; falta el PDF).
  Lo usarían patrones a bordo y el supervisor de operaciones.
- **Comercial**, futuro.
Todo el sistema, la interfaz y las conversaciones están en **español**.

> **Esta carpeta (`maint-atm`) es la COPIA PILOTO** para demos (local en el 8002 y en Render con
> `render.yaml`), con datos ficticios de `db/demo/`. El sistema real está en `../maint-champf4` (puerto
> 8000). Aquí la base local no tiene datos reales, y la prueba de la app usa el puerto 8003.

## Cómo trabajar con el usuario

- **Prioridad actual: Mantenimiento.** La empresa quiere ordenar primero el mantenimiento de las
  embarcaciones. No avances Operaciones salvo que el usuario lo pida, pero no lo rompas: las pruebas
  de Operaciones siguen corriendo con todo lo demás.
- **Git lo maneja el usuario:** no hagas `git commit`, `git push` ni cambies ramas. Al terminar una tarea,
  sugiere un mensaje de commit si sirve, pero no lo ejecutes.
- **Datos reales:** la base local tiene datos reales. Antes de aplicar una migración, corre
  `./deploy/respaldo.sh`. Prueba siempre en las instancias temporales (ver Pruebas), nunca creando o
  editando registros en la base real.
- Explica las decisiones de diseño que tomes por tu cuenta y señala los puntos débiles del planteamiento.
- Terminología: **ODS** (Orden de Servicio), nunca "OT". El rol `SUPERVISOR` se muestra como
  "Supervisor de Mantenimiento". El estado `EJECUTADA` se muestra como "Por aprobar". En Operaciones, el
  estado `FINALIZADO` del viaje se muestra como "Por cerrar".
- No pongas un título de sistema ("Mantenimiento de flota", etc.) junto al logo: el sistema es general.

## Comandos

```bash
docker compose up -d --build                                    # levantar (dev: recarga sola, http://localhost:8002)
docker compose logs -f app                                      # ver logs
docker compose restart app                                      # aplica migraciones nuevas (respaldar antes)
docker compose exec db psql -U mantenimiento -d mantenimiento   # consola SQL de la base real
./deploy/respaldo.sh                                            # respaldo de base y fotos en respaldos/
./db/pruebas/correr.sh                                          # prueba de la base: temporal, migraciones + flujo Pacific
./pruebas/correr_app.sh                                         # prueba de la app: instancia temporal en :8003, 3 roles
DEJAR=1 ./pruebas/correr_app.sh                                 # igual, pero deja la instancia :8003 encendida
```

- Ojo: la app de desarrollo se recarga sola al guardar código. Si el código nuevo usa una tabla de una
  migración que aún no se aplicó, la app real se rompe hasta hacer `docker compose restart app`.
- pyflakes no está en el contenedor: usa un venv aparte (en el scratchpad) para revisar `app/` y `pruebas/`.

## Arquitectura

- **FastAPI** con rutas síncronas, plantillas **Jinja2** del lado del servidor, **psycopg 3** con pool
  (`dict_row`), **PostgreSQL 18**, Docker Compose. Producción: VPS + Caddy (`docker-compose.prod.yml`).
- Front: Bootstrap 5.3.3 e íconos Tabler (webfont 3.19.0) desde CDN, fuentes Inter y Montserrat.
  Nada de frameworks JS; solo JS pequeño dentro de cada plantilla (`{% block scripts %}`).
- `app/web.py`: `MENU` (secciones = módulos del sistema), `render`, `avisar`, `redirigir`, los parsers
  `campo_*` y `param_*`, `ErrorFormulario` y `ERRORES`.
- `app/seguridad.py`: roles, `requiere(...)`, `tiene_rol` (ADMIN puede todo) y `formulario` (lee el POST
  y verifica el token CSRF).
- `app/db.py`: pool, `get_db` (fija `app.usuario_id` para los triggers) y `MENSAJES_RESTRICCION`
  (nombre de la restricción → mensaje amable).
- `app/marca.py`: colores y logos configurables (Desarrollador → Configuración), con caché en memoria.
- `app/fotos.py`: fotos de ODS. El navegador las comprime y el servidor las vuelve a procesar
  (máx. 1920 px, sin EXIF/GPS). Se guardan en el volumen `fotos` y se sirven solo con sesión.
- `app/catalogos.py` + `routers/catalogos.py`: catálogos simples de Data definidos por sus campos (los de
  Operaciones y, de Mantenimiento, materiales y motivos de inoperatividad). Una sola pantalla de lista y
  edición, alta por JSON para crear al vuelo desde otro formulario, y desactivar en vez de borrar lo ya usado.
  Un catálogo nuevo se agrega ahí, sin plantillas nuevas.
- `app/materiales.py`: listas de herramientas y repuestos (fila material + cantidad) de actividades y ODS.
  En las plantillas, las macros `lista_materiales`, `fila_demora` y `script_filas` (listas fila por fila).
- `app/analisis.py`: sección «Fallas y tiempos» de Inicio (falla más recurrente, disponibilidad, reparto del
  tiempo fuera de servicio, por unidad e historial de una lancha o generador, filtrado por fechas).
- `app/routers/`: un archivo por pantalla o módulo. `app/templates/<módulo>/`. `app/static/app.css`.
  - Operaciones: `operaciones.py` (tablero de disponibilidad) y `viajes.py` (reporte de viaje: encabezado,
    botones del trayecto, correcciones, cierre y enmiendas).
  - Cada router de un módulo se protege entero con
    `APIRouter(..., dependencies=[Depends(requiere(*ROLES_<MODULO>))])`.

## Base de datos y migraciones

- Las **reglas de negocio viven en PostgreSQL** (triggers, CHECKs y funciones), no solo en Python:
  - horómetro de solo lectura;
  - lecturas que no retroceden y máximo 24 h por día;
  - máquina de estados de la ODS e historial con su autor;
  - una ODS pendiente por tarea;
  - reprogramación de tareas al aprobar una ODS.
- La app llama a `procesar_bitacora(id)` al guardar una bitácora, y a `generar_ordenes_preventivas()`
  cada hora y al crear o editar tareas.
- Vista `v_estado_tareas`: el semáforo del Programa (AL DIA, PROXIMA, VENCIDA, FUERA DE TOLERANCIA, INACTIVA).
- Vista `v_ordenes_analisis` (cada ODS no cancelada con su unidad L<id>/G<id>, qué falló y sus horas) y
  función `horas_fuera_servicio(desde, hasta)` (unión de periodos por unidad: lo solapado cuenta una vez).
- Migraciones: SQL plano numerado en `db/migrations/` (la próxima es **013**). Las aplica
  `app/migrate.py` al arrancar el contenedor y quedan registradas en `schema_migrations`.
  **Nunca edites una migración ya aplicada: crea una nueva.**
- Al agregar un CHECK o una restricción, agrega su mensaje en `MENSAJES_RESTRICCION`.
  Los `RAISE EXCEPTION` de los triggers se muestran tal cual al usuario: escríbelos en español claro.
- Códigos LAN-001 y GEN-001 y correlativos: los genera la base, nunca se escriben a mano.
- `db/scripts/vaciar_datos.sql` borra los datos operativos y conserva usuarios, catálogos y
  configuración. Si creas tablas operativas nuevas, agrégalas ahí.

## Convenciones de código

- Escrituras: `with conn.transaction():` y luego `conn.commit()`. Ante `ERRORES`, se vuelve a mostrar
  el formulario con `texto_error(e)` y estado 422.
- Todo POST usa `form=Depends(formulario)` y la plantilla incluye `{{ csrf_input }}`.
- Confirmaciones: atributo `data-confirmar="…"` en el form o en el botón. Nunca JS en línea con datos
  del usuario (`onclick`/`onsubmit` con variables).
- Colores: solo variables CSS (`--color-principal`, `--color-menu`, `--color-acento`, etc. en `app.css`),
  nunca hex fijos de la marca, porque el administrador puede cambiarlos.
- Pensado para tablets: controles de al menos 44 px y sin desborde horizontal a 375 px de ancho.
- Comentarios y nombres en español, con el estilo del código existente.

## Pruebas

- Cada cambio se valida así:
  - `./db/pruebas/correr.sh` si toca la base: corre `flujo_pacific.sql` (narrado), `flujo_operaciones.sql`
    y `flujo_tiempos.sql` (comprobaciones OK/FALLA);
  - `./pruebas/correr_app.sh` para la app; debe terminar en «RESULTADO: todo bien» y «errores del servidor: 0»;
  - una revisión visual en el navegador, en escritorio y a 375 px.
- Las pruebas de la app están en `pruebas/humo_app.py`. Agrega allí los casos de cada funcionalidad nueva.

## Mantenimiento: fallas, tiempos y materiales (migración 012)

- **Falla** = ODS correctiva no cancelada, clasificada por `ordenes.item_id` (ítem de inspección): la de la
  bitácora lo hereda de la falla (no se cambia); en las manuales es opcional, al crearla o al reportarla.
- **Tiempos al reportar una ODS** (obligatorios desde la 012; las ODS anteriores no los tienen):
  - ¿quedó fuera de servicio? y, si sí, desde / volvió a operar (vacío = sigue parada, con «Guardar avance»);
  - duración del trabajo (horas de reloj; las horas-hombre van aparte);
  - factores externos = fuera de servicio − trabajo, repartido por motivo (`ordenes_demoras`).
  La base valida que el trabajo no supere el tiempo fuera de servicio ni los motivos a los factores externos.
- Materiales y motivos de una ODS solo cambian mientras está ABIERTA o EN_PROCESO.
- El texto libre de materiales (`actividades.materiales`, `ordenes.materiales_usados`) queda como «otros».

## Mantenimiento: temas abiertos e ideas ya conversadas

Ninguno está decidido; si el usuario pide algo relacionado, retómalo desde aquí.
- **Sin decisión del usuario:**
  - campos de costo y factura en el servicio contratado de la ODS;
  - si el diagnóstico debe ser obligatorio en las ODS correctivas;
  - si el mecánico debe ver las actividades (solo lectura);
  - generadores en tierra o dos generadores a bordo de la misma lancha.
- **Ideas propuestas:**
  - exportar a Excel las listas filtradas (por ejemplo, ODS por fecha);
  - ODS imprimible en PDF con logo, fotos y espacio para firmas;
  - costos de repuestos y servicios por lancha y por mes;
  - indicadores: preventivas a tiempo y «lanchas paradas ahora» en Inicio (los datos ya existen: ODS con
    fuera de servicio sin «volvió a operar»); las fallas más repetidas ya están;
  - avisar a Operaciones cuando una lancha está fuera de servicio por una ODS (hoy solo avisa por ODS crítica);
  - marcar «fuera de servicio» desde la bitácora o al crear la ODS, para no depender de que la tomen;
  - reincidencia: la misma falla en la misma lancha antes de N días (el arreglo no funcionó);
  - documentos y certificados de las lanchas con aviso de vencimiento;
  - hoja de vida de cada lancha o generador (el historial de Inicio es un primer paso);
  - inventario de repuestos que se descuentan al cerrar la ODS;
  - avisos por correo o Telegram.
- **Despliegue:** falta publicar en el VPS (Caddy + subdominio; ver README).

## Roles por módulo

- Cada usuario tiene **un rol por módulo** (tabla `usuarios_roles`), más el indicador global
  `usuarios.es_admin`. Los códigos de rol son únicos en todo el sistema, así que
  `tiene_rol(usuario, "SUPERVISOR")` no se confunde entre módulos.
- **Mantenimiento:**
  - `MECANICO`: bitácora, toma y reporta ODS, consulta.
  - `SUPERVISOR` (Supervisor de Mantenimiento): además programa, aprueba, asigna o cancela ODS, y
    administra Maquinaria y los catálogos de Mantenimiento en Data.
- **Operaciones:**
  - `PATRON` (Patrón): registra el trayecto de los viajes que tiene asignados.
  - `SUPERVISOR_OPS` (Supervisor de Operaciones): crea, asigna, cierra y enmienda viajes, y administra
    los catálogos de Operaciones en Data.
- Administrador: todo, más Usuarios y Configuración.
- **Un rol de un módulo no ve nada de otro módulo**: ni el menú, ni las rutas, ni sus catálogos en Data.
  Cada router protege sus rutas con el rol del módulo. Los datos sí se cruzan por debajo, en el backend:
  por ejemplo, Operaciones avisa si la lancha tiene una falla crítica pendiente, sin mostrar la ODS.

## Módulo Operaciones

- Digitaliza el formato **ATM-RG-OP-002 «Reporte de Viaje» (rev. 04)**: una lancha de ATM prestando un
  servicio a un buque cliente. El objetivo es no digitar dos veces lo mismo.
  - Cada reporte tiene un código **PLC-0001** (P.L.C. = Puerto La Cruz), correlativo sin huecos generado
    por la base.
  - Las 10 filas del papel no limitan nada: los tramos son ilimitados.
- **Requisición Comercial** (ATM-RG-CM-001, «RQC/PCPAL/CM-00X»): es la cotización formal que hace el
  departamento Comercial, futuro módulo. Por ahora el viaje solo guarda su número como texto.
  **Clientes** y **Buques** son catálogos compartidos que Comercial reutilizará; por eso Buques incluye
  la manga.
- **Catálogos en Data (Operaciones):**
  - Clientes («Al servicio de»).
  - Buques (bandera, IMO con dígito verificador, tipo, arqueo bruto, eslora y manga).
  - Ubicaciones: áreas (bahías y zonas) y muelles. Es la misma lista para el área del servicio y para
    los puntos del trayecto.
  - Tipos de servicio.
  - Marinos: se eligen de la lista y no entran al sistema.
- **Estados del viaje:** `BORRADOR` → `EN_CURSO` (con el primer evento) → `FINALIZADO` (lo termina el
  patrón) → `CERRADO` (lo cierra el supervisor). También `CANCELADO`.
- **Trayecto:**
  - El patrón pulsa botones (Zarpé, Llegué, Inicio de espera, Fin de espera) y la hora la pone el
    sistema. Las horas se pueden corregir.
  - Cada tramo guarda salida, destino y llegada. **El origen no se guarda**: es el destino del tramo
    anterior (el primero sale del origen del viaje), así que siempre coinciden.
  - Un punto es una ubicación del catálogo o «a bordo del buque» cliente.
  - El tiempo de espera total se calcula a partir de las esperas (con su motivo), nunca se digita.
    Sirve para facturar.
- **Disponibilidad de la lancha:** se deduce, no se guarda. Inactiva si la lancha está inactiva, En
  servicio si tiene un viaje en curso, Disponible en otro caso.
  - Una lancha o un patrón no pueden tener dos viajes en curso a la vez.
  - Una lancha inactiva no se asigna.
  - Una ODS crítica pendiente solo avisa, no impide asignar la lancha.
- **Sin firmas** (decisión del usuario: en el medio naviero se firma en papel). Después del cierre, el
  viaje no cambia salvo por una **enmienda** con motivo, que queda registrada en `viajes_enmiendas`
  (quién, cuándo y el antes y después).
- **Sin modo sin conexión:** las lanchas tienen Starlink.
- **Pendiente:** el PDF con el formato oficial, con páginas de continuación si hay más de 10 tramos.
