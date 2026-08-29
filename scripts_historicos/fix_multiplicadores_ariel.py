"""
Diagnóstico y corrección de multiplicadores de taloneras + recálculo
de contados_equiv para la última liquidación de Ariel.

USO:
    # 1) Diagnóstico (NO modifica nada):
    python fix_multiplicadores_ariel.py --dry-run

    # 2) Aplicar las correcciones (dentro de transacción, hace COMMIT al final):
    python fix_multiplicadores_ariel.py --fix

Reglas que aplica:
    - PATA 0 → multiplicador 0.67
    - PATA 1 → multiplicador 1
    - PATA 2 → multiplicador 2
    - PATA 3 → multiplicador 3
    - PATA N → multiplicador N (para nombres tipo "PATA <n>")

    Luego recalcula la ÚLTIMA liquidación de Ariel:
        contados_equiv = SUM(talonera.multiplicador) de las boletas marcadas al contado
        cuotas_equiv   = SUM(talonera.multiplicador) de las boletas de cuotas

    Usa SQLAlchemy + los modelos de la app para no romper nada de la lógica.
"""

import os
import re
import sys
import argparse
from decimal import Decimal

# IMPORTANTE: cambiar a False y poner la URL en .env (DATABASE_URL=...) si preferís.
DATABASE_URL_DEFAULT = "postgresql://postgres:mLnPatDuzRYNGodaWTZBlygTEgWzqhpQ@tramway.proxy.rlwy.net:57548/railway"

os.environ.setdefault("DATABASE_URL", DATABASE_URL_DEFAULT)

# Importa la app (asegurate de correr el script desde la raíz del repo, con el venv activo)
from app.database import SessionLocal, engine
from app import models


PATA_RE = re.compile(r"PATA\s*([0-9]+)", re.IGNORECASE)


def multiplicador_esperado(nombre: str):
    """Devuelve el multiplicador esperado para un nombre tipo 'PATA N'.
    PATA 0 = 0.67 (especial), PATA N = N para N >= 1.
    Devuelve None si no matchea el patrón (no la toca)."""
    if not nombre:
        return None
    m = PATA_RE.search(nombre)
    if not m:
        return None
    n = int(m.group(1))
    if n == 0:
        return 0.67
    return float(n)


def diagnosticar(db):
    print("=" * 70)
    print("DIAGNÓSTICO")
    print("=" * 70)

    taloneras = db.query(models.Talonera).order_by(models.Talonera.id).all()
    print(f"\n[Taloneras: {len(taloneras)}]")
    print(f"{'ID':<5} {'NOMBRE':<22} {'MULT_ACT':<10} {'MULT_ESP':<10} {'ESTADO':<10}")
    print("-" * 60)
    cambios_taloneras = []
    for t in taloneras:
        esp = multiplicador_esperado(t.nombre)
        act = float(t.multiplicador or 1.0)
        if esp is None:
            estado = "N/A"
        elif abs(act - esp) < 1e-6:
            estado = "OK"
        else:
            estado = "CAMBIA"
            cambios_taloneras.append((t, act, esp))
        print(f"{t.id:<5} {(t.nombre or ''):<22} {act:<10} {str(esp):<10} {estado}")

    # Vendedor Ariel
    ariel = (
        db.query(models.Vendedor)
        .filter(models.Vendedor.nombre.ilike("%ariel%"))
        .first()
    )
    if not ariel:
        print("\n[!] No se encontró vendedor 'ariel'.")
        return cambios_taloneras, None

    print(f"\n[Vendedor]  id={ariel.id}  nombre={ariel.nombre}")

    # Última liquidación
    liq = (
        db.query(models.LiquidacionVendedor)
        .filter(models.LiquidacionVendedor.vendedor_id == ariel.id)
        .order_by(models.LiquidacionVendedor.fecha.desc())
        .first()
    )
    if not liq:
        print("[!] Ariel no tiene liquidaciones.")
        return cambios_taloneras, None

    print(
        f"[Última liq] id={liq.id}  fecha={liq.fecha}  "
        f"cuotas_vend={liq.cuotas_vendidas}  cuotas_equiv={liq.cuotas_equiv}  "
        f"contados_vend={liq.contados_vendidos}  contados_equiv={liq.contados_equiv}"
    )

    # Boletas de esa liquidación
    boletas = (
        db.query(models.Boleta)
        .filter(models.Boleta.liquidacion_vendedor_id == liq.id)
        .all()
    )
    print(f"\n[Boletas asociadas a la liq {liq.id}: {len(boletas)}]")
    print(
        f"{'BID':<6} {'PATA':<14} {'MULT':<6} {'CONTADO?':<10} "
        f"{'NUM_PRINC':<10} {'NUM_ESP':<10}"
    )
    print("-" * 60)
    cuotas_mult_sum = 0.0
    contados_mult_sum = 0.0
    cuotas_n = 0
    contados_n = 0
    for b in boletas:
        pata = b.talonera.nombre if b.talonera else "?"
        mult = float((b.talonera.multiplicador or 1.0) if b.talonera else 1.0)
        es_contado = (b.numero_especial is not None) or (b.numero_especial_2 is not None)
        print(
            f"{b.id:<6} {pata:<14} {mult:<6} {('SI' if es_contado else 'no'):<10} "
            f"{str(b.numero_principal):<10} {str(b.numero_especial):<10}"
        )
        if es_contado:
            contados_n += 1
            contados_mult_sum += mult
        else:
            cuotas_n += 1
            cuotas_mult_sum += mult

    # También recalcular con los multiplicadores ESPERADOS (post-fix)
    cuotas_mult_sum_esp = 0.0
    contados_mult_sum_esp = 0.0
    for b in boletas:
        pata = b.talonera.nombre if b.talonera else "?"
        esp = multiplicador_esperado(pata)
        mult_esp = esp if esp is not None else float((b.talonera.multiplicador or 1.0) if b.talonera else 1.0)
        es_contado = (b.numero_especial is not None) or (b.numero_especial_2 is not None)
        if es_contado:
            contados_mult_sum_esp += mult_esp
        else:
            cuotas_mult_sum_esp += mult_esp

    print()
    print("[Resumen recálculo]")
    print(f"  cuotas (n={cuotas_n}):   actual_sum={cuotas_mult_sum:.2f}  "
          f"esperado_sum={cuotas_mult_sum_esp:.2f}  guardado={liq.cuotas_equiv}")
    print(f"  contados (n={contados_n}): actual_sum={contados_mult_sum:.2f}  "
          f"esperado_sum={contados_mult_sum_esp:.2f}  guardado={liq.contados_equiv}")

    return cambios_taloneras, (liq, cuotas_mult_sum_esp, contados_mult_sum_esp)


def aplicar_fix(db, cambios_taloneras, liq_info):
    print("\n" + "=" * 70)
    print("APLICANDO CORRECCIONES (transacción)")
    print("=" * 70)

    # 1. Multiplicadores
    for t, act, esp in cambios_taloneras:
        print(f"  · Talonera id={t.id} '{t.nombre}': {act} → {esp}")
        t.multiplicador = esp

    # 2. Recalcular la liq de Ariel
    if liq_info:
        liq, cuotas_eq, contados_eq = liq_info
        print(
            f"\n  · Liquidación id={liq.id} de Ariel: "
            f"cuotas_equiv {liq.cuotas_equiv} → {cuotas_eq:.2f}  "
            f"contados_equiv {liq.contados_equiv} → {contados_eq:.2f}"
        )
        liq.cuotas_equiv = cuotas_eq
        liq.contados_equiv = contados_eq

    db.commit()
    print("\n✓ COMMIT realizado.")


def main():
    ap = argparse.ArgumentParser()
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--dry-run", action="store_true", help="Solo diagnostica, no toca DB.")
    grp.add_argument("--fix", action="store_true", help="Aplica las correcciones.")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        cambios_taloneras, liq_info = diagnosticar(db)

        if args.dry_run:
            print("\n[dry-run] No se modificó nada.")
            return

        if not cambios_taloneras and (liq_info is None):
            print("\nNo hay cambios para aplicar.")
            return

        print("\nSe aplicarán los cambios anteriores. Presioná ENTER para confirmar o Ctrl+C para abortar.")
        try:
            input()
        except KeyboardInterrupt:
            print("Abortado.")
            return

        aplicar_fix(db, cambios_taloneras, liq_info)

        # Verificación post-commit
        print("\n" + "=" * 70)
        print("VERIFICACIÓN POST-COMMIT")
        print("=" * 70)
        diagnosticar(db)

    finally:
        db.close()


if __name__ == "__main__":
    main()
