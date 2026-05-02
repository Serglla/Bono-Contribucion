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

templates = Jinja2Templates(env=_env)
