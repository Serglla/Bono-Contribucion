from jinja2 import Environment, FileSystemLoader
from starlette.templating import Jinja2Templates

# cache_size=0 evita el bug de Jinja2 con globals no-hashables
_env = Environment(
    loader=FileSystemLoader("app/templates"),
    cache_size=0,
    autoescape=True
)

# Filtros personalizados
_env.filters["zfill"] = lambda v, n: str(v).zfill(n)

# Helper de permisos disponible en todos los templates como has_permission(user, seccion, accion)
def _has_permission(user, section: str, action: str = "ver") -> bool:
    import json
    if user is None:
        return False
    if getattr(user, "is_admin", False):
        return True
    raw = getattr(user, "permissions", None)
    if not raw:
        return False
    try:
        return json.loads(raw).get(section, {}).get(action, False)
    except Exception:
        return False

_env.globals["has_permission"] = _has_permission

templates = Jinja2Templates(env=_env)
