#!/usr/bin/env python3
"""
Backup / restore completo de la base de datos de bono-app.

A diferencia de /backup/ dentro de la app, este script NO tiene listas de
columnas escritas a mano: descubre tablas y columnas desde information_schema,
asi que nunca queda desactualizado cuando se agrega una columna nueva.

USO (PowerShell, desde D:\\MeIA\\bono-app):

  # 1) URL publica de Railway (Postgres -> Variables -> DATABASE_PUBLIC_URL)
  $env:DATABASE_URL="postgresql://postgres:xxx@yyy.proxy.rlwy.net:1234/railway"

  # 2) Backup
  py -3.12 backup_pg.py dump

  # 3) Ver que trajo
  py -3.12 backup_pg.py inspect backups\\backup_20260801_143000.zip

  # 4) Restaurar (PIDE CONFIRMACION: borra todo antes de insertar)
  py -3.12 backup_pg.py restore backups\\backup_20260801_143000.zip

Requiere: py -3.12 -m pip install psycopg2-binary
"""

import json
import os
import sys
import zipfile
from datetime import date, datetime, time
from decimal import Decimal

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    sys.exit("Falta psycopg2. Instalalo con:  py -3.12 -m pip install psycopg2-binary")


BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")


def get_url():
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        sys.exit(
            "No hay DATABASE_URL.\n"
            'PowerShell:  $env:DATABASE_URL="postgresql://..."\n'
            "Usa la URL PUBLICA de Railway (host *.proxy.rlwy.net), "
            "la .railway.internal no funciona desde tu PC."
        )
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if "railway.internal" in url:
        sys.exit(
            "Esa es la URL INTERNA de Railway y no funciona desde afuera.\n"
            "Copia DATABASE_PUBLIC_URL (host *.proxy.rlwy.net)."
        )
    return url


def encode(v):
    """Convierte tipos de Postgres a algo serializable en JSON."""
    if isinstance(v, (datetime, date, time)):
        return {"__t__": "dt", "v": v.isoformat()}
    if isinstance(v, Decimal):
        return {"__t__": "dec", "v": str(v)}
    if isinstance(v, (bytes, memoryview)):
        import base64
        return {"__t__": "b64", "v": base64.b64encode(bytes(v)).decode()}
    return v


def decode(v):
    """Inversa de encode(). Postgres castea los strings ISO solo."""
    if isinstance(v, dict) and "__t__" in v:
        if v["__t__"] == "b64":
            import base64
            return base64.b64decode(v["v"])
        return v["v"]
    return v


def listar_tablas(cur):
    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """)
    return [r[0] for r in cur.fetchall()]


def columnas_de(cur, tabla):
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
    """, (tabla,))
    return [r[0] for r in cur.fetchall()]


def orden_por_dependencias(cur, tablas):
    """Ordena las tablas para que las padres se inserten antes que las hijas."""
    cur.execute("""
        SELECT tc.table_name, ccu.table_name AS referencia
        FROM information_schema.table_constraints tc
        JOIN information_schema.constraint_column_usage ccu
          ON tc.constraint_name = ccu.constraint_name
        WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
    """)
    deps = {t: set() for t in tablas}
    for hija, padre in cur.fetchall():
        if hija in deps and padre in tablas and hija != padre:
            deps[hija].add(padre)

    orden, pendientes = [], dict(deps)
    while pendientes:
        libres = sorted(t for t, d in pendientes.items() if not (d - set(orden)))
        if not libres:                      # ciclo de FKs: corto por lo que quede
            libres = sorted(pendientes)
        for t in libres:
            orden.append(t)
            pendientes.pop(t)
    return orden


# ─────────────────────────────────────────────────────────────
# DUMP
# ─────────────────────────────────────────────────────────────

def cmd_dump():
    url = get_url()
    os.makedirs(BACKUP_DIR, exist_ok=True)
    conn = psycopg2.connect(url)
    cur = conn.cursor()

    tablas = listar_tablas(cur)
    if not tablas:
        sys.exit("No se encontro ninguna tabla en el schema public.")

    dcur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = os.path.join(BACKUP_DIR, f"backup_{stamp}.zip")
    conteos = {}

    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as zf:
        for t in tablas:
            dcur.execute(f'SELECT * FROM "{t}"')
            filas = [{k: encode(v) for k, v in dict(r).items()} for r in dcur.fetchall()]
            conteos[t] = len(filas)
            zf.writestr(f"{t}.json", json.dumps(filas, ensure_ascii=False, indent=1))
            print(f"  {t:32} {len(filas):7,} filas")

        cur.execute("SELECT version()")
        zf.writestr("_meta.json", json.dumps({
            "fecha": datetime.now().isoformat(),
            "postgres": cur.fetchone()[0],
            "tablas": conteos,
            "orden_restore": orden_por_dependencias(cur, tablas),
        }, ensure_ascii=False, indent=2))

    conn.close()
    mb = os.path.getsize(destino) / 1024 / 1024
    print(f"\nOK -> {destino}  ({mb:.2f} MB, {sum(conteos.values()):,} filas en {len(tablas)} tablas)")


# ─────────────────────────────────────────────────────────────
# INSPECT
# ─────────────────────────────────────────────────────────────

def cmd_inspect(path):
    with zipfile.ZipFile(path) as zf:
        meta = json.loads(zf.read("_meta.json"))
    print(f"Fecha:    {meta['fecha']}")
    print(f"Postgres: {meta['postgres'][:60]}")
    print(f"\n{'TABLA':34} FILAS")
    total = 0
    for t, n in sorted(meta["tablas"].items(), key=lambda x: -x[1]):
        print(f"  {t:32} {n:7,}")
        total += n
    print(f"\n  {'TOTAL':32} {total:7,}")
    vacias = [t for t, n in meta["tablas"].items() if n == 0]
    if vacias:
        print(f"\nTablas vacias: {', '.join(sorted(vacias))}")


# ─────────────────────────────────────────────────────────────
# RESTORE
# ─────────────────────────────────────────────────────────────

def cmd_restore(path, forzar=False):
    url = get_url()
    with zipfile.ZipFile(path) as zf:
        meta = json.loads(zf.read("_meta.json"))
        datos = {
            n[:-5]: json.loads(zf.read(n))
            for n in zf.namelist() if n.endswith(".json") and n != "_meta.json"
        }

    print(f"Backup del {meta['fecha']} - {sum(meta['tablas'].values()):,} filas")
    print(f"Destino:   {url.split('@')[-1]}")
    if not forzar:
        print("\nEsto BORRA todo el contenido actual de esas tablas.")
        if input('Escribi "RESTAURAR" para continuar: ').strip() != "RESTAURAR":
            sys.exit("Cancelado.")

    conn = psycopg2.connect(url)
    cur = conn.cursor()
    existentes = set(listar_tablas(cur))
    orden = [t for t in meta.get("orden_restore") or orden_por_dependencias(cur, list(datos))
             if t in datos and t in existentes]

    try:
        # Una sola transaccion: si algo falla, no queda a medias.
        for t in reversed(orden):
            cur.execute(f'DELETE FROM "{t}"')

        for t in orden:
            filas = datos[t]
            if not filas:
                continue
            # Solo columnas que existen HOY en la tabla: si el backup es viejo y
            # le falta una columna nueva, entra igual con el default.
            cols = [c for c in columnas_de(cur, t) if c in filas[0]]
            if not cols:
                print(f"  {t:32} SALTEADA (ninguna columna coincide)")
                continue
            campos = ", ".join(f'"{c}"' for c in cols)
            marcas = ", ".join(["%s"] * len(cols))
            psycopg2.extras.execute_batch(
                cur,
                f'INSERT INTO "{t}" ({campos}) VALUES ({marcas})',
                [[decode(f.get(c)) for c in cols] for f in filas],
                page_size=500,
            )
            print(f"  {t:32} {len(filas):7,} filas")

        # Reset de secuencias: sin esto, el proximo INSERT choca con un id ya usado.
        for t in orden:
            for c in columnas_de(cur, t):
                cur.execute("SELECT pg_get_serial_sequence(%s, %s)", (t, c))
                seq = cur.fetchone()[0]
                if seq:
                    cur.execute(
                        f'SELECT setval(%s, COALESCE((SELECT MAX("{c}") FROM "{t}"), 0) + 1, false)',
                        (seq,),
                    )
        conn.commit()
        print("\nOK - restauracion completa.")
    except Exception as e:
        conn.rollback()
        sys.exit(f"\nERROR: {e}\nSe revirtio todo, la base quedo como estaba.")
    finally:
        conn.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] not in ("dump", "restore", "inspect"):
        sys.exit(__doc__)
    if args[0] == "dump":
        cmd_dump()
    elif args[0] == "inspect":
        if len(args) < 2:
            sys.exit("Falta el archivo: backup_pg.py inspect backups\\backup_....zip")
        cmd_inspect(args[1])
    else:
        if len(args) < 2:
            sys.exit("Falta el archivo: backup_pg.py restore backups\\backup_....zip")
        cmd_restore(args[1], forzar="--yes" in args)
