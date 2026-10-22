"""Corre todos los tests sin necesitar pytest.

    py -3.12 tests/correr.py            # todos
    py -3.12 tests/correr.py cuotas     # solo los archivos que contengan "cuotas"

Sale con código 1 si algo falla (sirve para chequear antes de pushear).

Bug conocido: un test que documenta un error todavía sin arreglar se marca con
`@bug_conocido("qué pasa")` (de _entorno). Si falla se muestra como BUG y no
cuenta como falla; si empieza a pasar, avisa para sacarle la marca.
"""
import importlib
import os
import sys
import traceback

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import _entorno  # noqa: E402,F401  (base temporal + campaña fija, antes que app)


def main():
    filtro = sys.argv[1] if len(sys.argv) > 1 else ""
    archivos = sorted(f[:-3] for f in os.listdir(AQUI)
                      if f.startswith("test_") and f.endswith(".py") and filtro in f)
    ok = fallos = bugs = 0
    for nombre in archivos:
        mod = importlib.import_module(nombre)
        print(f"\n== {nombre}")
        for fn_nombre in sorted(n for n in dir(mod) if n.startswith("test_")):
            fn = getattr(mod, fn_nombre)
            if not callable(fn):
                continue
            bug = getattr(fn, "bug_conocido", None)
            try:
                fn()
                ok += 1
                if bug:
                    print("  ok    ", fn_nombre, "<- YA PASA: sacarle @bug_conocido")
                else:
                    print("  ok    ", fn_nombre)
            except Exception as e:  # AssertionError o error inesperado
                if bug and isinstance(e, AssertionError):
                    bugs += 1
                    print("  BUG   ", fn_nombre, "->", bug)
                    continue
                fallos += 1
                print("  FALLA ", fn_nombre, "->", type(e).__name__, e)
                if not isinstance(e, AssertionError):
                    traceback.print_exc()
    print(f"\n{ok} ok, {fallos} fallaron" + (f", {bugs} bugs conocidos sin arreglar" if bugs else ""))
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()
