# python -m app.migraciones → deja la base al día (lo corre el Dockerfile antes
# de levantar los workers). Ver app/migraciones/__init__.py.
import logging

from . import asegurar_esquema

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
asegurar_esquema()
print("Esquema al día.")
