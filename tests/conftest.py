# pytest carga este archivo primero: así el entorno de tests (base temporal,
# campaña fija) queda armado antes de que cualquier test importe `app`.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _entorno  # noqa: E402,F401
