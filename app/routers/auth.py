from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from sqlalchemy.orm import Session
from datetime import timedelta
from .. import models, auth as auth_module
from ..templates_config import templates
from ..database import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


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
    user = auth_module.authenticate_user(db, username, password)
    if not user:
        return templates.TemplateResponse(request, "login.html", {"error": "Usuario o contraseña incorrectos"})
    token = auth_module.create_access_token({"sub": user.username})
    response = RedirectResponse("/", status_code=302)
    response.set_cookie("access_token", f"Bearer {token}", httponly=True, max_age=86400)
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse("/auth/login", status_code=302)
    response.delete_cookie("access_token")
    return response


@router.get("/usuarios", response_class=HTMLResponse)
async def usuarios_page(request: Request, db: Session = Depends(get_db)):
    user = await auth_module.require_admin(request, db)
    usuarios = db.query(models.User).all()
    return templates.TemplateResponse(request, "usuarios.html", {
        "user": user,
        "usuarios": usuarios,
        "secciones": auth_module.SECCIONES,
        "get_user_permissions": auth_module.get_user_permissions,
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
