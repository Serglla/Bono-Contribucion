from jinja2 import Environment, FileSystemLoader
from starlette.templating import Jinja2Templates

# cache_size: cantidad de templates compilados que Jinja mantiene en memoria.
#
# Estuvo en 0 (cache desactivado) por un bug viejo con globals no-hashables. Ese
# problema aparece cuando se pasan globals distintos en cada get_template(); acá
# los globals se registran una sola vez sobre el env (ver abajo), así que no
# aplica. Con el cache apagado, Jinja RECOMPILABA el template entero en cada
# request: compradores.html (84 KB) ~34 ms y vendedor_detalle.html (130 KB) ~53 ms
# de CPU puro por visita, y bastante más en el CPU compartido de Railway.
#
# auto_reload=True mantiene el hot reload en desarrollo: Jinja compara el mtime
# del archivo y recompila solo si cambió. Editás un .html y lo ves al recargar,
# igual que antes, pero sin pagar la compilación cuando no cambió nada.
_env = Environment(
    loader=FileSystemLoader("app/templates"),
    cache_size=400,
    auto_reload=True,
    autoescape=True
)

# Filtros personalizados
_env.filters["zfill"] = lambda v, n: str(v).zfill(n)

def _pesos(v):
    """Formatea un número como $1.234.567 (puntos como separador de miles)."""
    try:
        return "$" + f"{int(round(float(v or 0))):,}".replace(",", ".")
    except Exception:
        return "$0"
_env.filters["pesos"] = _pesos

def _fmtcuota(v):
    """Formatea una cantidad de cuotas ponderadas: entero si es entero,
    si no 1 decimal redondeado (ej. 17.3333 -> 17.3). Espejo del helper JS
    fmtCuota() usado en la grilla interactiva de liquidación."""
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        return ""
    r = round(n * 10) / 10
    return str(int(r)) if r == int(r) else f"{r:.1f}"
_env.filters["fmtcuota"] = _fmtcuota

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


def _volver_seguro(valor, default: str = "/") -> str:
    """Ruta de retorno para los botones «Volver».

    Varias pantallas se alcanzan desde más de un lugar (p. ej. la planilla se abre
    tanto desde Planillas como desde Emplanillado), pero el botón Volver tenía el
    destino fijo y siempre te dejaba en el mismo lado, no en el que veniás.
    La solución es que quien linkea pase `?volver=<ruta>`; esta función valida ese
    valor y, si no sirve, cae al destino de siempre.

    Validación (evita open redirect: sin esto, `?volver=https://sitio-malo/` te
    sacaría de la app desde un link manipulado):
      - tiene que ser una ruta interna: empezar con "/" y no con "//"
      - sin esquema ("http:", "javascript:") ni backslashes
    """
    if not valor or not isinstance(valor, str):
        return default
    v = valor.strip()
    if not v.startswith("/") or v.startswith("//"):
        return default
    if "\\" in v or ":" in v.split("?")[0]:
        return default
    return v


_env.globals["volver_seguro"] = _volver_seguro

templates = Jinja2Templates(env=_env)
