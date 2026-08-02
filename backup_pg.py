#!/usr/bin/env python3
"""
Backup / restore completo de la base de datos de bono-app.

A diferencia de /backup/ dentro de la app, este script NO tiene listas de
columnas escritas a mano: descubre tablas y columnas desde information_schema,
asi que nunca queda desactualizado cuando se agrega una columna nueva.

FORMA FACIL: doble click en backup.bat (tiene la URL adentro).

USO MANUAL (PowerShell, desde D:\\MeIA\\bono-app):

  # 1) URL publica de Railway (Postgres -> Variables -> DATABASE_PUBLIC_URL)
  $env:DATABASE_URL="postgresql://postgres:xxx@yyy.proxy.rlwy.net:1234/railway"

  # 2) Backup (se verifica solo al terminar)
  py -3.12 backup_pg.py dump

  # 3) Re-verificar el ultimo backup en cualquier momento
  py -3.12 backup_pg.py verify

  # 4) Ver que trajo
  py -3.12 backup_pg.py inspect

  # 5) Restaurar (PIDE CONFIRMACION: borra todo antes de insertar)
  py -3.12 backup_pg.py restore backups\\backup_20260801_143000.zip

Los comandos verify / inspect / restore, sin nombre de archivo, usan el backup
mas reciente de la carpeta backups\\.

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


def foreign_keys(cur):
    """[(tabla_hija, columna_hija, tabla_padre, columna_padre), ...]"""
    cur.execute("""
        SELECT tc.table_name, kcu.column_name, ccu.table_name, ccu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
        JOIN information_schema.constraint_column_usage ccu
          ON tc.constraint_name = ccu.constraint_name
        WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
    """)
    return cur.fetchall()


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

    mb = os.path.getsize(destino) / 1024 / 1024
    print(f"\nArchivo: {destino}")
    print(f"         {mb:.2f} MB - {sum(conteos.values()):,} filas en {len(tablas)} tablas\n")

    # Verificación inmediata: si el backup no sirve, quiero enterarme AHORA y no
    # el día que lo necesite.
    ok = cmd_verify(destino, conn=conn)
    conn.close()
    if not ok:
        print("\n*** NO USES ESTE BACKUP. Volve a correrlo; si sigue fallando, avisá. ***")
        sys.exit(1)
    return destino


# ─────────────────────────────────────────────────────────────
# VERIFY — un backup "que existe" no sirve; este comprueba que sirva
# ─────────────────────────────────────────────────────────────

def cmd_verify(path, conn=None):
    """Tres controles sobre el ZIP recién hecho:
      1. Se puede reabrir y cada tabla es JSON válido.
      2. La cantidad de filas coincide con la base, tabla por tabla.
      3. Las relaciones cierran DENTRO del backup: toda boleta apunta a un socio
         y a una talonera que también están en el ZIP. Un backup con conteos
         correctos pero relaciones rotas falla al restaurar, y eso recién se
         descubre el día que lo necesitás.
    Devuelve True/False.
    """
    print("VERIFICANDO", os.path.basename(path))
    problemas, avisos = [], []

    # ── 1. legibilidad ────────────────────────────────────────
    try:
        with zipfile.ZipFile(path) as zf:
            roto = zf.testzip()
            if roto:
                print("  [FALLA] el ZIP esta corrupto en:", roto)
                return False
            meta = json.loads(zf.read("_meta.json"))
            datos = {}
            for n in zf.namelist():
                if n.endswith(".json") and n != "_meta.json":
                    try:
                        datos[n[:-5]] = json.loads(zf.read(n))
                    except Exception as e:
                        problemas.append("%s no es JSON valido: %s" % (n, e))
    except Exception as e:
        print("  [FALLA] no se puede abrir el ZIP:", e)
        return False
    print("  [OK] archivo legible - %d tablas, %s" % (len(datos), meta.get("fecha", "")[:19]))

    # ── 2. conteos contra la base ─────────────────────────────
    propia = conn is None
    if propia:
        conn = psycopg2.connect(get_url())
    cur = conn.cursor()
    try:
        for t in sorted(datos):
            try:
                cur.execute('SELECT COUNT(*) FROM "%s"' % t)
                en_db = cur.fetchone()[0]
            except Exception:
                conn.rollback()
                avisos.append("%s: esta en el ZIP pero ya no existe en la base" % t)
                continue
            en_zip = len(datos[t])
            if en_zip != en_db:
                problemas.append("%s: %d filas en el ZIP vs %d en la base" % (t, en_zip, en_db))
        faltantes = [t for t in listar_tablas(cur) if t not in datos]
        if faltantes:
            problemas.append("tablas de la base que no entraron al ZIP: " + ", ".join(faltantes))
        if not problemas:
            total = sum(len(v) for v in datos.values())
            print("  [OK] conteos coinciden - %s filas en total" % format(total, ","))

        # ── 3. integridad referencial dentro del ZIP ──────────
        claves = {}
        for t, filas in datos.items():
            claves[t] = {f.get("id") for f in filas if f.get("id") is not None}
        rotas = 0
        for hija, col_h, padre, col_p in foreign_keys(cur):
            if hija not in datos or padre not in claves or col_p != "id":
                continue
            validos = claves[padre]
            for f in datos[hija]:
                v = f.get(col_h)
                if v is not None and v not in validos:
                    rotas += 1
                    if rotas <= 5:
                        problemas.append(
                            "%s.%s = %s no existe en %s (relacion rota)" % (hija, col_h, v, padre))
        if rotas > 5:
            problemas.append("...y %d relaciones rotas mas" % (rotas - 5))
        if not rotas:
            print("  [OK] relaciones consistentes - todo apunta a algo que existe")
    finally:
        if propia:
            conn.close()

    for a in avisos:
        print("  [aviso]", a)
    if problemas:
        print("\n  BACKUP NO CONFIABLE:")
        for p in problemas:
            print("    -", p)
        return False
    print("\n  BACKUP VERIFICADO - se puede restaurar.")
    return True


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


def ultimo_backup():
    if not os.path.isdir(BACKUP_DIR):
        return None
    zips = sorted(f for f in os.listdir(BACKUP_DIR) if f.endswith(".zip"))
    return os.path.join(BACKUP_DIR, zips[-1]) if zips else None


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] not in ("dump", "restore", "inspect", "verify"):
        sys.exit(__doc__)

    if args[0] == "dump":
        cmd_dump()
    else:
        # Sin argumento, opera sobre el backup más reciente.
        path = args[1] if len(args) > 1 and not args[1].startswith("--") else ultimo_backup()
        if not path:
            sys.exit("No encontre ningun backup en %s. Corre primero:  backup_pg.py dump" % BACKUP_DIR)
        if not os.path.exists(path):
            sys.exit("No existe el archivo: %s" % path)
        if args[0] == "inspect":
            cmd_inspect(path)
        elif args[0] == "verify":
            sys.exit(0 if cmd_verify(path) else 1)
        else:
            cmd_restore(path, forzar="--yes" in args)
