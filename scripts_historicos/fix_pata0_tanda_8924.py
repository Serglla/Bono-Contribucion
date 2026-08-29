"""
fix_pata0_tanda_8924.py
=======================

Corrige la tanda de PATA 0 que se cargo mal por error humano.

Estado ACTUAL (mal)
-------------------
    Talonera PATA 0 -> numero_inicio=8924, numero_fin=9325 (402 boletas)
    offset_series chico -> los pares salen 8924/9023, 8925/9024, ...
    Resultado: la talonera ocupa 8924..9325 y por eso las taloneras NUEVAS
    "se pisan" y el sistema no deja cargarlas.

Estado CORRECTO (lo que dice la talonera fisica)
------------------------------------------------
    100 boletas: 8924 .. 9023
    2 series, offset 401 ->  primera boleta 8924 - 9325
                             ultima  boleta 9023 - 9424

Que hace el script
------------------
1. Busca la talonera COMUN llamada PATA 0 con numero_inicio = 8924.
2. Verifica que el rango nuevo (8924-9023 + 9325-9424) NO pise a ninguna
   otra talonera COMUN.  Si pisa, aborta y dice con cual.
3. Revisa las boletas con numero_principal > 9023 (las 302 fantasma):
      - Si alguna tiene comprador, liquidacion, planilla, cuotas pagadas o
        numero especial -> ABORTA y las lista (no se pierde nada).
      - Si estan vacias (a lo sumo entregadas a un vendedor) -> las borra.
4. Actualiza la talonera: numero_fin=9023, num_series=2, offset_series=401,
   multiplicador = 2/3.
5. Recalcula numeros_adicionales de las boletas 8924..9023  (n -> n+401).
   NO toca comprador, vendedor, cobrador, cuotas ni condicion.
6. Crea las boletas que falten dentro de 8924..9023.

Base de datos
-------------
- Con DATABASE_URL exportada  -> Railway / PostgreSQL (PRODUCCION)
- Sin DATABASE_URL            -> SQLite local (bonos.db)

USO (desde la carpeta bono-app/, PowerShell)
--------------------------------------------
    # 0) una sola vez, si falta el driver:
    py -3.12 -m pip install psycopg2-binary

    # 1) apuntar a Railway con la URL PUBLICA (host *.proxy.rlwy.net):
    $env:DATABASE_URL="postgresql://USER:PASS@HOST.proxy.rlwy.net:PUERTO/railway"

    # 2) diagnostico, no escribe nada:
    py -3.12 fix_pata0_tanda_8924.py --dry-run

    # 3) aplicar (pide confirmacion):
    py -3.12 fix_pata0_tanda_8924.py

Flags:
    --dry-run   Muestra todo lo que haria, NO escribe.
    --yes       Aplica sin pedir confirmacion.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import SessionLocal
from app import models
from app.models import CondicionBoleta


# ── Parametros del arreglo ───────────────────────────────────────────────────
NOMBRE_TALONERA = "PATA 0"
INICIO = 8924          # primer numero principal (no cambia)
FIN = 9023             # ultimo numero principal (antes estaba en 9325)
NUM_SERIES = 2
OFFSET = 401           # 8924 -> 9325 ; 9023 -> 9424


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


def _intervalos(ini, fin, ns, off, tipo="COMUN"):
    """Bloques de numeros que ocupa una talonera (misma logica que taloneras.py)."""
    if ini is None or fin is None:
        return []
    ini, fin = int(ini), int(fin)
    if fin < ini:
        ini, fin = fin, ini
    ns = int(ns or 1)
    off = int(off or 0)
    if (tipo or "COMUN") == "CONTADO" or ns <= 1 or off == 0:
        return [(ini, fin)]
    return [(ini + off * i, fin + off * i) for i in range(ns)]


def _tiene_datos(b) -> bool:
    """True si la boleta tiene informacion que se perderia al borrarla."""
    return bool(
        b.comprador_id
        or b.liquidacion_vendedor_id
        or b.planilla_id
        or (b.cuotas_pagadas or 0) > 0
        or (b.total_pagado or 0) > 0
        or b.numero_especial
        or b.fecha_venta
        or b.condicion == CondicionBoleta.BAJA
    )


def main(argv):
    dry = "--dry-run" in argv
    auto = "--yes" in argv

    _print_db_target()
    db = SessionLocal()
    try:
        # ── 1. Localizar la talonera ─────────────────────────────────────────
        t = (db.query(models.Talonera)
               .filter(models.Talonera.nombre == NOMBRE_TALONERA,
                       models.Talonera.numero_inicio == INICIO)
               .first())
        if not t:
            print(f"[ERROR] No encontre la talonera '{NOMBRE_TALONERA}' que "
                  f"empieza en {INICIO}. Taloneras existentes:")
            for x in db.query(models.Talonera).order_by(models.Talonera.nombre,
                                                        models.Talonera.numero_inicio):
                print(f"        id={x.id:<4} {x.nombre:<12} {x.tipo:<8} "
                      f"{x.numero_inicio}-{x.numero_fin}  series={x.num_series} "
                      f"off={x.offset_series}")
            return 1

        print(f"\n[INFO] Talonera id={t.id}  {t.nombre}  {t.tipo}")
        print(f"       ANTES : {t.numero_inicio}-{t.numero_fin}  "
              f"series={t.num_series}  offset={t.offset_series}  "
              f"mult={t.multiplicador}")
        print(f"       DESPUES: {INICIO}-{FIN}  series={NUM_SERIES}  "
              f"offset={OFFSET}  mult={NUM_SERIES/3.0:.4f}")
        print(f"       Bloques nuevos: {_intervalos(INICIO, FIN, NUM_SERIES, OFFSET)}")
        print(f"       Primera boleta: {INICIO} - {INICIO + OFFSET}")
        print(f"       Ultima  boleta: {FIN} - {FIN + OFFSET}")

        # ── 2. Chequeo de solapamiento con las demas taloneras COMUN ─────────
        nuevos = _intervalos(INICIO, FIN, NUM_SERIES, OFFSET)
        choque = None
        for otra in db.query(models.Talonera).all():
            if otra.id == t.id or (otra.tipo or "COMUN") != "COMUN":
                continue
            for (blo, bhi) in _intervalos(otra.numero_inicio, otra.numero_fin,
                                          otra.num_series, otra.offset_series,
                                          otra.tipo):
                for (alo, ahi) in nuevos:
                    lo, hi = max(alo, blo), min(ahi, bhi)
                    if lo <= hi:
                        choque = (otra.nombre, otra.id, lo, hi)
                        break
                if choque:
                    break
            if choque:
                break
        if choque:
            nom, oid, lo, hi = choque
            print(f"\n[ERROR] El rango nuevo pisa a '{nom}' (id={oid}) "
                  f"en {lo}-{hi}. No aplico nada.")
            return 1
        print("[OK]   El rango nuevo no pisa ninguna otra talonera.")

        # ── 3. Boletas fantasma (> FIN) ──────────────────────────────────────
        fantasma = (db.query(models.Boleta)
                      .filter(models.Boleta.talonera_id == t.id,
                              models.Boleta.numero_principal > FIN)
                      .order_by(models.Boleta.numero_principal)
                      .all())
        con_datos = [b for b in fantasma if _tiene_datos(b)]
        borrables = [b for b in fantasma if not _tiene_datos(b)]
        con_vendedor = [b for b in borrables if b.vendedor_id]

        print(f"\n[INFO] Boletas fuera del rango correcto (> {FIN}): {len(fantasma)}")
        if con_datos:
            print(f"[ERROR] {len(con_datos)} de esas boletas TIENEN DATOS y no se "
                  f"pueden borrar automaticamente:")
            for b in con_datos[:60]:
                print(f"        N° {b.numero_principal}  comprador_id={b.comprador_id} "
                      f"vendedor_id={b.vendedor_id} cond={b.condicion} "
                      f"pagadas={b.cuotas_pagadas} liq={b.liquidacion_vendedor_id}")
            if len(con_datos) > 60:
                print(f"        ... y {len(con_datos)-60} mas")
            print("\n       Hay que resolverlas a mano antes de correr el fix.")
            return 1
        print(f"       Se borran {len(borrables)} boletas vacias "
              f"({len(con_vendedor)} estaban entregadas a un vendedor, "
              f"sin socio cargado).")

        # ── 4. Boletas del rango correcto ────────────────────────────────────
        dentro = (db.query(models.Boleta)
                    .filter(models.Boleta.talonera_id == t.id,
                            models.Boleta.numero_principal >= INICIO,
                            models.Boleta.numero_principal <= FIN)
                    .order_by(models.Boleta.numero_principal)
                    .all())
        existentes = {b.numero_principal for b in dentro}
        faltantes = [n for n in range(INICIO, FIN + 1) if n not in existentes]
        con_socio = [b for b in dentro if b.comprador_id]
        a_recalcular = [b for b in dentro
                        if (b.numeros_adicionales or "") != str(b.numero_principal + OFFSET)]

        print(f"\n[INFO] Boletas dentro de {INICIO}-{FIN}: {len(dentro)} "
              f"(faltan crear: {len(faltantes)})")
        print(f"       Con socio cargado (se conservan intactas): {len(con_socio)}")
        print(f"       Se les recalcula el 2do numero a: {len(a_recalcular)}")
        for b in dentro[:5]:
            print(f"         ej.  {b.numero_principal}: "
                  f"'{b.numeros_adicionales}' -> '{b.numero_principal + OFFSET}'")

        if dry:
            print("\n[DRY-RUN] No se escribio nada.")
            return 0

        if not auto:
            resp = input("\n¿Aplicar los cambios? (escribi SI): ").strip()
            if resp.upper() != "SI":
                print("Cancelado.")
                return 0

        # ── 5. Aplicar ───────────────────────────────────────────────────────
        for b in borrables:
            db.delete(b)

        t.numero_fin = FIN
        t.num_series = NUM_SERIES
        t.offset_series = OFFSET
        t.multiplicador = NUM_SERIES / 3.0

        for b in dentro:
            b.numeros_adicionales = str(b.numero_principal + OFFSET)

        cuotas = int(t.num_cuotas or 12)
        for n in faltantes:
            db.add(models.Boleta(
                talonera_id=t.id,
                numero_principal=n,
                numeros_adicionales=str(n + OFFSET),
                condicion=CondicionBoleta.SIN_VENDER,
                cuotas_pactadas=cuotas,
                cuotas_pagadas=0,
                total_pagado=0.0,
            ))

        db.commit()
        print(f"\n[OK] Listo. Talonera {t.id} -> {INICIO}-{FIN}, offset {OFFSET}.")
        print(f"     Borradas: {len(borrables)} | Recalculadas: {len(dentro)} | "
              f"Creadas: {len(faltantes)}")
        print("     Verificar en /taloneras/ (ojito de PATA 0) que los pares "
              f"vayan {INICIO}-{INICIO+OFFSET} ... {FIN}-{FIN+OFFSET}.")
        return 0

    except Exception as e:
        db.rollback()
        print(f"\n[ERROR] {type(e).__name__}: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
