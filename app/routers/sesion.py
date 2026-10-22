from fastapi import APIRouter, Depends, Request

from ..db import get_db
from ..seguridad import formulario, hash_password, usuario_actual, verificar_password
from ..web import ERRORES, ErrorFormulario, avisar, campo_texto, redirigir, render, texto_error

router = APIRouter()


@router.get("/login")
def login_form(request: Request):
    if request.session.get("uid"):
        return redirigir("/")
    return render(request, "login.html")


@router.post("/login")
def login(request: Request, form=Depends(formulario), conn=Depends(get_db)):
    nombre_usuario = str(form.get("usuario") or "").strip().lower()
    password = str(form.get("password") or "")
    fila = conn.execute(
        "SELECT id, password_hash FROM usuarios WHERE usuario = %s AND activo", (nombre_usuario,)
    ).fetchone()
    if not fila or not verificar_password(fila["password_hash"], password):
        return render(
            request, "login.html",
            {"error": "Usuario o contraseña incorrectos.", "usuario_ingresado": nombre_usuario}, 401,
        )
    request.session.clear()
    request.session["uid"] = fila["id"]
    return redirigir("/")


@router.post("/logout")
def logout(request: Request, form=Depends(formulario)):
    request.session.clear()
    return redirigir("/login")


@router.get("/cuenta")
def cuenta(request: Request, usuario=Depends(usuario_actual)):
    return render(request, "cuenta.html")


@router.post("/cuenta")
def cambiar_password(request: Request, form=Depends(formulario), conn=Depends(get_db),
                     usuario=Depends(usuario_actual)):
    try:
        actual = campo_texto(form, "actual", "Contraseña actual", True)
        nueva = campo_texto(form, "nueva", "Nueva contraseña", True)
        if len(nueva) < 8:
            raise ErrorFormulario("La nueva contraseña debe tener al menos 8 caracteres.")
        if nueva != form.get("confirmar"):
            raise ErrorFormulario("La confirmación no coincide con la nueva contraseña.")
        fila = conn.execute("SELECT password_hash FROM usuarios WHERE id = %s", (usuario["id"],)).fetchone()
        if not verificar_password(fila["password_hash"], actual):
            raise ErrorFormulario("La contraseña actual no es correcta.")
        with conn.transaction():
            conn.execute("UPDATE usuarios SET password_hash = %s WHERE id = %s",
                         (hash_password(nueva), usuario["id"]))
    except ERRORES as e:
        return render(request, "cuenta.html", {"error": texto_error(e)}, 422)
    conn.commit()
    avisar(request, "Contraseña actualizada.")
    return redirigir("/")
