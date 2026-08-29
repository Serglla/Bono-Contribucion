#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_cuota2_institucion_8689_8694.py
===================================
Boletas 8689 (CHESINI FLORENCIA) y 8694 (RODRIGUEZ HERNAN ROBERTO), planilla
de MABEL: la cobradora no las encontro, la institucion mando a alguien que
cobro la cuota 2 y se cargo manualmente (cuotas_pagadas=2). Pero la carga
manual no toco cuotas_anticipadas ni historial_cuotas, entonces:
  - la planilla de MABEL no muestra la marca de la cuota 2, y
  - la proxima liquidacion recalcularia cuotas_pagadas = anticipadas +
    len(historial) y las volveria a dejar en 1/12 (se pierde la cuota).

FIX (decidido con Sergio 01/07/2026): cuota cobrada FUERA de la cobranza
=> se marca con X negra, igual que las anticipadas de venta. O sea:
  cuotas_anticipadas = 2   (y cuotas_pagadas queda/asegura en 2)
NO se toca historial_cuotas ni la liquidacion de junio de MABEL (esa plata
no la rindio ella).

USO (PowerShell en Windows):
  $env:DATABASE_URL="postgresql://...proxy.rlwy.net:PUERTO/railway"   # DATABASE_PUBLIC_URL
  py -3.12 fix_cuota2_institucion_8689_8694.py            # DRY-RUN
  py -3.12 fix_cuota2_institucion_8689_8694.py --apply    # aplica (commit)
"""

import os
import sys

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("Falta psycopg2. Instalalo con:  py -3.12 -m pip install psycopg2-binary")
    sys.exit(1)

NUMEROS = [8689, 8694]
ESPERADOS = {8689: "CHESINI", 8694: "RODRIGUEZ"}


def get_args():
    apply = False
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    for a in sys.argv[1:]:
        if a in ("--apply", "-a"):
            apply = True
        elif a.startswith("postgres://") or a.startswith("postgresql://"):
            db_url = a
    return apply, db_url


def main():
    apply, db_url = get_args()
    if not db_url:
        print("Falta DATABASE_URL (usar la publica *.proxy.rlwy.net).")
        sys.exit(1)
    if "railway.internal" in db_url:
        print("Esa es la URL interna de Railway; desde la PC usa DATABASE_PUBLIC_URL (*.proxy.rlwy.net).")
        sys.exit(1)

    conn = psycopg2.connect(db_url)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT b.id, b.numero_principal, b.cuotas_pagadas, b.cuotas_anticipadas,
               b.cuotas_pactadas, b.historial_cuotas, b.condicion,
               c.apellido_nombre AS socio, cb.nombre AS cobrador
        FROM boletas b
        LEFT JOIN compradores c  ON c.id  = b.comprador_id
        LEFT JOIN cobradores  cb ON cb.id = b.cobrador_id
        WHERE b.numero_principal = ANY(%s)
        ORDER BY b.numero_principal
    """, (NUMEROS,))
    rows = cur.fetchall()

    print(f"{'DRY-RUN (no escribe nada)' if not apply else '*** APPLY ***'}\n")
    a_tocar = []
    for r in rows:
        print(f"  Boleta id={r['id']}  N° {r['numero_principal']:04d}  socio={r['socio']}  "
              f"cobrador={r['cobrador']}  pagadas={r['cuotas_pagadas']}  "
              f"anticipadas={r['cuotas_anticipadas']}  historial={r['historial_cuotas']}")
        esperado = ESPERADOS[r["numero_principal"]]
        if not r["socio"] or esperado not in r["socio"].upper():
            print(f"    -> SKIP: socio no coincide con {esperado} (¿numero repetido en otra talonera?)")
            continue
        if (r["cuotas_anticipadas"] or 1) >= 2:
            print("    -> SKIP: anticipadas ya >= 2 (ya corregida)")
            continue
        a_tocar.append(r)

    if not a_tocar:
        print("\nNada para corregir.")
        conn.close()
        return

    print(f"\nSe corrigen {len(a_tocar)} boleta(s): anticipadas -> 2, pagadas -> max(pagadas, 2)")
    if apply:
        for r in a_tocar:
            cur.execute("""
                UPDATE boletas
                SET cuotas_anticipadas = 2,
                    cuotas_pagadas = GREATEST(COALESCE(cuotas_pagadas, 0), 2)
                WHERE id = %s
            """, (r["id"],))
        conn.commit()
        print("COMMIT hecho. Verifica la planilla de MABEL: cuota 2 con X negra.")
    else:
        print("Dry-run: corre con --apply para aplicar.")
    conn.close()


if __name__ == "__main__":
    main()
