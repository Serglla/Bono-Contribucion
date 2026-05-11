"""
crear_liquidacion_ariel_10052026.py
====================================

Recrea la liquidacion de ARIEL del 10/05/2026 enlazando 27 boletas
(53 ponderadas) que quedaron sin liquidacion_vendedor_id despues
de borrar el historial para corregir el registro.

- Detecta DATABASE_URL para correr contra Railway/PostgreSQL.
- Si no esta seteada, usa SQLite local (app.db) via app.database.
- Aplica las migraciones de startup automaticamente.
- No toca Boleta.vendedor_id ni condicion: solo crea la cabecera
  LiquidacionVendedor y setea liquidacion_vendedor_id en cada boleta.

USO
---
Desde la carpeta bono-app/:

    # Local (SQLite):
    py -3.12 crear_liquidacion_ariel_10052026.py

    # Railway (PostgreSQL):
    $env:DATABASE_URL="postgresql://USUARIO:PASSWORD@HOST:PORT/DBNAME"
    py -3.12 crear_liquidacion_ariel_10052026.py

Flags opcionales:
    --yes        No pide confirmacion (commit directo).
    --dry-run    Calcula y muestra resumen, no escribe nada.
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import or_
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


VENDEDOR_LIQUIDADOR = "ARIEL"
FECHA_LIQUIDACION = datetime(2026, 5, 10, 12, 0, 0)
OBSERVACION = (
    "Reconstruida 10/05/2026 - historial regenerado tras borrado "
    "para corregir registro; las boletas ya tenian socio cargado."
)

COMISION_CUOTAS_PCT = 5.0
COMISION_CONTADOS_PCT = 30.0

BOLETAS = [
    (942,  "PATA 1", "ARIEL"),
    (972,  "PATA 1", "ARIEL"),
    (975,  "PATA 1", "ARIEL"),
    (976,  "PATA 1", "ARIEL"),
    (977,  "PATA 1", "ARIEL"),
    (978,  "PATA 1", "ARIEL"),
    (979,  "PATA 1", "ARIEL"),
    (980,  "PATA 1", "ARIEL"),
    (4508, "PATA 2", "ARIEL"),
    (4511, "PATA 2", "ARIEL"),
    (8050, "PATA 8", "ARIEL"),
    (8051, "PATA 8", "ARIEL"),
    (8052, "PATA 8", "ARIEL"),
    (733,  "PATA 1", "PAJARO"),
    (735,  "PATA 1", "PAJARO"),
    (941,  "PATA 1", "PAJARO"),
    (943,  "PATA 1", "PAJARO"),
    (948,  "PATA 1", "PAJARO"),
    (949,  "PATA 1", "PAJARO"),
    (7091, "PATA 4", "PAJARO"),
    (931,  "PATA 1", "VICTOR"),
    (933,  "PATA 1", "VICTOR"),
    (936,  "PATA 1", "VICTOR"),
    (937,  "PATA 1", "VICTOR"),
    (938,  "PATA 1", "VICTOR"),
    (939,  "PATA 1", "VICTOR"),
    (940,  "PATA 1", "VICTOR"),
]


def _print_db_target():
    url = os.environ.get("DATABASE_URL")
    if url:
        safe = url
        if "@" in url and "://" in url:
            scheme, rest = url.split("://", 1)
            if "@" in rest:
                creds, host = rest.split("@", 1)
                user = creds.split(":", 1)[0]
                safe = f"{scheme}://{user}:***@{host}"
        print(f"[INFO] DATABASE_URL detectada -> {safe}")
    else:
        print("[INFO] Sin DATABASE_URL -> usando SQLite local (app.db)")


def _resolve_vendedor(db, nombre):
    return db.query(models.Vendedor).filter(
        models.Vendedor.nombre.ilike(nombre)
    ).first()


def _resolve_talonera_ids(db, nombre):
    ts = db.query(models.Talonera).filter(
        models.Talonera.nombre == nombre,
        or_(models.Talonera.tipo == "COMUN", models.Talonera.tipo.is_(None)),
    ).all()
    return [t.id for t in ts]


def main(argv):
    dry_run = "--dry-run" in argv
    auto_yes = "--yes" in argv

    _print_db_target()
    _aplicar_migraciones()
    db = SessionLocal()
    try:
        ariel = _resolve_vendedor(db, VENDEDOR_LIQUIDADOR)
        if not ariel:
            print(f"[ERROR] Vendedor '{VENDEDOR_LIQUIDADOR}' no encontrado.")
            return 1
        print(f"[OK]   Vendedor liquidador: {ariel.nombre} (id={ariel.id}, jefe={ariel.es_jefe_equipo})")

        vendedores_actuales = {}
        for nom in sorted({v for _, _, v in BOLETAS}):
            obj = _resolve_vendedor(db, nom)
            if not obj:
                print(f"[ERROR] Vendedor '{nom}' no existe.")
                return 1
            vendedores_actuales[nom.upper()] = obj
            print(f"[OK]   Vendedor actual: {obj.nombre} (id={obj.id})")

        taloneras_ids = {}
        for nom in sorted({n for _, n, _ in BOLETAS}):
            ids = _resolve_talonera_ids(db, nom)
            if not ids:
                print(f"[ERROR] Talonera '{nom}' no encontrada.")
                return 1
            taloneras_ids[nom] = ids
            tal = db.query(models.Talonera).get(ids[0])
            print(f"[OK]   Talonera {nom!r}: id={ids[0]}  mult={tal.multiplicador}  valor_cuota={tal.valor_cuota}")

        resueltas = []
        errores = []
        warnings = []
        for numero, tal_nombre, vend_esperado in BOLETAS:
            tal_ids = taloneras_ids[tal_nombre]
            b = db.query(models.Boleta).filter(
                models.Boleta.numero_principal == numero,
                models.Boleta.talonera_id.in_(tal_ids),
            ).first()
            if not b:
                errores.append(f"Boleta {numero:04d} en {tal_nombre} no encontrada.")
                continue
            if b.liquidacion_vendedor_id is not None:
                errores.append(
                    f"Boleta {numero:04d} ({tal_nombre}) YA tiene liquidacion_vendedor_id={b.liquidacion_vendedor_id}."
                )
                continue
            if b.comprador_id is None:
                warnings.append(f"Boleta {numero:04d} ({tal_nombre}) no tiene comprador cargado.")
            esperado = vendedores_actuales[vend_esperado.upper()].id
            if b.vendedor_id is None:
                warnings.append(
                    f"Boleta {numero:04d} ({tal_nombre}) sin vendedor_id (esperado {vend_esperado})."
                )
            elif b.vendedor_id != esperado:
                actual = db.query(models.Vendedor).get(b.vendedor_id)
                nom_actual = actual.nombre if actual else b.vendedor_id
                warnings.append(
                    f"Boleta {numero:04d} ({tal_nombre}) tiene vendedor {nom_actual} pero se esperaba {vend_esperado}."
                )
            resueltas.append((b, vend_esperado, b.talonera))

        if errores:
            print("\n[ABORT] Errores criticos:")
            for e in errores:
                print(f"   - {e}")
            return 2

        if warnings:
            print("\n[WARN] Avisos (no bloquean):")
            for w in warnings:
                print(f"   - {w}")

        cuotas_vendidas = len(resueltas)
        cuotas_equiv = sum(int(t.multiplicador or 1) for _, _, t in resueltas)
        cuota_1_total = round(sum(float(t.valor_cuota or 0.0) for _, _, t in resueltas), 2)
        monto_cuotas = cuota_1_total

        pond_por_vend = Counter()
        bol_por_vend = Counter()
        for _, vend, t in resueltas:
            pond_por_vend[vend] += int(t.multiplicador or 1)
            bol_por_vend[vend] += 1

        print()
        print("=" * 60)
        print("  LIQUIDACION A CREAR")
        print("=" * 60)
        print(f"  Vendedor:                  {ariel.nombre}")
        print(f"  Fecha:                     {FECHA_LIQUIDACION:%d/%m/%Y %H:%M}")
        print(f"  Boletas literales:         {cuotas_vendidas}")
        print(f"  Cuotas equivalentes:       {cuotas_equiv}  (ponderado por PATA)")
        print(f"  Cuota 1 total:             ${cuota_1_total:,.2f}")
        print(f"  Monto cuotas:              ${monto_cuotas:,.2f}")
        print(f"  Comision cuotas %:         {COMISION_CUOTAS_PCT}%  (no aplica, cuota 1)")
        print(f"  Contados:                  0")
        print(f"  Total comision (al vend):  $0.00")
        print(f"  Total a rendir:            $0.00")
        print(f"  Observacion:")
        print(f"     {OBSERVACION}")
        print()
        print("  Desglose por vendedor actual:")
        for v in ("ARIEL", "PAJARO", "VICTOR"):
            if v in bol_por_vend:
                print(f"     - {v:8s}  {bol_por_vend[v]:>2d} boleta(s)  /  {pond_por_vend[v]:>2d} ponderado")
        print("=" * 60)

        if dry_run:
            print("\n[DRY-RUN] No se modifica la base de datos.")
            return 0

        if not auto_yes:
            resp = input("\n¿Confirmas crear la liquidacion y enlazar las boletas? [s/N]: ").strip().lower()
            if resp not in ("s", "si", "y", "yes"):
                print("Cancelado por el usuario.")
                return 0

        liq = models.LiquidacionVendedor(
            vendedor_id=ariel.id,
            fecha=FECHA_LIQUIDACION,
            cuotas_vendidas=cuotas_vendidas,
            cuotas_equiv=cuotas_equiv,
            cuota_1_total=cuota_1_total,
            monto_cuotas=monto_cuotas,
            comision_cuotas_pct=COMISION_CUOTAS_PCT,
            comision_cuotas=0.0,
            contados_vendidos=0,
            monto_contados=0.0,
            comision_contados_pct=COMISION_CONTADOS_PCT,
            comision_contados=0.0,
            cuotas_extras_cantidad=0,
            cuotas_extras_valor=0.0,
            cuotas_extras_monto=0.0,
            comision_cuotas_extras=0.0,
            total_comision=0.0,
            total_a_rendir=0.0,
            observacion=OBSERVACION,
        )
        db.add(liq)
        db.flush()
        print(f"\n[OK] LiquidacionVendedor creada: id={liq.id}")

        for b, _, _ in resueltas:
            b.liquidacion_vendedor_id = liq.id
        db.commit()
        print(f"[OK] {cuotas_vendidas} boletas enlazadas a la liquidacion {liq.id}.")
        print(f"\nListo. Verificar en /vendedores/{ariel.id}/detalle (tab Liquidaciones).")
        return 0

    except Exception as e:
        db.rollback()
        print(f"\n[ERROR] {type(e).__name__}: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
