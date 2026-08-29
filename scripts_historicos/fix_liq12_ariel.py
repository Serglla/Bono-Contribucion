"""
Fix puntual: setear contados_equiv = 8 en la liquidacion 12 de Ariel.

Justificación (verificada con --dry-run del script anterior):
  - 24 boletas asociadas a la liq, suman multiplicadores = 46.
  - cuotas_equiv guardado = 38 (correcto).
  - contados_equiv guardado = 3 (incorrecto, quedó como conteo crudo).
  - Debe ser 46 - 38 = 8  (= 1 PATA 2 + 2 PATA 3, según indicó el vendedor).

USO:
    python fix_liq12_ariel.py --dry-run   # solo muestra antes/después
    python fix_liq12_ariel.py --apply     # aplica el UPDATE
"""
import os, sys, argparse

DATABASE_URL_DEFAULT = "postgresql://postgres:mLnPatDuzRYNGodaWTZBlygTEgWzqhpQ@tramway.proxy.rlwy.net:57548/railway"
os.environ.setdefault("DATABASE_URL", DATABASE_URL_DEFAULT)

from app.database import SessionLocal
from app import models

LIQ_ID = 12
CONTADOS_EQUIV_NUEVO = 8.0

def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        liq = db.query(models.LiquidacionVendedor).get(LIQ_ID)
        if not liq:
            print(f"[!] No existe liquidacion id={LIQ_ID}")
            return

        print(f"Liquidacion id={liq.id}  vendedor_id={liq.vendedor_id}  fecha={liq.fecha}")
        print(f"  ANTES:  cuotas_equiv={liq.cuotas_equiv}  contados_equiv={liq.contados_equiv}")
        print(f"  CAMBIO: contados_equiv  {liq.contados_equiv}  ->  {CONTADOS_EQUIV_NUEVO}")

        if args.dry_run:
            print("\n[dry-run] No se modificó nada.")
            return

        liq.contados_equiv = CONTADOS_EQUIV_NUEVO
        db.commit()
        db.refresh(liq)
        print(f"  DESPUES: cuotas_equiv={liq.cuotas_equiv}  contados_equiv={liq.contados_equiv}")
        print("\n✓ COMMIT aplicado.")
    finally:
        db.close()

if __name__ == "__main__":
    main()
