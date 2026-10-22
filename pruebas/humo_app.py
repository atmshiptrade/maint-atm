"""Prueba de humo de la aplicación (la corre pruebas/correr_app.sh contra la instancia de PRUEBA).

Uso: python3 pruebas/humo_app.py <clave admin> <foto de prueba> <base de datos de prueba>
"""
import datetime
import http.cookiejar
import json
import os
import re
import secrets
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import struct
import uuid
import zlib

BASE = "http://127.0.0.1:8003"
ADMIN_CLAVE, FOTO, BASE_DATOS = sys.argv[1], sys.argv[2], sys.argv[3]
HOY = datetime.date.today().isoformat()
fallos = []


class SinRedireccion(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class Cliente:
    def __init__(self, usuario, clave):
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), SinRedireccion)
        estado, _, _ = self.post("/login", {"usuario": usuario, "password": clave})
        assert estado == 303, f"login {usuario}: {estado}"

    def pedir(self, ruta, cuerpo=None, cabeceras=None):
        try:
            r = self.op.open(urllib.request.Request(BASE + ruta, cuerpo, cabeceras or {}))
            return r.status, r.read().decode(errors="replace"), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(errors="replace"), e.headers

    def csrf(self, pagina):
        return re.search(r'name="csrf" value="([^"]+)"', self.pedir(pagina)[1]).group(1)

    def post(self, ruta, datos, pagina=None, cabeceras=None, archivos=None, campo="fotos", tipo="image/jpeg"):
        datos = dict(datos, csrf=self.csrf(pagina or ruta))
        if archivos is None:
            return self.pedir(ruta, urllib.parse.urlencode(datos, doseq=True).encode(), cabeceras)
        # multipart/form-data con fotos (o el archivo del campo indicado)
        limite = uuid.uuid4().hex
        partes = []
        for clave, valor in datos.items():
            for v in (valor if isinstance(valor, list) else [valor]):
                partes.append(f'--{limite}\r\nContent-Disposition: form-data; name="{clave}"\r\n\r\n{v}\r\n'.encode())
        for nombre, contenido in archivos:
            partes.append(f'--{limite}\r\nContent-Disposition: form-data; name="{campo}"; filename="{nombre}"\r\n'
                          f'Content-Type: {tipo}\r\n\r\n'.encode() + contenido + b"\r\n")
        cuerpo = b"".join(partes) + f"--{limite}--\r\n".encode()
        return self.pedir(ruta, cuerpo, {"Content-Type": f"multipart/form-data; boundary={limite}"})


def verificar(nombre, condicion, detalle=""):
    print(("  OK    " if condicion else "  FALLA ") + nombre + (f" — {detalle}" if detalle and not condicion else ""))
    if not condicion:
        fallos.append(nombre)


def error(html):
    m = re.search(r'alert-danger" role="alert">([^<]+)<', html)
    return m.group(1).strip() if m else ""


def png(ancho, alto):
    """PNG RGBA semitransparente, sin depender de Pillow."""
    def trozo(tipo, cuerpo):
        return struct.pack(">I", len(cuerpo)) + tipo + cuerpo + struct.pack(">I", zlib.crc32(tipo + cuerpo))
    filas = (b"\x00" + b"\xff\xff\xff\x80" * ancho) * alto
    return (b"\x89PNG\r\n\x1a\n" + trozo(b"IHDR", struct.pack(">IIBBBBB", ancho, alto, 8, 6, 0, 0, 0))
            + trozo(b"IDAT", zlib.compress(filas)) + trozo(b"IEND", b""))


def sql(consulta):
    return subprocess.run(["docker", "compose", "exec", "-T", "db", "psql", "-U", "mantenimiento", "-d", BASE_DATOS,
                           "-tAc", consulta], capture_output=True, text=True, check=True).stdout.strip()


foto = open(FOTO, "rb").read()
admin = Cliente("admin", ADMIN_CLAVE)
ROLES_PRUEBA = {"sup": ("MANTENIMIENTO", "SUPERVISOR"), "mec": ("MANTENIMIENTO", "MECANICO"),
                "mec2": ("MANTENIMIENTO", "MECANICO"), "jefe": ("OPERACIONES", "SUPERVISOR_OPS"),
                "patron": ("OPERACIONES", "PATRON"), "patron2": ("OPERACIONES", "PATRON")}
claves = {u: secrets.token_urlsafe(12) for u in ROLES_PRUEBA}
for u, (modulo, rol) in ROLES_PRUEBA.items():
    estado, html, _ = admin.post("/usuarios/nuevo", {"nombre": f"Prueba {u}", "usuario": u, f"rol_{modulo}": rol,
                                                     "password": claves[u]}, "/usuarios/nuevo")
    assert estado == 303, f"crear {u}: {estado} {error(html)}"
sup, mec, mec2, jefe, patron, patron2 = (Cliente(u, claves[u]) for u in ROLES_PRUEBA)

print("Roles y términos:")
_, html, _ = admin.pedir("/usuarios")
verificar("el rol se llama Supervisor de Mantenimiento", "Supervisor de Mantenimiento" in html)
_, html, _ = sup.pedir("/")
verificar("logo blanco, ODS y 'por aprobar'", "/static/img/logo-blanco.png" in html and "ODS por aprobar" in html and "OT " not in html)
verificar("el supervisor ve Data", 'href="/data"' in html)

print("Menú por módulos y Configuración:")
_, html, _ = admin.pedir("/")
verificar("secciones Mantenimiento y Desarrollador, sin título del sistema",
          all(f'sidebar-seccion">{t}<' in html for t in ("Mantenimiento", "Desarrollador"))
          and not any(t in html for t in ("Mantenimiento de flota", ">Operación<", ">Planificación<", ">Flota<", ">Administración<")))
verificar("el administrador ve Configuración", 'href="/configuracion"' in html)
_, html, _ = sup.pedir("/")
verificar("el supervisor ve Data en Desarrollador, sin Usuarios ni Configuración",
          'sidebar-seccion">Desarrollador<' in html and 'href="/data"' in html
          and 'href="/usuarios"' not in html and 'href="/configuracion"' not in html)
_, html, _ = mec.pedir("/")
verificar("el mecánico no ve la sección Desarrollador", "Desarrollador" not in html and 'sidebar-seccion">Mantenimiento<' in html)
estado, _, _ = sup.pedir("/configuracion")
verificar("el supervisor no entra a Configuración", estado == 403)
_, html, _ = admin.pedir("/configuracion")
verificar("Configuración muestra colores y logos", all(t in html for t in (
    'name="color_principal"', 'value="#192A89"', 'name="color_fondo"', "Logo del menú", "Logo a color", "Ahora se usa el logo original")))
colores = {"color_principal": "#7A1F3D", "color_menu": "#2B0F1C", "color_acento": "#E0A100",
           "color_secundario": "#F2C14E", "color_fondo": "#FAF7F2"}
estado, html, _ = admin.post("/configuracion/colores", colores | {"color_acento": "amarillo"}, "/configuracion")
verificar("un color inválido se rechaza", estado == 422 and "no es un color válido" in error(html), error(html))
estado, html, _ = admin.post("/configuracion/colores", colores, "/configuracion")
verificar("colores guardados", estado == 303, error(html))
_, html, _ = mec.pedir("/ordenes")
verificar("los colores nuevos se aplican a todos (también al mecánico)",
          "--color-principal: #7A1F3D;" in html and "--color-principal-rgb: 122, 31, 61;" in html
          and '<meta name="theme-color" content="#2B0F1C">' in html)
estado, html, _ = admin.post("/configuracion/colores", colores | {"color_principal": "#F5F5F5"}, "/configuracion")
_, html, _ = admin.pedir("/configuracion")
verificar("un color principal muy claro se guarda pero avisa", "texto blanco de los botones" in html)
estado, html, _ = admin.post("/configuracion/colores/restaurar", {}, "/configuracion")
_, html, _ = admin.pedir("/configuracion")
verificar("restaurar vuelve a los colores originales", estado == 303 and "--color-principal: #192A89;" in html
          and sql("SELECT count(*) FROM configuracion") == "0")

texto = b"esto no es una imagen"
estado, html, _ = admin.post("/configuracion/logos/logo_menu", {}, "/configuracion", archivos=[("logo.png", texto)],
                             campo="archivo", tipo="image/png")
verificar("un archivo que no es imagen se rechaza", estado == 422 and "no es una imagen" in error(html), error(html))
svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
estado, html, _ = admin.post("/configuracion/logos/logo_menu", {}, "/configuracion", archivos=[("logo.svg", svg)],
                             campo="archivo", tipo="image/svg+xml")
verificar("un SVG se rechaza", estado == 422, error(html))
estado, html, _ = admin.post("/configuracion/logos/logo_menu", {}, "/configuracion", archivos=[("logo.jpg", foto)],
                             campo="archivo")
verificar(f"un logo de más de 5 MB se rechaza ({len(foto) // 1024 // 1024} MB)", estado == 422 and "5 MB" in error(html),
          error(html))
estado, html, _ = admin.post("/configuracion/logos/logo_menu", {}, "/configuracion", archivos=[("logo.png", png(1600, 400))],
                             campo="archivo", tipo="image/png")
verificar("logo del menú subido", estado == 303, error(html))
verificar("el logo se guardó como PNG reducido a 1200 px, proporcional",
          sql("SELECT tipo || ' ' || ancho || 'x' || alto FROM configuracion_imagenes WHERE clave = 'logo_menu'") == "image/png 1200x300")
_, html, _ = mec.pedir("/")
m = re.search(r'class="sidebar-logo" src="(/marca/logo_menu\.png\?v=[0-9a-f]+)"', html)
verificar("el menú usa el logo subido", m is not None)
if not m:
    sys.exit("sin logo subido no se puede seguir")
anonimo = urllib.request.urlopen(BASE + m.group(1))
verificar("el logo se sirve sin sesión, como PNG y en caché",
          anonimo.status == 200 and anonimo.headers["Content-Type"] == "image/png"
          and "immutable" in anonimo.headers["Cache-Control"] and anonimo.read(8) == b"\x89PNG\r\n\x1a\n")
login = urllib.request.urlopen(BASE + "/login").read().decode()
verificar("la página de ingreso sigue con el logo a color original y sin título",
          'src="/static/img/logo.png"' in login and "Mantenimiento de flota" not in login)
estado, _, _ = admin.pedir("/marca/otro.png")
verificar("un logo que no existe da 404", estado == 404)
estado, html, _ = admin.post("/configuracion/logos/logo_menu/restaurar", {}, "/configuracion")
_, html, _ = admin.pedir("/")
verificar("usar el logo original", estado == 303 and 'class="sidebar-logo" src="/static/img/logo-blanco.png"' in html)

print("Flota → Maquinaria:")
_, html, _ = sup.pedir("/")
verificar("el menú tiene Maquinaria y ya no Lanchas/Generadores por separado",
          'href="/maquinaria"' in html and 'sidebar-enlace" href="/lanchas"' not in html and 'href="/generadores"' not in html)
estado, _, h = sup.pedir("/maquinaria")
verificar("Maquinaria abre en lanchas", estado == 303 and h.get("Location") == "/lanchas")
for ruta, boton in (("/lanchas", "Nueva lancha"), ("/generadores", "Nuevo generador")):
    _, html, _ = sup.pedir(ruta)
    verificar(f"{ruta}: título Maquinaria, selector y botón «{boton}»",
              "Maquinaria</h1>" in html and "selector-maquinaria" in html and boton in html
              and 'class="sidebar-enlace activo" href="/maquinaria"' in html)
_, html, _ = sup.pedir("/lanchas/nueva")
verificar("crear lancha: selector para cambiar a generador", 'href="/generadores/nuevo"' in html and "Nueva maquinaria" in html)
_, html, _ = sup.pedir("/generadores/nuevo")
verificar("crear generador: selector para cambiar a lancha", 'href="/lanchas/nueva"' in html and "Nueva maquinaria" in html)

print("Data → Actividades:")
_, html, _ = sup.pedir("/")
verificar("Actividades ya no está en el menú lateral", 'href="/actividades"' not in html and 'href="/data/actividades"' not in html)
_, html, _ = sup.pedir("/data")
verificar("la portada de Data tiene Actividades", 'href="/data/actividades"' in html)
_, html, _ = sup.pedir("/data/actividades")
verificar("en Actividades el menú activo es Data", 'class="sidebar-enlace activo" href="/data"' in html)
estado, html, h = sup.post("/data/actividades/nueva", {"nombre": "Cambio de aceite", "categoria_id": 2, "severidad": "ALTA",
                                                       "tolerancia_pct": "10", "intervalo_horas_sugerido": "250",
                                                       "activa": "on"}, "/data/actividades/nueva")
verificar("crear actividad desde Data", estado == 303 and h.get("Location") == "/data/actividades", error(html))
sup.post("/data/actividades/nueva", {"nombre": "Cambio de correa", "categoria_id": 3, "severidad": "MEDIA",
                                     "tolerancia_pct": "10", "activa": "on"}, "/data/actividades/nueva")
estado, _, h = sup.pedir("/actividades")
verificar("la dirección vieja redirige a Data", estado == 301 and h.get("Location") == "/data/actividades")
estado, _, _ = mec.pedir("/data/actividades")
verificar("el mecánico no administra actividades", estado == 403)

print("Data → Proveedores:")
estado, _, _ = mec.pedir("/data/proveedores")
verificar("el mecánico no administra proveedores", estado == 403)
estado, html, _ = sup.post("/data/proveedores", {"nombre": "Taller Marino del Caribe", "rif": "j-12345678-9",
                                                 "especialidad": "Fuera de borda", "telefono": "0412-0000000"},
                           "/data/proveedores")
verificar("registrar proveedor", estado == 303, error(html))
estado, html, _ = sup.post("/data/proveedores", {"nombre": "Electro Naval"}, "/data/proveedores", {"Accept": "application/json"})
verificar("alta rápida de proveedor (JSON)", estado == 200 and json.loads(html)["nombre"] == "Electro Naval", html[:100])
estado, html, _ = sup.post("/data/proveedores", {"nombre": "Otro", "rif": "J-12345678-9"}, "/data/proveedores",
                           {"Accept": "application/json"})
verificar("RIF duplicado con mensaje claro", estado == 422 and "RIF" in json.loads(html)["error"], html[:100])
_, html, _ = sup.pedir("/data/proveedores")
verificar("listado con RIF en mayúsculas y botón eliminar", "J-12345678-9" in html and 'aria-label="Eliminar Electro Naval"' in html)
proveedor_id = sql("SELECT id FROM proveedores WHERE nombre = 'Taller Marino del Caribe'")
_, html, _ = sup.pedir("/data")
verificar("portada de Data con proveedores", "/data/proveedores" in html)

print("ODS con detalle del servicio y fotos:")
_, _, h = sup.post("/lanchas/nueva", {"nombre": "Pacific", "matricula": "PA-1", "estado": "OPERATIVA",
                                      "horometro_inicial": "5000", "fecha_alta": HOY}, "/lanchas/nueva")
lancha_id = h.get("Location").rsplit("/", 1)[1]
_, html, _ = mec.pedir(f"/bitacora/nueva?lancha_id={lancha_id}")
luz = min(int(i) for i in re.findall(r'name="item_(\d+)"', html))
estado, html, h = mec.post("/bitacora/nueva", {"lancha_id": lancha_id, "fecha": HOY, "horometro_lancha": "5010",
                                               f"item_{luz}": "FALLA", f"comentario_{luz}": "No enciende"},
                           f"/bitacora/nueva?lancha_id={lancha_id}")
bitacora = h.get("Location")
ods = sql("SELECT id FROM ordenes ORDER BY id LIMIT 1")
mec.post(f"/ordenes/{ods}/tomar", {}, f"/ordenes/{ods}")
_, html, _ = mec.pedir(f"/ordenes/{ods}")
verificar("formulario con los campos nuevos", all(t in html for t in ("Corresponde al programa de mantenimiento",
    "Diagnóstico antes del trabajo", "Descripción del servicio de mantenimiento requerido",
    "Descripción del servicio de mantenimiento realizado", "Servicio contratado", "Observaciones",
    'name="fotos"', "Guardar avance")))
descripcion = sql(f"SELECT descripcion FROM ordenes WHERE id = {ods}")
verificar("el servicio requerido viene precargado con la descripción de la ODS", f"{descripcion}</textarea>" in html)
verificar("el mecánico no ve 'Reportar y aprobar'", "Reportar y aprobar" not in html)
estado, html, _ = mec.post(f"/ordenes/{ods}/guardar", {"diagnostico": "Bombillo quemado"}, f"/ordenes/{ods}",
                           archivos=[("antes.jpg", foto)])
verificar("guardar avance con diagnóstico y foto", estado == 303, error(html))
ancho, alto, peso = (int(x) for x in sql(f"SELECT ancho, alto, bytes FROM ordenes_fotos WHERE orden_id = {ods}").split("|"))
verificar(f"la foto se enderezó y redujo (original {len(foto) // 1024} KB, 4000x3000 girada)",
          (ancho, alto) == (1440, 1920) and peso < len(foto), f"{ancho}x{alto}, {peso // 1024} KB")
_, html, _ = mec.pedir(f"/ordenes/{ods}")
verificar("el avance quedó guardado", "Bombillo quemado</textarea>" in html and "Fotos del servicio (1)" in html)
foto_id = sql(f"SELECT id FROM ordenes_fotos WHERE orden_id = {ods}")
estado, contenido, cab = mec.pedir(f"/ordenes/{ods}/fotos/{foto_id}?mini=1")
verificar("la miniatura se sirve como JPEG", estado == 200 and cab.get("Content-Type") == "image/jpeg")
anonimo = urllib.request.build_opener(SinRedireccion)
try:
    estado = anonimo.open(BASE + f"/ordenes/{ods}/fotos/{foto_id}").status
except urllib.error.HTTPError as e:
    estado = e.code
verificar("sin sesión las fotos no se ven", estado == 303)
estado, _, _ = mec2.post(f"/ordenes/{ods}/fotos/{foto_id}/eliminar", {}, f"/ordenes/{ods}")
verificar("otro mecánico no puede quitar la foto", estado == 403)
estado, html, _ = mec.post(f"/ordenes/{ods}/guardar", {}, f"/ordenes/{ods}", archivos=[("nota.jpg", b"esto no es una imagen")])
verificar("un archivo que no es imagen se rechaza", estado == 422 and "no es una imagen" in error(html), error(html))
base = {"fecha_ejecucion": HOY, "trabajo_realizado": "Se cambió el bombillo de babor", "diagnostico": "Bombillo quemado",
        "servicio_requerido": descripcion, "horas_reparacion": "1", "fuera_servicio": "NO"}
estado, html, _ = mec.post(f"/ordenes/{ods}/ejecutar", base | {"servicio": "PROPIO"}, f"/ordenes/{ods}")
verificar("reportar sin indicar si es del programa se rechaza", estado == 422 and "programa" in error(html), error(html))
base |= {"programa": "NO"}
estado, html, _ = mec.post(f"/ordenes/{ods}/ejecutar", base, f"/ordenes/{ods}")
verificar("reportar sin indicar quién hizo el servicio se rechaza", estado == 422 and "contratado" in error(html), error(html))
estado, html, _ = mec.post(f"/ordenes/{ods}/ejecutar", base | {"servicio": "CONTRATADO"}, f"/ordenes/{ods}")
verificar("contratado sin proveedor se rechaza", estado == 422 and "Proveedor" in error(html), error(html))
verificar("tras el error se conserva lo escrito", "Se cambió el bombillo de babor</textarea>" in html)
estado, html, _ = mec.post(f"/ordenes/{ods}/ejecutar", base | {"servicio": "CONTRATADO", "proveedor_id": proveedor_id,
                                                               "observaciones": "Garantía de 3 meses"},
                           f"/ordenes/{ods}", archivos=[("despues.jpg", foto)])
verificar("reportar servicio contratado con proveedor y foto", estado == 303, error(html))
estado, _, _ = mec.post(f"/ordenes/{ods}/cerrar", {}, f"/ordenes/{ods}")
verificar("el mecánico no puede aprobar", estado == 403)
_, html, _ = sup.pedir(f"/ordenes/{ods}")
verificar("el supervisor ve 'Por aprobar' y la aprobación", "Por aprobar" in html and "Aprobar y cerrar ODS" in html)
estado, html, _ = sup.post(f"/ordenes/{ods}/cerrar", {"comentario": "Revisado en sitio"}, f"/ordenes/{ods}")
verificar("el supervisor aprueba", estado == 303, error(html))
_, html, _ = sup.pedir(f"/ordenes/{ods}")
verificar("detalle aprobado con proveedor, diagnóstico y 2 fotos", all(t in html for t in ("No, servicio fuera del programa",
    "Aprobada", "Taller Marino del Caribe", "Bombillo quemado", "Garantía de 3 meses", "Fotos del servicio (2)")))
estado, _, _ = sup.post(f"/ordenes/{ods}/fotos", {}, f"/ordenes/{ods}", archivos=[("tarde.jpg", foto)])
verificar("una ODS cerrada no acepta más fotos", estado == 403)

print("El supervisor reporta y aprueba su propia ODS:")
estado, html, h = sup.post("/ordenes/nueva", {"unidad": f"L{lancha_id}", "prioridad": "MEDIA", "descripcion": "Asiento roto"},
                           "/ordenes/nueva")
ods2 = h.get("Location").rsplit("/", 1)[1]
sup.post(f"/ordenes/{ods2}/tomar", {}, f"/ordenes/{ods2}")
_, html, _ = sup.pedir(f"/ordenes/{ods2}")
verificar("el supervisor ve 'Reportar y aprobar'", "Reportar y aprobar" in html)
estado, html, _ = sup.post(f"/ordenes/{ods2}/ejecutar_aprobar", {"fecha_ejecucion": HOY, "servicio": "PROPIO", "programa": "NO",
                                                                 "horas_reparacion": "0.5", "fuera_servicio": "NO",
                                                                 "trabajo_realizado": "Se cambió el asiento"},
                           f"/ordenes/{ods2}")
verificar("reportada y aprobada en un paso", estado == 303, error(html))
_, html, _ = sup.pedir(f"/ordenes/{ods2}")
verificar("queda cerrada y marcada como autoaprobada", "Cerrada" in html and "autoaprobada" in html
          and "Mecánicos de la empresa" in html)

print("ODS correctiva que cumple tareas del programa:")
propulsion = sql(f"SELECT id FROM equipos WHERE lancha_id = {lancha_id} AND tipo = 'PROPULSION'")
act_aceite = sql("SELECT id FROM actividades WHERE nombre = 'Cambio de aceite'")
act_correa = sql("SELECT id FROM actividades WHERE nombre = 'Cambio de correa'")
for actividad, cada in ((act_aceite, "250"), (act_correa, "10")):
    estado, html, _ = sup.post("/programa/nueva", {"actividad_id": actividad, "intervalo_horas": cada, "equipo_id": [propulsion],
                                                   f"ultimo_{propulsion}": "5000", "fecha_ultimo_servicio": HOY},
                               f"/programa/nueva?lancha_id={lancha_id}")
    verificar(f"programar actividad {actividad} en la lancha", estado == 303, error(html))
tarea_aceite = sql(f"SELECT id FROM tareas WHERE actividad_id = {act_aceite} AND equipo_id = {propulsion}")
tarea_correa = sql(f"SELECT id FROM tareas WHERE actividad_id = {act_correa} AND equipo_id = {propulsion}")
preventiva = sql(f"SELECT id FROM ordenes WHERE tarea_id = {tarea_correa}")
verificar("la correa (cada 10 h) ya quedó con su ODS preventiva abierta", preventiva != "")
estado, html, h = sup.post("/ordenes/nueva", {"unidad": f"L{lancha_id}", "prioridad": "ALTA",
                                              "descripcion": "Se rompió la correa y se aprovechó el cambio de aceite"},
                           "/ordenes/nueva")
ods3 = h.get("Location").rsplit("/", 1)[1]
mec.post(f"/ordenes/{ods3}/tomar", {}, f"/ordenes/{ods3}")
_, html, _ = mec.pedir(f"/ordenes/{ods3}")
verificar("la ODS muestra las tareas del programa de la lancha",
          f'name="tarea_id" value="{tarea_aceite}"' in html and f'name="tarea_id" value="{tarea_correa}"' in html
          and f"ODS #{preventiva} pendiente" in html)
reporte = {"fecha_ejecucion": HOY, "servicio": "PROPIO", "trabajo_realizado": "Cambio de correa y de aceite",
           "horometro_ejecucion": "5012", "programa": "SI", "horas_reparacion": "2", "fuera_servicio": "NO"}
estado, html, _ = mec.post(f"/ordenes/{ods3}/ejecutar", reporte, f"/ordenes/{ods3}")
verificar("'Sí' sin marcar tareas se rechaza", estado == 422 and "Marca qué tareas" in error(html), error(html))
estado, html, _ = mec.post(f"/ordenes/{ods3}/ejecutar", reporte | {"tarea_id": [tarea_aceite, tarea_correa],
                                                                   "horometro_ejecucion": ""}, f"/ordenes/{ods3}")
verificar("del programa sin horómetro se rechaza", estado == 422 and "Horómetro" in error(html), error(html))
estado, html, _ = mec.post(f"/ordenes/{ods3}/ejecutar", reporte | {"tarea_id": [tarea_aceite, tarea_correa]},
                           f"/ordenes/{ods3}")
verificar("reportada cumpliendo 2 tareas del programa", estado == 303, error(html))
estado, html, _ = sup.post(f"/ordenes/{ods3}/cerrar", {}, f"/ordenes/{ods3}")
verificar("aprobada", estado == 303, error(html))
_, html, _ = sup.pedir(f"/ordenes/{ods3}")
verificar("avisa que se reprogramaron las 2 tareas", "Se reprogramaron 2 tarea(s)" in html and "reprogramada, próximo: 5262.0 h" in html
          and "reprogramada, próximo: 5022.0 h" in html)
verificar("el historial dice 'ODS creada' (no 'OT')", "ODS creada" in html and "OT creada" not in html)
verificar("las tareas se reprogramaron desde 5012 h",
          sql(f"SELECT string_agg(proximo_horometro::text, ',' ORDER BY id) FROM tareas WHERE id IN ({tarea_aceite}, {tarea_correa})")
          == "5262.0,5022.0")
verificar("la ODS preventiva de la correa se canceló sola",
          sql(f"SELECT estado || '|' || (SELECT comentario FROM ordenes_historial WHERE orden_id = o.id ORDER BY id DESC LIMIT 1) FROM ordenes o WHERE id = {preventiva}")
          == f"CANCELADA|Cumplida con la ODS #{ods3}")

print("Filtro de ODS por fechas:")
AYER = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
MANANA = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
_, html, _ = sup.pedir(f"/ordenes?vista=todas&desde={HOY}&hasta={HOY}")
verificar("realizadas hoy: aparece la ODS reportada hoy", f'href="/ordenes/{ods3}"' in html and "orden(es) realizadas" in html)
verificar("realizadas: no aparecen las que no se han ejecutado", f'href="/ordenes/{preventiva}"' not in html)
_, html, _ = sup.pedir(f"/ordenes?vista=todas&desde={MANANA}")
verificar("desde mañana: no hay órdenes", f'href="/ordenes/{ods3}"' not in html and "con esas fechas" in html)
_, html, _ = sup.pedir(f"/ordenes?vista=todas&fecha=apertura&desde={HOY}&hasta={HOY}")
verificar("abiertas hoy: incluye también las no ejecutadas", f'href="/ordenes/{preventiva}"' in html and f'href="/ordenes/{ods3}"' in html)
_, html, _ = sup.pedir(f"/ordenes?vista=todas&fecha=cierre&desde={HOY}&hasta={HOY}")
verificar("aprobadas hoy", f'href="/ordenes/{ods3}"' in html)
_, html, _ = sup.pedir(f"/ordenes?vista=todas&desde={HOY}&hasta={AYER}")
verificar("desde > hasta se invierte solo", f'href="/ordenes/{ods3}"' in html)
estado, html, _ = sup.pedir("/ordenes?vista=todas&desde=2026-13-45&hasta=basura&fecha=xx")
verificar("fechas inválidas se ignoran sin error", estado == 200 and f'href="/ordenes/{ods3}"' in html)
_, html, _ = sup.pedir(f"/ordenes?vista=CERRADA&lancha_id={lancha_id}&desde={HOY}")
verificar("las pestañas conservan lancha y fechas", f"vista=todas&amp;lancha_id={lancha_id}" in html and f"desde={HOY}" in html
          and "solo «Cerradas»" in html)
_, html, _ = sup.pedir("/ordenes")
verificar("los atajos desde «Pendientes» buscan en todas", "Este mes" in html and "vista=todas&amp;desde=" in html)

print("Data → Herramientas y repuestos, motivos de inoperatividad:")
for cliente_web, nombre in ((mec, "el mecánico"), (jefe, "Operaciones")):
    for ruta in ("/data/materiales", "/data/motivos-inoperatividad"):
        estado, _, _ = cliente_web.pedir(ruta)
        verificar(f"{nombre} no administra {ruta}", estado == 403)
_, html, _ = sup.pedir("/data")
verificar("la portada de Data los muestra en Mantenimiento", 'href="/data/materiales"' in html
          and 'href="/data/motivos-inoperatividad"' in html and html.count('<h2 class="h5 mb-3">Mantenimiento</h2>') == 1)
_, html, _ = jefe.pedir("/data")
verificar("Operaciones no los ve en Data", 'href="/data/materiales"' not in html)
estado, html, _ = sup.post("/data/materiales", {"nombre": "Filtro de aceite Yamaha", "tipo": "REPUESTO", "unidad": "unidad",
                                                "codigo": "69J-13440"}, "/data/materiales")
verificar("registrar un repuesto", estado == 303, error(html))
estado, html, _ = sup.post("/data/materiales", {"nombre": "Aceite 15W-40", "tipo": "INSUMO", "unidad": "litro"},
                           "/data/materiales", {"Accept": "application/json"})
aceite_json = json.loads(html) if estado == 200 else {}
verificar("alta rápida (JSON) con tipo y unidad", aceite_json.get("tipo") == "INSUMO" and aceite_json.get("unidad") == "litro", html[:150])
estado, html, _ = sup.post("/data/materiales", {"nombre": "aceite 15w-40", "tipo": "INSUMO"}, "/data/materiales",
                           {"Accept": "application/json"})
verificar("nombre repetido (sin importar mayúsculas)", estado == 422 and "Ya existe un material" in json.loads(html)["error"], html[:150])
sup.post("/data/materiales", {"nombre": "Llave de filtro", "tipo": "HERRAMIENTA"}, "/data/materiales")
estado, _, _ = mec.post("/data/materiales", {"nombre": "Cinta", "tipo": "INSUMO"}, "/ordenes", {"Accept": "application/json"})
verificar("el mecánico no registra materiales", estado == 403)
filtro, aceite, llave = (sql(f"SELECT id FROM materiales WHERE nombre = '{n}'")
                         for n in ("Filtro de aceite Yamaha", "Aceite 15W-40", "Llave de filtro"))
_, html, _ = sup.pedir("/data/materiales")
verificar("la lista muestra tipo, unidad y sugerencias de unidad", "Herramienta" in html and "69J-13440" in html
          and 'list="sugerencias-unidad"' in html and '<option value="galón">' in html)
_, html, _ = sup.pedir("/data/motivos-inoperatividad")
verificar("motivos iniciales", "Repuesto no disponible" in html and "Fuera del horario laboral" in html)

print("Actividad con herramientas y repuestos:")
_, html, _ = sup.pedir("/data/actividades/nueva")
verificar("el formulario tiene la lista de materiales y el alta rápida", 'name="material_id"' in html
          and "Registrar uno nuevo" in html and 'data-clave="materiales"' in html and "Otros materiales o notas" in html)
actividad_base = {"nombre": "Cambio de aceite de cola", "categoria_id": 2, "severidad": "MEDIA", "tolerancia_pct": "10",
                  "activa": "on", "materiales": "Trapos"}
estado, html, _ = sup.post("/data/actividades/nueva", actividad_base | {"material_id": [filtro, filtro], "material_cantidad": ["1", "2"]},
                           "/data/actividades/nueva")
verificar("un material repetido se rechaza", estado == 422 and "repetido" in error(html), error(html))
verificar("tras el error se conservan las filas", html.count(f'<option value="{filtro}" data-unidad="unidad" selected>') == 2)
estado, html, _ = sup.post("/data/actividades/nueva", actividad_base | {"material_id": ["", aceite], "material_cantidad": ["3", "1"]},
                           "/data/actividades/nueva")
verificar("una cantidad sin material se rechaza", estado == 422 and "Elige el material" in error(html), error(html))
estado, html, _ = sup.post("/data/actividades/nueva", actividad_base | {"material_id": [filtro, aceite, llave, ""],
                                                                       "material_cantidad": ["1", "0,5", "", ""]},
                           "/data/actividades/nueva")
verificar("actividad con 3 materiales (cantidad con coma, herramienta sin cantidad, fila vacía ignorada)", estado == 303, error(html))
act_cola = sql("SELECT id FROM actividades WHERE nombre = 'Cambio de aceite de cola'")
verificar("se guardaron con sus cantidades",
          sql(f"SELECT string_agg(coalesce(cantidad::text, '-'), ',' ORDER BY material_id) FROM actividades_materiales WHERE actividad_id = {act_cola}")
          == "1.00,0.50,-")
_, html, _ = sup.pedir("/data/actividades")
verificar("la lista de actividades muestra los materiales", "Filtro de aceite Yamaha (1 unidad)" in html and "Aceite 15W-40 (0.5 litro)" in html)
_, html, _ = sup.pedir(f"/data/actividades/{act_cola}/editar")
verificar("al editar aparecen elegidos", f'<option value="{aceite}" data-unidad="litro" selected>' in html and 'value="0.5"' in html)

print("ODS: qué falló, tiempos y materiales:")
radio = sql("SELECT id FROM items_inspeccion WHERE nombre = 'Radio VHF'")
estado, html, _ = sup.post("/data/items", {"nombre": "Escape del generador", "categoria_id": 3, "prioridad": "ALTA",
                                           "aplica_a": "GENERADOR", "activo": "on"}, "/data/items")
escape = sql("SELECT id FROM items_inspeccion WHERE nombre = 'Escape del generador'")
_, html, _ = sup.pedir("/ordenes/nueva")
verificar("la ODS manual pregunta qué falló (lancha y generador)", 'name="item_id"' in html and 'data-aplica="GENERADOR"' in html)
estado, html, _ = sup.post("/ordenes/nueva", {"unidad": f"L{lancha_id}", "prioridad": "ALTA", "descripcion": "Humo", "item_id": escape},
                           "/ordenes/nueva")
verificar("un ítem del generador en una ODS de lancha se rechaza", estado == 422 and "ítem del generador" in error(html), error(html))
estado, html, h = sup.post("/ordenes/nueva", {"unidad": f"L{lancha_id}", "prioridad": "ALTA", "descripcion": "No transmite",
                                              "item_id": radio}, "/ordenes/nueva")
verificar("ODS manual clasificada (Radio VHF)", estado == 303, error(html))
ods4 = h.get("Location").rsplit("/", 1)[1]
mec.post(f"/ordenes/{ods4}/tomar", {}, f"/ordenes/{ods4}")
_, html, _ = mec.pedir(f"/ordenes/{ods4}")
verificar("el reporte tiene qué falló, tiempos y materiales", all(t in html for t in (
    f'<option value="{radio}" selected>Radio VHF</option>', "Duración del trabajo", "Volvió a operar",
    'name="demora_motivo"', "Agregar material", "Otros materiales")) and "Registrar uno nuevo" not in html)
ANTEAYER = (datetime.date.today() - datetime.timedelta(days=2)).isoformat()
reporte4 = {"fecha_ejecucion": HOY, "servicio": "PROPIO", "programa": "NO", "trabajo_realizado": "Se cambió el radio",
            "item_id": radio, "fuera_servicio": "SI", "fuera_servicio_desde": f"{ANTEAYER}T08:00",
            "fuera_servicio_hasta": f"{AYER}T08:00", "horas_reparacion": "4", "horas_hombre": "8",
            "material_id": [filtro, llave], "material_cantidad": ["1", ""], "materiales_usados": "Cinta aislante"}
estado, html, _ = mec.post(f"/ordenes/{ods4}/ejecutar", reporte4 | {"horas_reparacion": ""}, f"/ordenes/{ods4}")
verificar("reportar sin la duración del trabajo se rechaza", estado == 422 and "Duración del trabajo" in error(html), error(html))
estado, html, _ = mec.post(f"/ordenes/{ods4}/ejecutar", reporte4 | {"fuera_servicio": ""}, f"/ordenes/{ods4}")
verificar("reportar sin decir si quedó fuera de servicio se rechaza", estado == 422 and "fuera de servicio" in error(html), error(html))
estado, html, _ = mec.post(f"/ordenes/{ods4}/ejecutar", reporte4 | {"fuera_servicio_hasta": ""}, f"/ordenes/{ods4}")
verificar("fuera de servicio sin la hora en que volvió a operar se rechaza", estado == 422 and "Volvió a operar" in error(html), error(html))
estado, html, _ = mec.post(f"/ordenes/{ods4}/ejecutar", reporte4 | {"horas_reparacion": "30"}, f"/ordenes/{ods4}")
verificar("el trabajo no puede durar más que el tiempo fuera de servicio", estado == 422 and "no puede ser mayor" in error(html), error(html))
repuesto = sql("SELECT id FROM motivos_inoperatividad WHERE nombre = 'Repuesto no disponible'")
horario = sql("SELECT id FROM motivos_inoperatividad WHERE nombre = 'Fuera del horario laboral'")
estado, html, _ = mec.post(f"/ordenes/{ods4}/ejecutar", reporte4 | {"demora_motivo": [repuesto, horario], "demora_horas": ["14", "10"]},
                           f"/ordenes/{ods4}")
verificar("los motivos no suman más que los factores externos (24 h − 4 h = 20 h)",
          estado == 422 and "Los motivos suman 24.00 h" in error(html), error(html))
verificar("tras el error se conservan materiales y motivos", f'<option value="{horario}" selected>' in html
          and f'<option value="{llave}" data-unidad="" selected>' in html and "Cinta aislante</textarea>" in html)
estado, html, _ = mec.post(f"/ordenes/{ods4}/ejecutar", reporte4 | {"demora_motivo": ["", horario], "demora_horas": ["3", "4"]},
                           f"/ordenes/{ods4}")
verificar("horas sin motivo se rechazan", estado == 422 and "Elige el motivo" in error(html), error(html))
estado, html, _ = mec.post(f"/ordenes/{ods4}/ejecutar", reporte4 | {"demora_motivo": [repuesto, horario], "demora_horas": ["14", "4"]},
                           f"/ordenes/{ods4}")
verificar("reportada con tiempos, motivos y materiales", estado == 303, error(html))
verificar("quedó guardado", sql(f"SELECT fuera_servicio || '|' || horas_reparacion || '|' || (SELECT sum(horas) FROM ordenes_demoras WHERE orden_id = {ods4}) "
                                f"|| '|' || (SELECT count(*) FROM ordenes_materiales WHERE orden_id = {ods4}) FROM ordenes WHERE id = {ods4}")
          == "true|4.00|18.00|2")
sup.post(f"/ordenes/{ods4}/cerrar", {}, f"/ordenes/{ods4}")
_, html, _ = sup.pedir(f"/ordenes/{ods4}")
verificar("el detalle muestra qué falló, tiempos, motivos y materiales", all(t in html for t in (
    "Qué falló</dt><dd class=\"col-sm-8\">Radio VHF", "<strong>24.0 h</strong>", "Duración del trabajo", "4.00 h",
    "Factores externos", "20.00 h", "Repuesto no disponible — 14.00 h", "Sin motivo indicado — 2.00 h",
    "Materiales usados", "Filtro de aceite Yamaha — 1 unidad", "(herramienta)", "Cinta aislante")), html[html.find("Servicio reportado"):][:200])

# La lancha sigue parada: se guarda el avance sin «volvió a operar»
estado, html, h = sup.post("/ordenes/nueva", {"unidad": f"L{lancha_id}", "prioridad": "CRITICA", "descripcion": "Radio sin señal otra vez",
                                              "item_id": radio}, "/ordenes/nueva")
ods5 = h.get("Location").rsplit("/", 1)[1]
mec.post(f"/ordenes/{ods5}/tomar", {}, f"/ordenes/{ods5}")
estado, html, _ = mec.post(f"/ordenes/{ods5}/guardar", {"fuera_servicio": "SI", "fuera_servicio_desde": f"{AYER}T10:00"},
                           f"/ordenes/{ods5}")
verificar("guardar avance con la lancha todavía fuera de servicio", estado == 303, error(html))
verificar("cuenta como fuera de servicio hasta ahora",
          sql(f"SELECT horas_fuera > 20 FROM v_ordenes_analisis WHERE id = {ods5}") == "t")

# Preventiva: los materiales de la actividad vienen precargados
estado, html, _ = sup.post("/programa/nueva", {"actividad_id": act_cola, "intervalo_horas": "10", "equipo_id": [propulsion],
                                               f"ultimo_{propulsion}": "5000", "fecha_ultimo_servicio": HOY},
                           f"/programa/nueva?lancha_id={lancha_id}")
preventiva_cola = sql(f"SELECT o.id FROM ordenes o JOIN tareas t ON t.id = o.tarea_id WHERE t.actividad_id = {act_cola}")
verificar("se abrió la preventiva de la actividad con materiales", estado == 303 and preventiva_cola != "", error(html))
mec.post(f"/ordenes/{preventiva_cola}/tomar", {}, f"/ordenes/{preventiva_cola}")
_, html, _ = mec.pedir(f"/ordenes/{preventiva_cola}")
verificar("la preventiva trae los materiales previstos y precargados",
          "Materiales previstos" in html and f'<option value="{aceite}" data-unidad="litro" selected>' in html
          and 'value="0.5"' in html and "¿Qué falló?" not in html)

print("Inicio: fallas y tiempos:")
_, html, _ = sup.pedir("/")
verificar("Inicio tiene la sección con la falla más recurrente de la flota", "Fallas y tiempos de mantenimiento" in html
          and "Toda la flota" in html and "<span class=\"indicador-numero d-block\">2 veces</span>" in html
          and "Falla más recurrente: <strong>Radio VHF</strong>" in html, re.sub(r"\s+", " ", html[html.find("indicador-numero", html.find("analisis")):][:700]))
verificar("tabla por lancha y generador", "Por lancha y generador" in html and f'href="/?unidad=L{lancha_id}' in html)
verificar("reparto del tiempo fuera de servicio", "Trabajo de reparación" in html and "Repuesto no disponible" in html
          and "Sin motivo indicado" in html)
verificar("«Sin clasificar» explica qué significa", "Sin clasificar" in html and "no se indicó qué falló" in html)
_, html, _ = sup.pedir(f"/?unidad=L{lancha_id}&desde={ANTEAYER}&hasta={HOY}")
verificar("al elegir la lancha: su historial en el período", "Historial de Pacific" in html and f'href="/ordenes/{ods4}"' in html
          and "Sigue fuera" in html and 'href="/lanchas/' in html and "Por lancha y generador" not in html)
verificar("…con su disponibilidad", "disponibilidad" in html)
_, html, _ = sup.pedir(f"/?desde={ANTEAYER}&hasta={ANTEAYER}")
verificar("si ninguna falla se repitió, no se elige una «más recurrente»", "ninguna se ha repetido" in html or "sin fallas" in html)
_, html, _ = sup.pedir(f"/?unidad=L{lancha_id}&desde={MANANA}&hasta={MANANA}")
verificar("un período sin datos no falla", "Sin órdenes de servicio en este período" in html and "Sin fallas en este período" in html)
estado, html, _ = sup.pedir("/?unidad=X99&desde=basura&hasta=2026-99-99")
verificar("filtros inválidos se ignoran", estado == 200 and "Toda la flota" in html)
_, html, _ = sup.pedir("/?unidad=G")
verificar("todos los generadores", "Todos los generadores" in html)
_, html, _ = mec.pedir("/")
verificar("el mecánico también lo consulta", "Fallas y tiempos de mantenimiento" in html)
_, html, _ = sup.pedir(f"/lanchas/{lancha_id}")
verificar("la lancha enlaza a su historial y fallas", f'href="/?unidad=L{lancha_id}#analisis"' in html)

print("Roles por módulo:")
estado, _, h = jefe.pedir("/")
verificar("quien solo es de Operaciones entra a su módulo", estado == 303 and h.get("Location") == "/operaciones")
_, html, _ = jefe.pedir("/operaciones")
verificar("su menú tiene Operaciones y Data, nada de Mantenimiento",
          'sidebar-seccion">Operaciones<' in html and 'href="/data"' in html
          and 'sidebar-seccion">Mantenimiento<' not in html and 'href="/bitacora"' not in html)
for ruta in ("/bitacora", "/ordenes", "/programa", "/lanchas", "/generadores", "/data/proveedores",
             "/data/actividades", "/data/items", "/data/categorias"):
    estado, _, _ = jefe.pedir(ruta)
    verificar(f"Operaciones no entra a {ruta}", estado == 403)
_, html, _ = jefe.pedir("/data")
verificar("en Data solo ve los catálogos de Operaciones",
          'href="/data/buques"' in html and 'href="/data/proveedores"' not in html and ">Mantenimiento<" not in html)
for ruta in ("/operaciones", "/viajes", "/viajes/nuevo", "/data/buques"):
    estado, _, _ = sup.pedir(ruta)
    verificar(f"Mantenimiento no entra a {ruta}", estado == 403)
_, html, _ = sup.pedir("/")
verificar("el menú de Mantenimiento no muestra Operaciones", 'href="/operaciones"' not in html)
estado, html, _ = admin.post("/usuarios/nuevo", {"nombre": "Sin rol", "usuario": "sinrol", "password": "clave-larga-1"},
                             "/usuarios/nuevo")
verificar("un usuario sin ningún rol se rechaza", estado == 422 and "al menos un módulo" in error(html), error(html))
admin.post("/usuarios/nuevo", {"nombre": "Mixto", "usuario": "mixto", "rol_MANTENIMIENTO": "MECANICO",
                               "rol_OPERACIONES": "PATRON", "password": "clave-larga-1"}, "/usuarios/nuevo")
_, html, _ = Cliente("mixto", "clave-larga-1").pedir("/")
verificar("con rol en dos módulos ve los dos", 'sidebar-seccion">Mantenimiento<' in html
          and 'sidebar-seccion">Operaciones<' in html and "Mecánico · Patrón" in html)
_, html, _ = admin.pedir("/usuarios")
verificar("la lista de usuarios muestra el rol en cada módulo", "Supervisor de Operaciones" in html and "<th>Operaciones</th>" in html)

print("Operaciones · catálogos en Data:")
estado, html, _ = jefe.post("/data/ubicaciones", {"nombre": "Muelle Guaraguao", "tipo": "MUELLE"}, "/data/ubicaciones")
verificar("registrar un muelle", estado == 303, error(html))
muelle = sql("SELECT id FROM ubicaciones WHERE nombre = 'Muelle Guaraguao'")
pozuelos = sql("SELECT id FROM ubicaciones WHERE nombre = 'Bahía de Pozuelos'")
estado, html, _ = jefe.post("/data/buques", {"nombre": "MV Mal Escrito", "imo": "9074728"}, "/data/buques")
verificar("un IMO con el dígito verificador malo se rechaza", estado == 422 and "IMO no es válido" in error(html), error(html))
estado, html, _ = jefe.post("/data/buques", {"nombre": "MV Caribe Star", "imo": "IMO 907 4729", "bandera": "Panamá",
                                             "tipo": "Granelero", "arqueo_bruto": "25000", "eslora": "180", "manga": "30"},
                            "/data/buques")
buque = sql("SELECT id FROM buques WHERE nombre = 'MV Caribe Star'")
verificar("registrar un buque (el IMO se normaliza)", estado == 303 and sql(f"SELECT imo FROM buques WHERE id = {buque}") == "9074729",
          error(html))
estado, html, _ = jefe.post("/data/clientes", {"nombre": "Agencia Naviera del Oriente", "rif": "j-12345678-9"}, "/data/clientes",
                            cabeceras={"Accept": "application/json"})
cliente = json.loads(html).get("id") if estado == 200 else None
verificar("alta rápida de un cliente por JSON (la usa el reporte)",
          cliente is not None and sql(f"SELECT rif FROM clientes WHERE id = {cliente}") == "J-12345678-9", html[:150])
jefe.post("/data/marinos", {"nombre": "Pedro Marino", "cedula": "v-111"}, "/data/marinos")
marino = sql("SELECT id FROM marinos WHERE nombre = 'Pedro Marino'")
_, html, _ = jefe.pedir("/data/buques")
verificar("la lista de buques", "MV Caribe Star" in html and "9074729" in html and "Panamá" in html)
estado, _, _ = patron.pedir("/data/buques")
verificar("el patrón no administra catálogos", estado == 403)

print("Operaciones · reporte de viaje:")
id_patron, id_patron2 = (sql(f"SELECT id FROM usuarios WHERE usuario = '{u}'") for u in ("patron", "patron2"))
estado, html, h = jefe.post("/viajes/nuevo", {"fecha": HOY, "lancha_id": lancha_id, "patron_id": id_patron,
                                              "requisicion": "RQC/PCPAL/CM-001"}, "/viajes/nuevo")
verificar("crear el reporte solo con lancha y patrón", estado == 303, error(html))
viaje = h.get("Location", "/0").rsplit("/", 1)[1]
verificar("código PLC-0001", sql(f"SELECT codigo FROM viajes WHERE id = {viaje}") == "PLC-0001")
_, html, _ = patron.pedir("/operaciones")
verificar("el patrón lo ve en Mis viajes", "Mis viajes" in html and f'href="/viajes/{viaje}"' in html)
estado, _, _ = patron2.post(f"/viajes/{viaje}/zarpe", {"origen": f"U{muelle}"}, f"/viajes/{viaje}")
verificar("otro patrón no registra el trayecto de un viaje ajeno", estado == 403)
estado, html, _ = patron.post(f"/viajes/{viaje}/zarpe", {}, f"/viajes/{viaje}")
verificar("zarpar exige el origen", estado == 422 and "de dónde zarpa" in error(html), error(html))
estado, html, _ = patron.post(f"/viajes/{viaje}/zarpe", {"origen": f"U{muelle}", "rumbo": f"U{pozuelos}"}, f"/viajes/{viaje}")
verificar("Zarpé", estado == 303, error(html))
_, html, _ = jefe.pedir("/operaciones")
verificar("el tablero muestra la lancha en servicio, navegando", "En servicio" in html
          and "Navegando hacia Bahía de Pozuelos" in html and "Salió de Muelle Guaraguao" in html)
estado, html, h = jefe.post("/viajes/nuevo", {"fecha": HOY, "lancha_id": lancha_id, "patron_id": id_patron2}, "/viajes/nuevo")
viaje2 = h.get("Location", "/0").rsplit("/", 1)[1]
estado, html, _ = patron2.post(f"/viajes/{viaje2}/zarpe", {"origen": f"U{muelle}"}, f"/viajes/{viaje2}")
verificar("la misma lancha no zarpa en dos viajes a la vez", estado == 422 and "ya tiene un viaje en curso" in error(html), error(html))
estado, html, _ = patron.post(f"/viajes/{viaje}/llegada", {"destino": "BUQUE"}, f"/viajes/{viaje}")
verificar("«a bordo del buque» exige el buque", estado == 422 and "indica primero el buque" in error(html), error(html))
estado, html, _ = patron.post(f"/viajes/{viaje}/editar", {"buque_id": buque, "cliente_id": cliente,
                                                          "solicitado_por": "Capitán Pérez", "marinos": [marino]},
                              f"/viajes/{viaje}/editar")
verificar("el patrón completa buque, cliente y marinos", estado == 303, error(html))
estado, html, _ = patron.post(f"/viajes/{viaje}/llegada", {"destino": "BUQUE"}, f"/viajes/{viaje}")
verificar("Llegué a bordo del buque", estado == 303, error(html))
estado, html, _ = patron.post(f"/viajes/{viaje}/espera/inicio", {"motivo": "Esperando al práctico"}, f"/viajes/{viaje}")
verificar("Inicio de espera", estado == 303, error(html))
_, html, _ = jefe.pedir("/operaciones")
verificar("el tablero muestra la espera", "En espera a bordo del MV Caribe Star" in html and "Esperando al práctico" in html)
estado, html, _ = patron.post(f"/viajes/{viaje}/zarpe", {}, f"/viajes/{viaje}")
verificar("no zarpa con una espera en curso", estado == 422 and "espera en curso" in error(html), error(html))
for paso, datos in (("espera/fin", {}), ("zarpe", {"rumbo": f"U{muelle}"}), ("llegada", {"destino": f"U{muelle}"})):
    estado, html, _ = patron.post(f"/viajes/{viaje}/{paso}", datos, f"/viajes/{viaje}")
    verificar(f"botón {paso}", estado == 303, error(html))
estado, html, _ = patron.post(f"/viajes/{viaje}/finalizar", {}, f"/viajes/{viaje}")
verificar("el patrón finaliza el viaje", estado == 303 and sql(f"SELECT estado FROM viajes WHERE id = {viaje}") == "FINALIZADO",
          error(html))
estado, _, _ = patron.post(f"/viajes/{viaje}/cerrar", {}, f"/viajes/{viaje}")
verificar("el patrón no cierra el reporte", estado == 403)
estado, html, _ = jefe.post(f"/viajes/{viaje}/cerrar", {}, f"/viajes/{viaje}")
verificar("cerrar dice lo que falta", estado == 422 and "falta: el tipo de servicio, el área donde se ejecutó, la descripción"
          in error(html), error(html))
servicio = sql("SELECT id FROM tipos_servicio WHERE nombre = 'Traslado de pasajeros'")
encabezado = {"fecha": HOY, "lancha_id": lancha_id, "patron_id": id_patron, "requisicion": "RQC/PCPAL/CM-001", "buque_id": buque, "cliente_id": cliente,
              "solicitado_por": "Capitán Pérez", "marinos": [marino], "servicios": [servicio], "areas": [pozuelos],
              "descripcion": "Traslado del práctico al MV Caribe Star"}
estado, html, _ = jefe.post(f"/viajes/{viaje}/editar", encabezado, f"/viajes/{viaje}/editar")
verificar("el supervisor completa el encabezado", estado == 303, error(html))
estado, html, _ = jefe.post(f"/viajes/{viaje}/cerrar", {}, f"/viajes/{viaje}")
verificar("reporte cerrado", estado == 303 and sql(f"SELECT estado FROM viajes WHERE id = {viaje}") == "CERRADO", error(html))
_, html, _ = jefe.pedir(f"/viajes/{viaje}")
verificar("el reporte muestra todo lo del formato", all(t in html for t in (
    "Muelle Guaraguao", "A bordo del MV Caribe Star", "Esperando al práctico", "Traslado de pasajeros", "Bahía de Pozuelos",
    "Pedro Marino", "IMO 9074729", "Bandera Panamá", "Agencia Naviera del Oriente", "RQC/PCPAL/CM-001", "Capitán Pérez")))
estado, _, _ = patron.post(f"/viajes/{viaje}/editar", {"descripcion": "Otra cosa"}, f"/viajes/{viaje}")
verificar("el patrón ya no modifica un reporte cerrado", estado == 403)
estado, html, _ = jefe.post(f"/viajes/{viaje}/editar", encabezado | {"descripcion": "Traslado del práctico y documentos"},
                            f"/viajes/{viaje}/editar")
verificar("enmendar sin motivo se rechaza", estado == 422 and "Motivo de la enmienda" in error(html), error(html))
ZONA = os.environ.get("TZ", "UTC")
tramo = sql(f"SELECT id FROM viajes_tramos WHERE viaje_id = {viaje} ORDER BY salida LIMIT 1")
salida, llegada = sql(f"""SELECT to_char((salida AT TIME ZONE '{ZONA}') - interval '5 minutes', 'YYYY-MM-DD"T"HH24:MI')
                                 || '|' || to_char(llegada AT TIME ZONE '{ZONA}', 'YYYY-MM-DD"T"HH24:MI')
                            FROM viajes_tramos WHERE id = {tramo}""").split("|")
estado, html, _ = jefe.post(f"/viajes/{viaje}/tramos/{tramo}", {"origen": f"U{muelle}", "salida": salida, "destino": "BUQUE",
                                                                "llegada": llegada,
                                                                "motivo_enmienda": "El zarpe se registró tarde"},
                            f"/viajes/{viaje}")
verificar("enmendar la hora de un tramo con motivo", estado == 303, error(html))
_, html, _ = jefe.pedir(f"/viajes/{viaje}")
verificar("la enmienda queda en el historial del reporte", "Enmiendas" in html and "El zarpe se registró tarde" in html
          and "Tramo · Salida:" in html)
estado, _, _ = sup.pedir(f"/viajes/{viaje}")
verificar("Mantenimiento no ve los reportes de viaje", estado == 403)
estado, html, _ = jefe.post(f"/viajes/{viaje2}/cancelar", {}, f"/viajes/{viaje2}")
verificar("cancelar exige el motivo", estado == 422 and "Motivo de la cancelación" in error(html), error(html))
estado, html, _ = jefe.post(f"/viajes/{viaje2}/cancelar", {"motivo_cancelacion": "El cliente suspendió el servicio"},
                            f"/viajes/{viaje2}")
verificar("cancelar con motivo", estado == 303 and sql(f"SELECT estado FROM viajes WHERE id = {viaje2}") == "CANCELADO", error(html))
_, html, _ = jefe.pedir("/viajes?vista=CERRADO")
verificar("la lista de cerrados", "PLC-0001" in html and "PLC-0002" not in html)
_, html, _ = patron.pedir("/viajes")
verificar("el patrón ve sus viajes por defecto", "PLC-0001" in html and "PLC-0002" not in html)
_, html, _ = jefe.pedir("/operaciones")
verificar("la lancha vuelve a estar disponible, con su última ubicación", "Disponible" in html and "Última ubicación: Muelle Guaraguao" in html)

print("Regresión de lo anterior:")
_, html, _ = mec.pedir(bitacora)
verificar("la bitácora sigue corrigiéndose", f'href="{bitacora}/editar"' in html)
estado, html, _ = sup.post("/data/categorias", {"nombre": "Refrigeración"}, "/data/actividades/nueva", {"Accept": "application/json"})
verificar("alta rápida de categoría", estado == 200)

print("Todas las páginas:")
for ruta in ["/", "/data", "/data/proveedores", f"/data/proveedores/{proveedor_id}/editar", "/data/categorias",
             "/data/items", "/lanchas", f"/lanchas/{lancha_id}", "/generadores", "/data/actividades", "/data/actividades/nueva", "/programa",
             "/bitacora", bitacora, "/ordenes", "/ordenes?vista=EJECUTADA", f"/ordenes/{ods}", f"/ordenes/{ods2}",
             "/ordenes/nueva", f"/ordenes/{ods}/fotos/{foto_id}", "/cuenta", "/data/materiales",
             f"/data/materiales/{filtro}/editar", "/data/motivos-inoperatividad", f"/data/actividades/{act_cola}/editar",
             f"/ordenes/{ods4}", f"/ordenes/{ods5}", f"/?unidad=L{lancha_id}", "/?unidad=L", "/?unidad=G"]:
    estado, html, _ = sup.pedir(ruta)
    verificar(f"GET {ruta}", estado == 200, f"{estado}: {html[:150]}")
for cliente_web, ruta in [(jefe, r) for r in ("/operaciones", "/viajes", "/viajes?vista=todos", "/viajes/nuevo", f"/viajes/{viaje}",
                                              f"/viajes/{viaje}/editar", f"/viajes/{viaje2}", "/data", "/data/clientes",
                                              "/data/buques", f"/data/buques/{buque}/editar", "/data/ubicaciones",
                                              "/data/tipos-servicio", "/data/marinos", "/cuenta")] \
                        + [(patron, r) for r in ("/operaciones", "/viajes", f"/viajes/{viaje}", "/cuenta")]:
    estado, html, _ = cliente_web.pedir(ruta)
    verificar(f"GET {ruta} (Operaciones)", estado == 200, f"{estado}: {html[:150]}")

print()
print("RESULTADO:", "todo bien" if not fallos else f"{len(fallos)} fallo(s): {fallos}")
sys.exit(1 if fallos else 0)
