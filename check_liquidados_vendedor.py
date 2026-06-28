#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_liquidados_vendedor.py
============================
Reconcilia el "Total liquidados" de un vendedor con su "Total vendidas",
mostrando en qué estado quedó cada boleta que el vendedor liquidó:

  - VENDIDA POR <vend>     : sigue a su nombre y con socio cargado  -> cuenta en "vendidas"
  - SIN CARGAR             : liquidada pero todavia sin socio cargado
  - PASADA A OTRO VENDEDOR : la liquido este vendedor pero despues se paso a otro
                             (Pasar caja / jefe de equipo). Queda en su "liquidado"
                             pero el socio se carga bajo el otro -> por eso no cuadra.

Asi se ve de donde sale la diferencia entre "Total liquidados" (foto al liquidar)
y "Total vendidas" (estado actual).

USO (PowerShell en Windows):
  py -3.12 -m pip install psycopg2-binary        # una sola vez
  $env:DATABASE_URL="postgresql://...proxy.rlwy.net:.../railway"   # DATABASE_PUBLIC_URL de Railway
  py -3.12 check_liquidados_vendedor.py HUGO

  - El nombre del vendedor es opcional (default: HUGO). No distingue mayusculas.
  - Tambien podes pasar la URL como 2do argumento en vez de la variable de entorno.

OJO: usar la URL PUBLICA de Railway (host tipo xxxx.proxy.rlwy.net), NO la interna
(postgres.railway.internal no resuelve desde tu PC).
"""

import os
import sys

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("Falta psycopg2. Instalalo con:  py -3.12 -m pip install psycopg2-binary")
    sys.exit(1)


def get_args():
    vendedor = "HUGO"
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    # Argumentos: [nombre_vendedor] [database_url]
    args = sys.argv[1:]
    for a in args:
        if a.startswith("postgres://") or a.startswith("postgresql://"):
            db_url = a
        else:
            vendedor = a
    return vendedor, db_url


def fnum(x):
    """Formatea un numero: entero si es entero, si no 2 decimales."""
    f = float(x or 0)
    return str(int(round(f))) if abs(f - round(f)) < 1e-9 else f"{f:.2f}"


def main():
    vendedor, db_url = get_args()
    if not db_url:
        print("No encontre la URL de la base. Defini la variable de entorno DATABASE_URL,")
        print('por ejemplo en PowerShell:')
        print('  $env:DATABASE_URL="postgresql://usuario:pass@xxxx.proxy.rlwy.net:12345/railway"')
        print("o pasala como argumento:  py -3.12 check_liquidados_vendedor.py HUGO postgresql://...")
        sys.exit(1)

    # psycopg2 no acepta el esquema "postgres://" en algunas versiones
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    # Guarda: si quedo el texto de ejemplo (placeholder con "..."), avisar claro
    if "..." in db_url:
        print("La URL que pusiste todavia tiene '...' (es el texto de ejemplo, no la real).")
        print("Copia el valor de DATABASE_PUBLIC_URL desde Railway:")
        print("  Postgres -> pestaña Variables -> DATABASE_PUBLIC_URL")
        print('  (host tipo xxxxx.proxy.rlwy.net y un puerto numerico, ej. :23456)')
        print('Despues:  $env:DATABASE_URL="postgresql://postgres:PASS@xxxxx.proxy.rlwy.net:23456/railway"')
        sys.exit(1)

    try:
        conn = psycopg2.connect(db_url)
    except Exception as e:
        print(f"No me pude conectar a la base: {e}")
        print("Verifica que sea la URL PUBLICA de Railway (host *.proxy.rlwy.net).")
        sys.exit(1)

    cur = conn.cursor(cursor_factory=RealDictCursor)

    # ── Resolver el vendedor ────────────────────────────────────────────────
    cur.execute("SELECT id, nombre FROM vendedores WHERE UPPER(nombre) = UPPER(%s)", (vendedor,))
    rows = cur.fetchall()
    if not rows:
        cur.execute("SELECT id, nombre FROM vendedores ORDER BY nombre")
        disponibles = ", ".join(r["nombre"] for r in cur.fetchall())
        print(f'No encontre un vendedor llamado "{vendedor}".')
        print(f"Vendedores disponibles: {disponibles}")
        sys.exit(1)
    vid = rows[0]["id"]
    vnombre = rows[0]["nombre"]

    print("=" * 64)
    print(f"  RECONCILIACION DE LIQUIDADO  —  {vnombre} (id {vid})")
    print("=" * 64)

    # ── Snapshot (lo que muestra "Total liquidados" / dashboard) ────────────
    cur.execute("""
        SELECT COALESCE(SUM(cuotas_equiv), 0)   AS cuotas_eq,
               COALESCE(SUM(contados_equiv), 0) AS contados_eq,
               COUNT(*)                         AS n_liq
        FROM liquidaciones_vendedor
        WHERE vendedor_id = %s
    """, (vid,))
    snap = cur.fetchone()
    total_liq_snapshot = int(round(float(snap["cuotas_eq"]))) + int(round(float(snap["contados_eq"])))
    print(f"\nTotal liquidados (FOTO al liquidar, = dashboard): {total_liq_snapshot}")
    print(f"   cuotas_equiv={fnum(snap['cuotas_eq'])}  +  contados_equiv={fnum(snap['contados_eq'])}"
          f"   ({snap['n_liq']} liquidacion/es)")

    # ── Desglose EN VIVO de las boletas que el vendedor liquido ─────────────
    cur.execute("""
        SELECT
          CASE
            WHEN b.comprador_id IS NULL          THEN 'SIN CARGAR'
            WHEN b.vendedor_id <> lv.vendedor_id THEN 'PASADA A OTRO VENDEDOR'
            ELSE 'VENDIDA POR ' || %s
          END AS estado,
          COUNT(*) AS cantidad,
          SUM(COALESCE(t.multiplicador, 1)) AS ponderado
        FROM boletas b
        JOIN liquidaciones_vendedor lv ON lv.id = b.liquidacion_vendedor_id
        LEFT JOIN taloneras t ON t.id = b.talonera_id
        WHERE lv.vendedor_id = %s
        GROUP BY 1
        ORDER BY 1
    """, (vnombre.upper(), vid))
    desglose = cur.fetchall()

    print("\nDesglose EN VIVO (estado actual de las boletas liquidadas por el vendedor):")
    print(f"  {'ESTADO':<28} {'CANT':>5} {'PONDERADO':>10}")
    print("  " + "-" * 46)
    tot_cant = 0
    tot_pond = 0.0
    for r in desglose:
        tot_cant += int(r["cantidad"])
        tot_pond += float(r["ponderado"] or 0)
        print(f"  {r['estado']:<28} {r['cantidad']:>5} {fnum(r['ponderado']):>10}")
    print("  " + "-" * 46)
    print(f"  {'TOTAL (en vivo)':<28} {tot_cant:>5} {fnum(tot_pond):>10}")
    print(f"\n  (El total en vivo puede diferir 1-2 del snapshot {total_liq_snapshot} porque el")
    print("   snapshot quedo congelado al liquidar; lo importante es el reparto por estado.)")

    # ── Detalle: las boletas que NO estan como 'vendida por el vendedor' ────
    cur.execute("""
        SELECT
          b.numero_principal AS numero,
          t.nombre           AS talonera,
          t.multiplicador,
          vact.nombre        AS vendedor_actual,
          c.apellido_nombre  AS socio,
          CASE
            WHEN b.comprador_id IS NULL          THEN 'SIN CARGAR'
            WHEN b.vendedor_id <> lv.vendedor_id THEN 'PASADA A OTRO VENDEDOR'
          END AS estado
        FROM boletas b
        JOIN liquidaciones_vendedor lv ON lv.id = b.liquidacion_vendedor_id
        LEFT JOIN vendedores vact ON vact.id = b.vendedor_id
        LEFT JOIN compradores c   ON c.id   = b.comprador_id
        LEFT JOIN taloneras  t    ON t.id   = b.talonera_id
        WHERE lv.vendedor_id = %s
          AND (b.comprador_id IS NULL OR b.vendedor_id <> lv.vendedor_id)
        ORDER BY estado, t.nombre, b.numero_principal
    """, (vid,))
    detalle = cur.fetchall()

    print("\nDetalle de la diferencia (las que NO cuentan en 'Total vendidas' del vendedor):")
    if not detalle:
        print("  (ninguna — el liquidado coincide con lo vendido)")
    else:
        pata_lbl = lambda n: (n or "?").replace("PATA ", "X")
        print(f"  {'NUMERO':>7}  {'PATA':<5} {'x':>5}  {'ESTADO':<24} DESTINO / SOCIO")
        print("  " + "-" * 70)
        for r in detalle:
            if r["estado"] == "SIN CARGAR":
                destino = "(sin socio cargado)"
            else:
                destino = f"-> {r['vendedor_actual'] or '?'}" + (f"  socio: {r['socio']}" if r["socio"] else "")
            print(f"  {str(r['numero']).zfill(4):>7}  {pata_lbl(r['talonera']):<5} "
                  f"{fnum(r['multiplicador']):>5}  {r['estado']:<24} {destino}")

    print("\n" + "=" * 64)
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
