#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
resync_liquidaciones_equiv.py
=============================
Re-sincroniza los snapshots `cuotas_equiv` y `contados_equiv` de cada
LiquidacionVendedor a partir de las boletas que HOY cuelgan de esa liquidacion.

Por que: esos dos numeros se congelaron al momento de liquidar. Si despues una
boleta se saco de la liquidacion o se libero, el snapshot no se bajo y quedo mas
alto que la realidad (es el "drift" que vimos: HUGO 174 snapshot vs 171,67 en vivo).
Tras correr esto, el "Total liquidados" del dashboard/detalle vuelve a cerrar con
"vendidas + sin cargar".

Que recalcula, por cada liquidacion:
  - boletas con liquidacion_vendedor_id = esa liquidacion (las que realmente le cuelgan)
  - se separan en CONTADO vs CUOTAS:
      * modalidad_liquidacion = 'contado' / 'contado2'  -> CONTADO
      * modalidad_liquidacion = 'cuotas'                -> CUOTAS
      * sin modalidad (boletas viejas, NULL): se infiere CONTADO si tiene
        numero_especial / numero_especial_2 asignado, si no CUOTAS
  - cuotas_equiv   = suma de multiplicador de las CUOTAS  (PATA 1 x1, X2 x2, X0 x0.67)
  - contados_equiv = suma de multiplicador de las CONTADO
  - Los numeros del pool (LiquidacionContadoItem) NO cuentan (son sorteo extra, no ventas).

NO toca: total_a_rendir, comisiones, montos, cuota_1_total, ni las boletas. Solo
actualiza esas dos columnas de snapshot.

USO (PowerShell en Windows):
  $env:DATABASE_URL="postgresql://...proxy.rlwy.net:PUERTO/railway"   # DATABASE_PUBLIC_URL real
  py -3.12 resync_liquidaciones_equiv.py            # DRY-RUN: solo muestra que cambiaria
  py -3.12 resync_liquidaciones_equiv.py --apply    # aplica los cambios (commit)

Tambien podes pasar la URL como argumento. Es seguro: sin --apply NO escribe nada.
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
    apply = False
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    for a in sys.argv[1:]:
        if a in ("--apply", "-a"):
            apply = True
        elif a.startswith("postgres://") or a.startswith("postgresql://"):
            db_url = a
    return apply, db_url


def fnum(x):
    f = float(x or 0)
    return str(int(round(f))) if abs(f - round(f)) < 1e-9 else f"{f:.2f}"


def es_contado(modalidad, ne, ne2):
    m = (modalidad or "").strip().lower()
    if m in ("contado", "contado2"):
        return True
    if m == "cuotas":
        return False
    # Boletas viejas sin modalidad: inferir por numero especial asignado
    return (ne is not None) or (ne2 is not None)


def main():
    apply, db_url = get_args()
    if not db_url:
        print("No encontre la URL de la base. Defini DATABASE_URL, por ejemplo:")
        print('  $env:DATABASE_URL="postgresql://postgres:PASS@xxxxx.proxy.rlwy.net:PUERTO/railway"')
        sys.exit(1)
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    if "..." in db_url:
        print("La URL todavia tiene '...' (es el ejemplo). Pone tu DATABASE_PUBLIC_URL real de Railway.")
        sys.exit(1)

    try:
        conn = psycopg2.connect(db_url)
    except Exception as e:
        print(f"No me pude conectar: {e}")
        sys.exit(1)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    modo = "APLICAR (se va a escribir en la base)" if apply else "DRY-RUN (no escribe nada)"
    print("=" * 70)
    print(f"  RE-SYNC de cuotas_equiv / contados_equiv  —  {modo}")
    print("=" * 70)

    # ── Liquidaciones actuales ──────────────────────────────────────────────
    cur.execute("""
        SELECT lv.id, lv.vendedor_id, v.nombre AS vendedor,
               COALESCE(lv.cuotas_equiv, 0)   AS cuotas_old,
               COALESCE(lv.contados_equiv, 0) AS contados_old
        FROM liquidaciones_vendedor lv
        LEFT JOIN vendedores v ON v.id = lv.vendedor_id
        ORDER BY v.nombre, lv.id
    """)
    liqs = {r["id"]: dict(r, cuotas_new=0.0, contados_new=0.0) for r in cur.fetchall()}

    # ── Boletas que cuelgan de alguna liquidacion ──────────────────────────
    cur.execute("""
        SELECT b.liquidacion_vendedor_id AS liq_id,
               b.modalidad_liquidacion,
               b.numero_especial, b.numero_especial_2,
               COALESCE(t.multiplicador, 1) AS mult
        FROM boletas b
        LEFT JOIN taloneras t ON t.id = b.talonera_id
        WHERE b.liquidacion_vendedor_id IS NOT NULL
    """)
    for r in cur.fetchall():
        liq = liqs.get(r["liq_id"])
        if not liq:
            continue
        mult = float(r["mult"] or 1)
        if es_contado(r["modalidad_liquidacion"], r["numero_especial"], r["numero_especial_2"]):
            liq["contados_new"] += mult
        else:
            liq["cuotas_new"] += mult

    # ── Detectar y clasificar cambios ───────────────────────────────────────
    # drops  = el total bajó (boletas sacadas/liberadas) → es el drift, SE APLICA.
    # splits = el total NO cambia pero cambia el reparto cuotas/contado → NO se toca
    #          (se respeta la clasificación original del operador, más confiable
    #          que inferir contado por numero_especial en boletas viejas).
    # subes  = el total subió → NO se toca, se reporta para revisar a mano.
    EPS = 1e-6
    drops, splits, subes = [], [], []
    for liq in liqs.values():
        old_total = float(liq["cuotas_old"]) + float(liq["contados_old"])
        new_total = liq["cuotas_new"] + liq["contados_new"]
        d_cu = liq["cuotas_new"]   - float(liq["cuotas_old"])
        d_co = liq["contados_new"] - float(liq["contados_old"])
        if abs(d_cu) <= EPS and abs(d_co) <= EPS:
            continue
        if new_total < old_total - EPS:
            drops.append(liq)
        elif new_total > old_total + EPS:
            subes.append(liq)
        else:
            splits.append(liq)

    def _tabla(titulo, lista):
        print(f"\n{titulo}: {len(lista)}")
        if not lista:
            return
        print(f"  {'LIQ':>5} {'VENDEDOR':<10} {'CUOTAS (old->new)':>22} {'CONTADOS (old->new)':>22}")
        print("  " + "-" * 62)
        for liq in lista:
            print(f"  {liq['id']:>5} {(liq['vendedor'] or '?'):<10} "
                  f"{fnum(liq['cuotas_old']):>9} -> {fnum(liq['cuotas_new']):<9} "
                  f"{fnum(liq['contados_old']):>10} -> {fnum(liq['contados_new']):<9}")

    _tabla("DRIFT a corregir — total baja (SE APLICA)", drops)
    _tabla("Solo cambia el reparto, total igual (NO se toca)", splits)
    _tabla("Total sube — revisar a mano (NO se toca)", subes)
    if not drops and not splits and not subes:
        print("\nNo hay nada que re-sincronizar: todos los snapshots ya coinciden.")

    # ── Resumen por vendedor: ANTES vs DESPUES (solo aplicando los drops) ────
    drop_ids = {liq["id"] for liq in drops}
    print("\nResumen por vendedor (Total liquidados, como lo muestra el dashboard):")
    print(f"  {'VENDEDOR':<10} {'ANTES':>8} {'DESPUES':>8} {'DIF':>6}")
    print("  " + "-" * 34)
    by_vend = {}
    for liq in liqs.values():
        v = liq["vendedor"] or "?"
        acc = by_vend.setdefault(v, {"cu_o": 0.0, "co_o": 0.0, "cu_n": 0.0, "co_n": 0.0})
        acc["cu_o"] += float(liq["cuotas_old"]);   acc["co_o"] += float(liq["contados_old"])
        # "después" = nuevo solo si es un drop que se aplica; si no, queda el viejo
        if liq["id"] in drop_ids:
            acc["cu_n"] += liq["cuotas_new"];      acc["co_n"] += liq["contados_new"]
        else:
            acc["cu_n"] += float(liq["cuotas_old"]); acc["co_n"] += float(liq["contados_old"])
    tot_o = tot_n = 0
    for v in sorted(by_vend):
        a = by_vend[v]
        antes   = int(round(a["cu_o"])) + int(round(a["co_o"]))
        despues = int(round(a["cu_n"])) + int(round(a["co_n"]))
        tot_o += antes; tot_n += despues
        dif = despues - antes
        print(f"  {v:<10} {antes:>8} {despues:>8} {dif:>+6}")
    print("  " + "-" * 34)
    print(f"  {'TOTAL':<10} {tot_o:>8} {tot_n:>8} {tot_n - tot_o:>+6}")

    # ── Aplicar (solo los drops) ────────────────────────────────────────────
    if apply and drops:
        for liq in drops:
            cur.execute(
                "UPDATE liquidaciones_vendedor SET cuotas_equiv = %s, contados_equiv = %s WHERE id = %s",
                (round(liq["cuotas_new"], 6), round(liq["contados_new"], 6), liq["id"]),
            )
        conn.commit()
        print(f"\n[OK] Aplicado: {len(drops)} liquidacion(es) corregida(s) por drift y commit hecho.")
        if splits or subes:
            print(f"     (Se dejaron sin tocar {len(splits)} de reparto y {len(subes)} que suben.)")
    elif drops:
        print("\n(DRY-RUN) No se escribio nada. Volve a correr con  --apply  para aplicar los drops.")

    print("=" * 70)
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
