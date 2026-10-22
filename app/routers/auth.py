from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from sqlalchemy.orm import Session
from datetime import timedelta
from typing import Optional
from urllib.parse import urlencode
import time
from .. import models, auth as auth_module
from ..templates_config import templates
from ..database import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Límite de intentos fallidos de login ────────────────────────────────────
# Sin esto se podían probar contraseñas sin parar. Se cuentan los fallos por
# (IP, usuario) y por IP sola: el primero frena al que insiste con un usuario,
# el segundo al que prueba muchos usuarios distintos. No se bloquea por usuario
# solo, para que nadie pueda dejar afuera a un operador real desde otra IP.
# Vive en memoria de cada worker (con 2 workers el tope real es hasta el doble)
# y se pierde al reiniciar: alcanza para frenar fuerza bruta sin tocar la base.
_VENTANA_SEG       = 15 * 60
_MAX_FALLOS_USER   = 5
_MAX_FALLOS_IP     = 20
_fallos: dict = {}   # clave -> [timestamps de fallos dentro de la ventana]


def _ip_cliente(request: Request) -> str:
    # Railway pone la IP real del cliente en X-Real-IP; local no hay proxy.
    return (request.headers.get("x-real-ip")
            or (request.client.host if request.client else "")
            or "?")


def _claves_login(request: Request, username: str):
    ip = _ip_cliente(request)
    return (f"u:{ip}:{username.strip().lower()}", _MAX_FALLOS_USER), (f"ip:{ip}", _MAX_FALLOS_IP)


def _recientes(clave: str, ahora: float) -> list:
    lst = [t for t in _fallos.get(clave, []) if ahora - t < _VENTANA_SEG]
    if lst:
        _fallos[clave] = lst
    else:
        _fallos.pop(clave, None)
    return lst


def _bloqueado_hasta(request: Request, username: str) -> float:
    """Segundos que faltan para poder reintentar (0 = puede intentar)."""
    ahora = time.time()
    espera = 0.0
    for clave, tope in _claves_login(request, username):
        lst = _recientes(clave, ahora)
        if len(lst) >= tope:
            espera = max(espera, _VENTANA_SEG - (ahora - lst[-tope]))
    return espera


def _registrar_fallo(request: Request, username: str) -> None:
    ahora = time.time()
    if len(_fallos) > 5000:   # tope de memoria: barrer las claves ya vencidas
        for clave in list(_fallos):
            _recientes(clave, ahora)
    for clave, _ in _claves_login(request, username):
        _fallos.setdefault(clave, []).append(ahora)


def _limpiar_fallos(request: Request, username: str) -> None:
    # Login correcto: se perdonan los fallos de ese usuario (no los de la IP).
    _fallos.pop(_claves_login(request, username)[0][0], None)


def _es_https(request: Request) -> bool:
    # Detrás del proxy de Railway la app ve http; el esquema real viene en el header.
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return proto.split(",")[0].strip().lower() == "https"


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: Session = Depends(get_db)):
    user = await auth_module.get_current_user(request, db)
    if user:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    espera = _bloqueado_hasta(request, username)
    if espera > 0:
        minutos = max(1, int(espera // 60) + (1 if espera % 60 else 0))
        return templates.TemplateResponse(
            request, "login.html",
            {"error": f"Demasiados intentos fallidos. Probá de nuevo en {minutos} min."},
            status_code=429,
        )
    user = auth_module.authenticate_user(db, username, password)
    if not user:
        _registrar_fallo(request, username)
        return templates.TemplateResponse(request, "login.html", {"error": "Usuario o contraseña incorrectos"})
    _limpiar_fallos(request, username)
    token = auth_module.create_access_token({"sub": user.username})
    response = RedirectResponse("/", status_code=302)
    # La cookie dura lo mismo que el token (antes 24 h contra 8 h del token).
    # secure: solo viaja por HTTPS en producción; local (http) sigue andando.
    response.set_cookie(
        "access_token", f"Bearer {token}",
        httponly=True,
        secure=_es_https(request),
        samesite="lax",
        max_age=auth_module.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    return response


@router.get("/logout")
async def logout(request: Request):
    response = RedirectResponse("/auth/login", status_code=302)
    response.delete_cookie("access_token", httponly=True,
                           secure=_es_https(request), samesite="lax")
    return response


# ── Cambio de contraseña ────────────────────────────────────────────────────
CLAVE_MIN = 8


def _validar_clave_nueva(nueva: str, repetir: str) -> Optional[str]:
    """Devuelve el mensaje de error, o None si la clave nueva sirve."""
    if len(nueva) < CLAVE_MIN:
        return f"La contraseña nueva tiene que tener al menos {CLAVE_MIN} caracteres."
    if nueva != repetir:
        return "Las dos contraseñas nuevas no coinciden."
    if nueva.strip().lower() in ("admin123", "12345678", "password", "contraseña"):
        return "Esa contraseña es demasiado conocida. Elegí otra."
    return None


def _render_mi_clave(request, user, error=None, msg=None, status_code=200):
    return templates.TemplateResponse(request, "mi_clave.html", {
        "user": user, "error": error, "msg": msg, "clave_min": CLAVE_MIN,
    }, status_code=status_code)


@router.get("/mi-clave", response_class=HTMLResponse)
async def mi_clave_page(request: Request, db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    return _render_mi_clave(request, user)


@router.post("/mi-clave", response_class=HTMLResponse)
async def mi_clave_guardar(
    request: Request,
    actual: str = Form(""),
    nueva: str = Form(""),
    repetir: str = Form(""),
    db: Session = Depends(get_db),
):
    """Cada usuario cambia SU contraseña. Pide la actual: si alguien encuentra
    una sesión abierta no puede cambiarla sin saberla. Los fallos cuentan para
    el mismo límite que el login (no sirve para adivinarla)."""
    user = await auth_module.require_user(request, db)
    if _bloqueado_hasta(request, user.username) > 0:
        return _render_mi_clave(request, user, status_code=429,
                                error="Demasiados intentos fallidos. Probá de nuevo en unos minutos.")
    if not auth_module.verify_password(actual, user.hashed_password):
        _registrar_fallo(request, user.username)
        return _render_mi_clave(request, user, error="La contraseña actual no es correcta.")
    error = _validar_clave_nueva(nueva, repetir)
    if not error and nueva == actual:
        error = "La contraseña nueva tiene que ser distinta de la actual."
    if error:
        return _render_mi_clave(request, user, error=error)
    user.hashed_password = auth_module.hash_password(nueva)
    db.commit()
    _limpiar_fallos(request, user.username)
    return _render_mi_clave(request, user, msg="Listo, tu contraseña quedó cambiada.")


@router.post("/usuarios/{user_id}/clave")
async def admin_cambiar_clave(
    user_id: int,
    request: Request,
    nueva: str = Form(""),
    repetir: str = Form(""),
    db: Session = Depends(get_db),
):
    """El admin le pone una contraseña nueva a otro usuario (ej: se la olvidó).
    Para la propia se usa /auth/mi-clave, que pide la actual."""
    current = await auth_module.require_admin(request, db)
    u = db.query(models.User).get(user_id)
    if not u:
        raise HTTPException(404, "Usuario no encontrado")
    if u.id == current.id:
        return RedirectResponse("/auth/mi-clave", status_code=302)
    error = _validar_clave_nueva(nueva, repetir)
    if error:
        return RedirectResponse(f"/auth/usuarios?{urlencode({'error': error})}", status_code=302)
    u.hashed_password = auth_module.hash_password(nueva)
    db.commit()
    ok = f"Contraseña de {u.username} cambiada. Pasásela por un medio seguro."
    return RedirectResponse(f"/auth/usuarios?{urlencode({'ok': ok})}", status_code=302)


@router.get("/usuarios", response_class=HTMLResponse)
async def usuarios_page(request: Request, db: Session = Depends(get_db)):
    user = await auth_module.require_admin(request, db)
    usuarios = db.query(models.User).all()
    return templates.TemplateResponse(request, "usuarios.html", {
        "user": user,
        "usuarios": usuarios,
        "secciones": auth_module.SECCIONES,
        "get_user_permissions": auth_module.get_user_permissions,
        "msg": request.query_params.get("ok"),
        "error": request.query_params.get("error"),
        "clave_min": CLAVE_MIN,
    })


@router.post("/usuarios/crear")
async def crear_usuario(
    request: Request,
    username: str = Form(...),
    email: str = Form(""),
    password: str = Form(...),
    is_admin: bool = Form(False),
    db: Session = Depends(get_db)
):
    await auth_module.require_admin(request, db)
    if db.query(models.User).filter(models.User.username == username).first():
        raise HTTPException(400, "Usuario ya existe")
    form_data = dict(await request.form())
    perms_json = auth_module.permisos_dict_from_form(form_data) if not is_admin else None
    nuevo = models.User(
        username=username,
        email=email or None,
        hashed_password=auth_module.hash_password(password),
        is_admin=is_admin,
        permissions=perms_json,
    )
    db.add(nuevo)
    db.commit()
    return RedirectResponse("/auth/usuarios", status_code=302)


@router.post("/usuarios/{user_id}/permisos")
async def actualizar_permisos(user_id: int, request: Request, db: Session = Depends(get_db)):
    """Actualiza los permisos de un usuario (solo admin)."""
    await auth_module.require_admin(request, db)
    u = db.query(models.User).get(user_id)
    if not u:
        raise HTTPException(404, "Usuario no encontrado")
    if u.is_admin:
        raise HTTPException(400, "No se pueden restringir permisos a un administrador")
    form_data = dict(await request.form())
    u.permissions = auth_module.permisos_dict_from_form(form_data)
    db.commit()
    return RedirectResponse("/auth/usuarios", status_code=302)


@router.post("/usuarios/{user_id}/eliminar")
async def eliminar_usuario(user_id: int, request: Request, db: Session = Depends(get_db)):
    current = await auth_module.require_admin(request, db)
    if current.id == user_id:
        raise HTTPException(400, "No podés eliminar tu propio usuario")
    u = db.query(models.User).get(user_id)
    if u:
        db.delete(u)
        db.commit()
    return RedirectResponse("/auth/usuarios", status_code=302)
