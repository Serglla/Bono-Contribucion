"""
rearmar_liquidaciones_10052026.py
=================================

Reconstruye TODAS las liquidaciones de vendedores con fecha 10/05/2026
para que coincidan con lo realmente vendido (lo que se ve en la seccion
Socios), reemplazando las liquidaciones viejas que quedaron desfasadas.

Que hace
--------
1. BORRA todas las liquidaciones de vendedor existentes (cabeceras
   LiquidacionVendedor + sus LiquidacionContadoItem por cascade) y limpia
   el campo Boleta.liquidacion_vendedor_id de todas las boletas.
2. Agrupa las boletas con comprador cargado (comprador_id IS NOT NULL)
   por Boleta.vendedor_id  -- sin importar fecha_venta.
3. Crea UNA liquidacion por vendedor, con fecha 10/05/2026, replicando
   exactamente la matematica del endpoint POST /vendedores/{vid}/liquidar:
       - Modalidad por boleta: 'contado' si tiene numero_especial o
         numero_especial_2; 'cuotas' en caso contrario.
       - cuotas_vendidas = cant. de boletas en cuotas (literal)
       - cuotas_equiv    = suma de multiplicadores de PATA (ponderado)
       - cuota_1_total   = suma de valor_cuota de las boletas en cuotas
       - monto_contados  = suma de (num_cuotas x valor_cuota) de contados
       - comision_contados = monto_contados x 30%
       - total_comision  = comision_contados
       - total_a_rendir  = monto_contados - comision_contados
       - cuotas_extras_* = 0  (no se pueden derivar de los datos)
4. Enlaza cada boleta a la liquidacion de su vendedor.

NO toca Boleta.vendedor_id ni Boleta.condicion: solo reescribe las
liquidaciones y el enlace liquidacion_vendedor_id.

Base de datos
-------------
- Con DATABASE_URL exportada  -> Railway / PostgreSQL (PRODUCCION)
- Sin DATABASE_URL            -> SQLite local (bonos.db)

USO
---
Desde la carpeta bono-app/:

    # 1) Vista previa SIN escribir nada (recomendado primero):
    $env:DATABASE_URL="postgresql://USUARIO:PASSWORD@HOST:PORT/DBNAME"
    py -3.12 rearmar_liquidaciones_10052026.py --dry-run

    # 2) Aplicar de verdad (pide confirmacion):
    py -3.12 rearmar_liquidaciones_10052026.py

    # 2b) Aplicar sin preguntar:
    py -3.12 rearmar_liquidaciones_10052026.py --yes

Flags:
    --dry-run   Calcula y muestra el resumen, NO escribe nada.
    --yes       No pide confirmacion (commit directo).
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

from app.database import SessionLocal
from app import models


# ── Parametros ────────────────────────────────────────────────────────────────
FECHA_LIQUIDACION = datetime(2026, 5, 10, 12, 0, 0)
OBSERVACION = (
    "Reconstruida 10/05/2026 - rearmada desde Socios para corregir el "
    "desfasaje de las liquidaciones del sistema viejo."
)
COMISION_CUOTAS_PCT = 5.0
COMISION_CONTADOS_PCT = 30.0


def _aplicar_migraciones():
    try:
        from app.main import create_default_admin
        create_default_admin()
        print("[OK]   Migraciones aplicadas (startup ejecutado).")
    except Exception as e:
        print(f"[WARN] No se pudo aplicar migraciones automaticamente: {e}")
        print("       Levanta la app manualmente una vez para aplicarlas:")
        print("         py -3.12 -m uvicorn app.main:app --reload")


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
        print("[INFO] Sin DATABASE_URL -> usando SQLite local (bonos.db)")


def _es_contado(b) -> bool:
    """Una boleta cuenta como vendida al contado si tiene cargado alguno
    de los slots del pool CONTADO (numero_especial / numero_especial_2).
    Mismo criterio que usa /vendedores/{vid}/detalle."""
    return (b.numero_especial is not None) or (b.numero_especial_2 is not None)


def _mult(b) -> float:
    if b.talonera and b.talonera.multiplicador is not None:
        return float(b.talonera.multiplicador)
    return 1.0


def _valor_cuota(b) -> float:
    if b.talonera and b.talonera.valor_cuota is not None:
        return float(b.talonera.valor_cuota)
    return 0.0


def _num_cuotas(b) -> int:
    if b.talonera and b.talonera.num_cuotas:
        return int(b.talonera.num_cuotas)
    return 12


def _tal_nombre(b) -> str:
    return b.talonera.nombre if b.talonera else "(sin talonera)"


def main(argv):
    dry_run = "--dry-run" in argv
    auto_yes = "--yes" in argv

    _print_db_target()
    _aplicar_migraciones()
    db = SessionLocal()
    try:
        # ── 1. Relevamiento ───────────────────────────────────────────────────
        vendedores = {v.id: v for v in db.query(models.Vendedor).all()}

        # Todas las boletas con comprador cargado (= "vendido" segun Socios).
        boletas_vendidas = (
            db.query(models.Boleta)
            .filter(models.Boleta.comprador_id.isnot(None))
            .all()
        )

        # Agrupar por vendedor_id
        por_vendedor: dict[int, list] = {}
        sin_vendedor = []
        for b in boletas_vendidas:
            if b.vendedor_id is None:
                sin_vendedor.append(b)
                continue
            if b.vendedor_id not in vendedores:
                sin_vendedor.append(b)
                continue
            por_vendedor.setdefault(b.vendedor_id, []).append(b)

        liqs_existentes = db.query(models.LiquidacionVendedor).count()
        boletas_con_liq = (
            db.query(models.Boleta)
            .filter(models.Boleta.liquidacion_vendedor_id.isnot(None))
            .count()
        )

        # ── 2. Calcular la liquidacion de cada vendedor ───────────────────────
        plan = []  # lista de dicts con todo lo necesario para crear/mostrar
        for vid, boletas in sorted(por_vendedor.items(),
                                   key=lambda kv: vendedores[kv[0]].nombre):
            v = vendedores[vid]
            cuotas = [b for b in boletas if not _es_contado(b)]
            contados_b = [b for b in boletas if _es_contado(b)]

            cuotas_vendidas = len(cuotas)
            cuotas_equiv = round(sum(_mult(b) for b in cuotas), 4)
            cuota_1_total = round(sum(_valor_cuota(b) for b in cuotas), 2)
            monto_cuotas = cuota_1_total
            comision_cuotas = 0.0

            monto_contados = round(
                sum(_num_cuotas(b) * _valor_cuota(b) for b in contados_b), 2
            )
            contados_vendidos = len(contados_b)
            comision_contados = round(monto_contados * COMISION_CONTADOS_PCT / 100, 2)

            total_comision = round(comision_contados, 2)
            total_a_rendir = round(monto_contados - comision_contados, 2)

            plan.append({
                "vendedor": v,
                "boletas": boletas,
                "cuotas": cuotas,
                "contados_b": contados_b,
                "cuotas_vendidas": cuotas_vendidas,
                "cuotas_equiv": cuotas_equiv,
                "cuota_1_total": cuota_1_total,
                "monto_cuotas": monto_cuotas,
                "comision_cuotas": comision_cuotas,
                "monto_contados": monto_contados,
                "contados_vendidos": contados_vendidos,
                "comision_contados": comision_contados,
                "total_comision": total_comision,
                "total_a_rendir": total_a_rendir,
            })

        # ── 3. Mostrar resumen ────────────────────────────────────────────────
        print()
        print("=" * 64)
        print("  REARMAR LIQUIDACIONES - FECHA 10/05/2026")
        print("=" * 64)
        print(f"  Liquidaciones viejas a BORRAR:        {liqs_existentes}")
        print(f"  Boletas con liquidacion_vendedor_id:  {boletas_con_liq}  (se limpian)")
        print(f"  Boletas vendidas (comprador cargado): {len(boletas_vendidas)}")
        print(f"  Vendedores con boletas:               {len(plan)}")
        print("-" * 64)

        tot_boletas = tot_equiv = 0.0
        tot_cuota1 = tot_contados_monto = tot_comision = tot_rendir = 0.0
        for p in plan:
            v = p["vendedor"]
            print(f"\n  >> {v.nombre}  (id={v.id})")
            print(f"     Boletas en cuotas:   {p['cuotas_vendidas']:>4d}   "
                  f"ponderado {p['cuotas_equiv']:>8.2f}")
            print(f"     Boletas al contado:  {p['contados_vendidos']:>4d}")
            print(f"     Cuota 1 total:       ${p['cuota_1_total']:>14,.2f}")
            print(f"     Monto contados:      ${p['monto_contados']:>14,.2f}")
            print(f"     Comision contados:   ${p['comision_contados']:>14,.2f}  "
                  f"({COMISION_CONTADOS_PCT:.0f}%)")
            print(f"     Total comision:      ${p['total_comision']:>14,.2f}")
            print(f"     Total a rendir:      ${p['total_a_rendir']:>14,.2f}")
            # desglose por talonera
            por_tal = Counter()
            for b in p["boletas"]:
                por_tal[_tal_nombre(b)] += 1
            desglose = "  ".join(f"{nom}:{n}" for nom, n in sorted(por_tal.items()))
            print(f"     Taloneras:           {desglose}")
            tot_boletas += len(p["boletas"])
            tot_equiv += p["cuotas_equiv"]
            tot_cuota1 += p["cuota_1_total"]
            tot_contados_monto += p["monto_contados"]
            tot_comision += p["total_comision"]
            tot_rendir += p["total_a_rendir"]

        print("\n" + "-" * 64)
        print(f"  TOTALES   boletas={int(tot_boletas)}   "
              f"ponderado={tot_equiv:.2f}")
        print(f"            cuota_1_total   = ${tot_cuota1:,.2f}")
        print(f"            monto_contados  = ${tot_contados_monto:,.2f}")
        print(f"            total_comision  = ${tot_comision:,.2f}")
        print(f"            total_a_rendir  = ${tot_rendir:,.2f}")

        if sin_vendedor:
            print("\n  [WARN] Boletas con comprador pero SIN vendedor_id valido")
            print(f"         (no entran en ninguna liquidacion): {len(sin_vendedor)}")
            for b in sin_vendedor[:20]:
                print(f"           - boleta id={b.id} numero={b.numero_principal} "
                      f"talonera={_tal_nombre(b)}")
            if len(sin_vendedor) > 20:
                print(f"           ... y {len(sin_vendedor) - 20} mas")

        print("=" * 64)

        if dry_run:
            print("\n[DRY-RUN] No se modifico la base de datos.")
            return 0

        if not plan:
            print("\n[INFO] No hay boletas vendidas para liquidar. Nada que hacer.")
            return 0

        if not auto_yes:
            print("\n  ATENCION: esto BORRA las liquidaciones viejas y las rearma.")
            resp = input("  Confirmas rearmar las liquidaciones? [s/N]: ").strip().lower()
            if resp not in ("s", "si", "y", "yes"):
                print("  Cancelado por el usuario. No se modifico nada.")
                return 0

        # ── 4. Borrar liquidaciones viejas ────────────────────────────────────
        (db.query(models.Boleta)
           .filter(models.Boleta.liquidacion_vendedor_id.isnot(None))
           .update({"liquidacion_vendedor_id": None}, synchronize_session=False))
        # Borra cabeceras (y LiquidacionContadoItem por cascade ORM)
        for liq_viejo in db.query(models.LiquidacionVendedor).all():
            db.delete(liq_viejo)
        db.flush()
        print(f"\n[OK] Liquidaciones viejas borradas y boletas desvinculadas.")

        # ── 5. Crear las nuevas y enlazar boletas ─────────────────────────────
        creadas = 0
        for p in plan:
            v = p["vendedor"]
            liq = models.LiquidacionVendedor(
                vendedor_id=v.id,
                fecha=FECHA_LIQUIDACION,
                cuotas_vendidas=p["cuotas_vendidas"],
                cuotas_equiv=p["cuotas_equiv"],
                cuota_1_total=p["cuota_1_total"],
                monto_cuotas=p["monto_cuotas"],
                comision_cuotas_pct=COMISION_CUOTAS_PCT,
                comision_cuotas=p["comision_cuotas"],
                contados_vendidos=p["contados_vendidos"],
                monto_contados=p["monto_contados"],
                comision_contados_pct=COMISION_CONTADOS_PCT,
                comision_contados=p["comision_contados"],
                cuotas_extras_cantidad=0,
                cuotas_extras_valor=0.0,
                cuotas_extras_monto=0.0,
                comision_cuotas_extras=0.0,
                total_comision=p["total_comision"],
                total_a_rendir=p["total_a_rendir"],
                observacion=OBSERVACION,
            )
            db.add(liq)
            db.flush()
            for b in p["boletas"]:
                b.liquidacion_vendedor_id = liq.id
            creadas += 1
            print(f"[OK] Liquidacion id={liq.id} para {v.nombre}: "
                  f"{len(p['boletas'])} boleta(s) enlazada(s).")

        db.commit()
        print(f"\n[OK] {creadas} liquidacion(es) creada(s) con fecha "
              f"{FECHA_LIQUIDACION:%d/%m/%Y}.")
        print("     Verificar en /vendedores/ (tab Liquidaciones de cada vendedor).")
        return 0

    except Exception as e:
        db.rollback()
        print(f"\n[ERROR] {type(e).__name__}: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
