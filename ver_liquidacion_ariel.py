"""
ver_liquidacion_ariel.py
=========================
Mini script de SOLO LECTURA para verificar si la liquidacion de Ariel
del 10/05/2026 se creo correctamente y muestra que boletas tiene enlazadas.

Funciona contra la misma DB que crear_liquidacion_ariel_10052026.py:
- Sin DATABASE_URL -> SQLite local (app.db)
- Con DATABASE_URL exportada -> Railway / PostgreSQL

Aplica automaticamente las migraciones de la app (es_jefe_equipo,
cuotas_equiv, liquidacion_vendedor_id, etc.) antes de hacer queries.

USO
---
Desde la carpeta bono-app/:

    # Local:
    py -3.12 ver_liquidacion_ariel.py

    # Railway:
    $env:DATABASE_URL="postgresql://..."
    py -3.12 ver_liquidacion_ariel.py
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import SessionLocal
from app import models


def _aplicar_migraciones():
    try:
        from app.main import create_default_admin
        create_default_admin()
        print("[OK]   Migraciones aplicadas (startup ejecutado).")
    except Exception as e:
        print(f"[WARN] No se pudo aplicar migraciones automaticamente: {e}")
        print("       Levanta la app manualmente una vez para aplicarlas:")
        print("         py -3.12 -m uvicorn app.main:app --reload")


def _db_target():
    url = os.environ.get("DATABASE_URL")
    if not url:
        return "SQLite local (app.db)"
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        if "@" in rest:
            creds, host = rest.split("@", 1)
            user = creds.split(":", 1)[0]
            return f"{scheme}://{user}:***@{host}"
    return "(DATABASE_URL seteada)"


def main():
    print(f"[INFO] DB: {_db_target()}")
    _aplicar_migraciones()
    db = SessionLocal()
    try:
        ariel = db.query(models.Vendedor).filter(
            models.Vendedor.nombre.ilike("ARIEL")
        ).first()
        if not ariel:
            print("[ERROR] Vendedor 'ARIEL' no existe en esta DB.")
            return 1
        print(f"[OK]   Vendedor: {ariel.nombre} (id={ariel.id})")

        liqs = db.query(models.LiquidacionVendedor).filter(
            models.LiquidacionVendedor.vendedor_id == ariel.id,
        ).order_by(models.LiquidacionVendedor.id.desc()).all()

        print(f"\n[INFO] {len(liqs)} liquidacion(es) totales de Ariel:")
        for liq in liqs:
            fecha = liq.fecha.strftime("%d/%m/%Y %H:%M") if liq.fecha else "(sin fecha)"
            n_boletas = db.query(models.Boleta).filter(
                models.Boleta.liquidacion_vendedor_id == liq.id
            ).count()
            print(
                f"   - id={liq.id}  fecha={fecha}  "
                f"cuotas_equiv={liq.cuotas_equiv}  "
                f"cuotas_vendidas={liq.cuotas_vendidas}  "
                f"boletas_enlazadas={n_boletas}"
            )
            if liq.observacion:
                obs = liq.observacion
                if len(obs) > 90:
                    obs = obs[:90] + "..."
                print(f"     obs: {obs}")

        target = [
            liq for liq in liqs
            if liq.fecha
            and liq.fecha.year == 2026
            and liq.fecha.month == 5
            and liq.fecha.day == 10
        ]
        if not target:
            print("\n[WARN] No se encontro liquidacion de Ariel del 10/05/2026.")
            print("       El script crear_liquidacion_ariel_10052026.py NO corrio contra esta DB.")
            return 2

        liq = target[0]
        print()
        print("=" * 60)
        print(f"  LIQUIDACION DEL 10/05/2026 (id={liq.id})")
        print("=" * 60)
        print(f"  cuotas_vendidas:    {liq.cuotas_vendidas}")
        print(f"  cuotas_equiv:       {liq.cuotas_equiv}")
        print(f"  cuota_1_total:      ${float(liq.cuota_1_total or 0):,.2f}")
        print(f"  contados_vendidos:  {liq.contados_vendidos}")
        print(f"  total_comision:     ${float(liq.total_comision or 0):,.2f}")
        rendir = float(getattr(liq, "total_a_rendir", 0) or 0)
        print(f"  total_a_rendir:     ${rendir:,.2f}")

        boletas = db.query(models.Boleta).filter(
            models.Boleta.liquidacion_vendedor_id == liq.id
        ).all()

        print(f"\n  Boletas enlazadas: {len(boletas)}")
        if not boletas:
            print("  [WARN] La liquidacion existe pero no tiene boletas enlazadas.")
            return 3

        por_vendedor = Counter()
        ponderado_por_vendedor = Counter()
        por_grupo = []
        for b in boletas:
            vend = b.vendedor.nombre if b.vendedor else "(sin vendedor)"
            tal = b.talonera.nombre if b.talonera else "(sin talonera)"
            mult = int(b.talonera.multiplicador or 1) if b.talonera else 1
            por_vendedor[vend] += 1
            ponderado_por_vendedor[vend] += mult
            por_grupo.append((vend, tal, b.numero_principal, mult))

        print("\n  Por vendedor actual:")
        for v in sorted(por_vendedor):
            n = por_vendedor[v]
            p = ponderado_por_vendedor[v]
            print(f"     - {v:10s}  {n:>3d} boleta(s)  /  {p:>3d} ponderado")
        total_b = sum(por_vendedor.values())
        total_p = sum(ponderado_por_vendedor.values())
        print(f"     {'-' * 40}")
        print(f"     {'TOTAL':10s}  {total_b:>3d} boleta(s)  /  {total_p:>3d} ponderado")

        print("\n  Detalle (numero, talonera, vendedor):")
        for vend, tal, num, mult in sorted(
            por_grupo, key=lambda x: (x[0], x[1], x[2])
        ):
            print(f"     {num:04d}  {tal:8s}  x{mult}  {vend}")

        print()
        print("[OK] Verificacion finalizada.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
