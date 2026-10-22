# Sistema de gestión · Grupo de Transporte ATM

Aplicación web (FastAPI + PostgreSQL + Docker) organizada por módulos, para usarse en tablets y
teléfonos desde el navegador:

- **Mantenimiento:** lanchas y sus generadores, catálogo de actividades, programa de mantenimiento,
  bitácora diaria con inspección y órdenes de servicio (ODS).
- **Operaciones:** reportes de viaje (formato ATM-RG-OP-002) y disponibilidad de la flota.

**Cómo se modela la flota:**

- El horómetro se lleva por **lancha** (no por motor).
- Los **generadores** son equipos independientes con su propio horómetro, programa y órdenes: no
  pertenecen a ninguna lancha porque se mueven de una a otra. En la bitácora se elige la lancha y el
  generador que lleva ese día; así queda el historial de dónde estuvo cada generador.
- Las actividades del catálogo sirven para ambos, y una misma tarea se puede programar de una vez en
  varias lanchas y generadores.
- Los ítems de inspección indican si se revisan en la lancha o en el generador; las fallas del
  generador abren ODS para ese generador.
- Los códigos (LAN-001, GEN-001) y todos los correlativos se asignan solos.

## Arrancar en desarrollo

```bash
cp .env.example .env
```

Edita `.env` (contraseñas, `SECRET_KEY`, usuario administrador y zona horaria) y luego:

```bash
docker compose up -d --build
```

Abre http://localhost:8000 e ingresa con `ADMIN_USUARIO` / `ADMIN_PASSWORD` de `.env`.
El código en `app/` se recarga solo al guardar.

Para probar el esquema completo con el ejemplo de la lancha Pacific **en una base temporal**
(se crea, se prueba y se borra; tus datos no se tocan):

```bash
./db/pruebas/correr.sh
```

Para probar la aplicación completa con los tres roles (otra instancia en el puerto 8001 sobre una
base temporal, con fotos de prueba; tus datos no se tocan):

```bash
./pruebas/correr_app.sh
```

## Empezar con datos propios

**Opción 1: vaciar solo los datos de trabajo** (conserva los usuarios, las categorías, los ítems de
inspección y los proveedores; borra lanchas, generadores, actividades, programa, bitácoras y ODS):

```bash
docker compose exec db psql -U mantenimiento -d mantenimiento -f /db/scripts/vaciar_datos.sql
```

y luego los archivos de las fotos:

```bash
docker compose exec app sh -c 'rm -rf /srv/datos/fotos/*'
```

**Opción 2: borrar la base completa** y dejarla como recién instalada. El administrador se vuelve a
crear con `ADMIN_USUARIO` / `ADMIN_PASSWORD` de `.env`, así que revisa esos valores antes:

```bash
docker compose down -v
```

```bash
docker compose up -d
```

`down -v` elimina los volúmenes de la base de datos y de las fotos, y no se puede deshacer. **Nunca lo uses en el VPS** con
datos reales: ahí se trabaja con respaldos (`deploy/respaldo.sh`).

## Contraseñas y secretos

| Valor en `.env` | Para qué sirve | Cómo se cambia |
|---|---|---|
| `ADMIN_USUARIO` / `ADMIN_PASSWORD` | Crear el primer administrador. Solo se usa la primera vez que arranca la aplicación; después se ignora. | Dentro de la aplicación: clic en tu nombre → **Mi cuenta**. |
| `SECRET_KEY` | Firmar las cookies de sesión para que nadie pueda fabricar una sesión falsa. No es la contraseña de nadie. | Pon un valor nuevo en `.env` y reinicia `app`. Se cierran todas las sesiones. |
| `POSTGRES_PASSWORD` | Conexión entre la aplicación y la base de datos. | Ver abajo: hay que cambiarla en la base **y** en `.env`. |

**Contraseña de cualquier usuario:** cada uno la cambia en **Mi cuenta**. El administrador puede
asignar una nueva desde **Usuarios → Editar**.

**Si se olvidó la contraseña del administrador** (nadie puede entrar), desde el servidor:

```bash
docker compose exec app python -m app.restablecer_password admin
```

**Cambiar `POSTGRES_PASSWORD`:** PostgreSQL solo lee esa variable al crear la base por primera vez,
así que primero se cambia dentro de la base y después en `.env`:

```bash
docker compose exec db psql -U mantenimiento -d mantenimiento -c "\password mantenimiento"
```

Luego escribe la misma clave en `POSTGRES_PASSWORD` de `.env` y recrea la aplicación con
`docker compose up -d app`.

## Módulos y roles

El menú lateral agrupa las opciones por módulo: **Mantenimiento** (Inicio, Bitácora, Órdenes de servicio,
Programa, Maquinaria), **Operaciones** (Disponibilidad, Reportes de viaje) y **Desarrollador** (Data, Usuarios,
Configuración). Cada usuario tiene **un rol en cada módulo** que usa (o ninguno) y solo ve los módulos donde
tiene rol; el administrador ve todo. Un módulo nuevo se agrega como otra sección en `MENU` de `app/web.py` y
con sus roles en `MODULOS` de `app/seguridad.py`.

**Mantenimiento**

| Opción | Mecánico | Supervisor de Mantenimiento | Administrador |
|---|---|---|---|
| Bitácora (horómetros + inspección) | Registra y corrige las suyas | Registra y corrige cualquiera | Todo |
| Órdenes de servicio | Toma y reporta el servicio (con fotos) | Además asigna, **aprueba**, devuelve, cancela y crea ODS manuales; puede reportar y aprobar las suyas en un paso | Todo |
| Programa (tareas por lancha/motor) | Consulta | Programa y edita | Todo |
| Maquinaria (lanchas y generadores) | Consulta | Crea y edita | Todo |
| Data (actividades, categorías, ítems de inspección, proveedores) | — | Crea y edita | Todo |

**Operaciones**

| Opción | Patrón | Supervisor de Operaciones | Administrador |
|---|---|---|---|
| Disponibilidad (tablero de la flota) | Consulta, y ve sus viajes | Consulta y crea viajes | Todo |
| Reportes de viaje | Registra el trayecto de los suyos con botones, completa el encabezado y los finaliza | Crea, asigna, completa, **cierra**, cancela y **enmienda** | Todo |
| Data (clientes, buques, ubicaciones, tipos de servicio, marinos) | — | Crea y edita | Todo |

**Sistema:** Usuarios y Configuración (colores y logos) son solo del administrador.

Ciclo de una ODS: **Abierta → En proceso** (el mecánico la toma) **→ Por aprobar** (el mecánico reporta
el servicio) **→ Cerrada** (el supervisor de mantenimiento la aprueba). Si la reporta el propio
supervisor, queda marcada como autoaprobada.

**ODS y programa de mantenimiento:** al reportar, toda ODS indica si el servicio **pertenece al programa**
de su lancha o generador. Una preventiva siempre pertenece (a su propia tarea, y se pueden marcar otras
tareas que se hicieron en el mismo servicio). Una correctiva puede ser **No** (trabajo imprevisto, fuera del
plan) o **Sí**: entonces se marcan las tareas del programa que se cumplieron (ej. se rompió la correa y se
cambió) y el horómetro pasa a ser obligatorio. **Al aprobarse**, cada tarea marcada se reprograma sola
desde el horómetro y la fecha de ese servicio, y si alguna tenía una ODS preventiva abierta, esa se
cancela sola con el comentario «Cumplida con la ODS #x».

Al reportar el servicio se indica si lo hicieron los **mecánicos de la empresa** o fue **contratado**
(con su proveedor), el diagnóstico antes del trabajo, el servicio requerido, el servicio realizado,
materiales, horas-hombre, observaciones y **fotos**. Mientras la ODS está en proceso se puede ir llenando
con **Guardar avance**.

**Buscar órdenes por fecha:** en la lista de ODS se filtra por la fecha en que fue **realizada** (la del
servicio), **abierta** o **aprobada**, con *desde / hasta* o los atajos *Este mes*, *Mes pasado* y *Este año*.
Se combina con las pestañas (Cerradas, Todas…), la lancha, el generador y el tipo.

**Fotos:** la tablet las reduce antes de subirlas y el servidor las vuelve a procesar (las endereza,
máximo 1920 px, JPEG de buena calidad, sin datos de ubicación) y genera miniaturas. Se guardan en el
volumen de Docker `fotos`, solo se ven con sesión iniciada y `deploy/respaldo.sh` las respalda junto con
la base.

**Corregir una bitácora:** desde su detalle, botón **Corregir** (el supervisor puede corregir cualquier
bitácora diaria; el mecánico, las que registró él). Se pueden cambiar los horómetros, las respuestas de
la inspección y la observación, indicando el motivo. Si un ítem pasa a **Falla**, al guardar se abre su
ODS. Una falla que ya generó su ODS no se puede quitar: si fue un error, se cancela la ODS. Cada
corrección queda registrada (quién, cuándo, qué cambió y por qué).

**Reporte de viaje (Operaciones):** digitaliza el formato ATM-RG-OP-002. El supervisor crea el reporte
(código **PLC-0001** automático) con la lancha y el patrón; el resto del encabezado (buque, cliente, requisición
comercial, tipo de servicio, área, marinos, descripción) se completa cuando se sepa, y un buque o cliente nuevo
se crea ahí mismo sin ir a Data. El patrón, a bordo, pulsa **Zarpé**, **Llegué**, **Inicio de espera** y **Fin
de espera**: la hora la pone el sistema, el origen de cada tramo es el destino del anterior y el **tiempo de
espera** se calcula solo (sirve para facturar). Al terminar pulsa **Finalizar viaje** y el supervisor lo
**cierra** (el sistema dice qué falta). Antes del cierre, las horas se corrigen o se agregan tramos con sus
horas; después, solo el supervisor con una **enmienda** que exige motivo y queda en el historial del reporte.
Reglas: una lancha o un patrón no tienen dos viajes en curso, una lancha inactiva no se asigna, y una falla
crítica pendiente en Mantenimiento solo avisa. La disponibilidad (Disponible, En servicio, Inactiva) se deduce
de los viajes; no se marca a mano. Sin firmas: en el medio naviero se siguen firmando en papel.

**Configuración:** el administrador cambia los cinco colores del sistema (principal, menú y títulos,
acento, secundario y fondo) viendo el resultado al instante antes de guardar, y sube el logo del menú
(versión clara, sobre el color del menú) y el logo a color (página de ingreso e ícono del navegador). Los
logos se guardan en la base de datos, así que viajan con el respaldo; siempre se puede volver a los
colores y logos originales. Si una combinación se leería mal (por ejemplo, un color principal muy claro
con texto blanco), el sistema avisa al guardar.

**Módulo Data:** administra las opciones de las listas desplegables: categorías (también se pueden
crear al vuelo con el botón **Nueva** del formulario de actividad o ítem), ítems de inspección y
proveedores (el supervisor también los puede crear desde la ODS). Lo que ya está en uso no se elimina: se
desactiva.

## Dónde vive cada regla

Las reglas que deben cumplirse siempre están en PostgreSQL (`db/migrations/`):
horómetro por motor de solo lectura, validación de lecturas (no retroceden, máximo 24 h/día),
una sola ODS pendiente por tarea, transiciones de estado válidas, reprogramación de las tareas
cumplidas al aprobar una ODS (tabla `ordenes_tareas`), cancelación de las preventivas redundantes
e historial de cada ODS con su autor. La aplicación llama a `procesar_bitacora()` al guardar una
bitácora y a `generar_ordenes_preventivas()` cada hora (tareas por calendario).

Cambios de esquema: crea un archivo nuevo `db/migrations/012_lo_que_sea.sql`. Se aplica solo al
reiniciar el contenedor `app` y queda registrado en la tabla `schema_migrations`. Nunca edites una
migración que ya se aplicó en producción.

## Estructura

```
app/
  main.py            aplicación, sesión, job periódico
  db.py              conexión y traducción de errores de la base
  seguridad.py       contraseñas (argon2), roles, CSRF
  web.py             plantillas y lectura de formularios
  migrate.py         migraciones + administrador inicial
  routers/           un archivo por módulo
  templates/         HTML (Bootstrap 5, adaptable a tablet y celular)
db/migrations/       esquema y datos base (se aplican solas al arrancar)
db/pruebas/          prueba del flujo completo en una base temporal
db/scripts/          utilidades (vaciar datos de trabajo)
deploy/              Caddyfile y script de respaldo
```

## Publicar en el VPS

1. VPS con Ubuntu 24.04 LTS, Docker instalado, firewall abierto solo en 22, 80 y 443.
2. Crea en tu DNS un registro tipo **A**: `mantenimiento.tuempresa.com` → IP del VPS.
3. Copia el proyecto al VPS, crea `.env` (con `DOMINIO`) y ejecuta:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Caddy obtiene el certificado HTTPS automáticamente. Programa `deploy/respaldo.sh` con cron y
copia los respaldos fuera del VPS.

## Copia piloto: demo local y en Render

Esta carpeta es una **copia piloto** del sistema para enseñarlo, con datos **ficticios**
(`db/demo/datos_demo.sql`: tres lanchas «Demo», un generador, 30 días de bitácoras, fallas y ODS en
todos los estados). No tiene datos reales de la empresa.

- **Local:** `docker compose up -d --build` → http://localhost:8002 (el sistema original sigue en el
  8000). La prueba de la app usa el 8003. Con `CARGAR_DEMO=true` en `.env`, la demo se carga sola la
  primera vez (base sin lanchas) y crea los usuarios `supervisor` y `mecanico` con `DEMO_PASSWORD`.
- **Render (plan gratuito):**
  1. Sube esta carpeta a un repositorio **nuevo y privado** en GitHub (`.env` y `respaldos/` no se suben).
  2. En Render: **New → Blueprint**, elige el repositorio. `render.yaml` crea la base PostgreSQL 18 y el
     Web Service (Docker) y las conecta.
  3. Render pide `ADMIN_PASSWORD` y `DEMO_PASSWORD` (8+ caracteres). `SECRET_KEY` la genera Render.
  4. Al terminar el despliegue, la URL es `https://atm-mantenimiento-demo.onrender.com` (o parecida).
     Usuarios: `admin`, `supervisor` y `mecanico`.

  Limitaciones del plan gratuito (está bien para una demo, no para uso real):
  - la app se duerme tras 15 min sin visitas y tarda ~1 min en despertar;
  - las **fotos** de las ODS se pierden en cada despliegue o reinicio (no hay disco persistente);
  - la base gratuita **caduca a los 30 días**; para seguir, se crea otra y la demo se vuelve a cargar;
  - el trabajo horario de preventivas solo corre mientras la app está despierta.
