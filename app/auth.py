import os
import json
import bcrypt
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from . import models
from .database import get_db

import logging as _logging
_log = _logging.getLogger(__name__)

# Secciones del sistema con su clave y nombre legible
SECCIONES = [
    ("reportes",   "Dashboard"),
    ("taloneras",  "Taloneras"),
    ("compradores","Socios"),
    ("vendedores", "Vendedores"),
    ("cobradores", "Cobradores"),
    ("zonas",      "Zonas"),
    ("sorteos",    "Sorteos"),
    ("cobranza",   "Cobranza"),
]

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
if SECRET_KEY == "dev-secret-key-change-in-production":
    _log.warning("⚠️  SECRET_KEY no configurada — usá la variable de entorno SECRET_KEY en producción")

ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=False)


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_user_by_username(db: Session, username: str):
    return db.query(models.User).filter(models.User.username == username).first()


def authenticate_user(db: Session, username: str, password: str):
    user = get_user_by_username(db, username)
    if not user or not verify_password(password, user.hashed_password):
        return None
    return user


async def get_current_user(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("access_token")
    if token and token.startswith("Bearer "):
        token = token[7:]
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            return None
        return get_user_by_username(db, username)
    except JWTError:
        return None


async def require_user(request: Request, db: Session = Depends(get_db)):
    user = await get_current_user(request, db)
    if not user:
        from fastapi.responses import RedirectResponse
        raise HTTPException(status_code=302, headers={"Location": "/auth/login"})
    return user


async def require_admin(request: Request, db: Session = Depends(get_db)):
    user = await require_user(request, db)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Solo administradores")
    return user


# ---------------------------------------------------------------------------
# Permisos por sección
# ---------------------------------------------------------------------------

def get_user_permissions(user) -> dict:
    """Devuelve el dict de permisos del usuario.
    Para admins, otorga todo. Para usuarios sin permisos configurados, deniega todo.
    """
    if user is None:
        return {s: {"ver": False, "editar": False} for s, _ in SECCIONES}
    if user.is_admin:
        return {s: {"ver": True, "editar": True} for s, _ in SECCIONES}
    if not user.permissions:
        return {s: {"ver": False, "editar": False} for s, _ in SECCIONES}
    try:
        perms = json.loads(user.permissions)
        # Asegurar que todas las secciones y acciones existan
        for s, _ in SECCIONES:
            if s not in perms:
                perms[s] = {"ver": False, "editar": False}
            perms[s].setdefault("ver", False)
            perms[s].setdefault("editar", False)
        return perms
    except Exception:
        return {s: {"ver": False, "editar": False} for s, _ in SECCIONES}


def has_permission(user, section: str, action: str = "ver") -> bool:
    """Verifica si el usuario tiene permiso para una sección y acción dadas."""
    if user is None:
        return False
    if user.is_admin:
        return True
    perms = get_user_permissions(user)
    return perms.get(section, {}).get(action, False)


def permisos_dict_from_form(form_data: dict) -> str:
    """Construye el JSON de permisos a partir de los datos del formulario."""
    perms = {}
    for seccion, _ in SECCIONES:
        perms[seccion] = {
            "ver":    form_data.get(f"{seccion}_ver") == "on",
            "editar": form_data.get(f"{seccion}_editar") == "on",
        }
    return json.dumps(perms)
