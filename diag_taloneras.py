"""
diag_taloneras.py  --  SOLO LECTURA, no escribe nada.

Muestra todas las taloneras cargadas, los bloques de numeros que ocupa cada
una (serie 1, serie 2, ...), cuantas boletas tiene realmente, y simula si una
tanda nueva se puede crear o si choca con alguna existente.

USO (PowerShell, desde bono-app/):
    $env:DATABASE_URL="postgresql://...proxy.rlwy.net:PUERTO/railway"

    # listado completo:
    py -3.12 diag_taloneras.py

    # ademas, simular el alta de una tanda nueva:
    py -3.12 diag_taloneras.py --simular 9024 9073 9425
      (serie1_inicio  serie1_fin  serie2_inicio)
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


def _intervalos(ini, fin, ns, off, tipo="COMUN"):
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
        print(f"[INFO] DATABASE_URL -> {safe}")
    else:
        print("[INFO] Sin DATABASE_URL -> SQLite local (bonos.db)")


def main(argv):
    _print_db_target()
    db = SessionLocal()
    try:
        ts = (db.query(models.Talonera)
                .order_by(models.Talonera.tipo,
                          models.Talonera.nombre,
                          models.Talonera.numero_inicio)
                .all())
        print(f"\n=== {len(ts)} TALONERA(S) ===\n")
        print(f"{'id':>4}  {'nombre':<16} {'tipo':<8} {'rango':<14} "
              f"{'ser':>3} {'offset':>7} {'boletas':>8} {'vend':>5}  bloques")
        print("-" * 110)
        for t in ts:
            total = (db.query(models.Boleta)
                       .filter(models.Boleta.talonera_id == t.id).count())
            vend = (db.query(models.Boleta)
                      .filter(models.Boleta.talonera_id == t.id,
                              models.Boleta.comprador_id.isnot(None)).count())
            blo = _intervalos(t.numero_inicio, t.numero_fin,
                              t.num_series, t.offset_series, t.tipo)
            blo_s = "  ".join(f"{a}-{b}" for a, b in blo)
            esperadas = ((t.numero_fin or 0) - (t.numero_inicio or 0) + 1) \
                if t.numero_inicio and t.numero_fin else 0
            flag = "" if total == esperadas else f"  <-- OJO: esperadas {esperadas}"
            print(f"{t.id:>4}  {repr(t.nombre):<16} {str(t.tipo):<8} "
                  f"{str(t.numero_inicio)+'-'+str(t.numero_fin):<14} "
                  f"{t.num_series:>3} {t.offset_series:>7} {total:>8} {vend:>5}  "
                  f"{blo_s}{flag}")

        # Nombres parecidos (espacios / mayusculas) que romperian el agrupado
        nombres = sorted({t.nombre for t in ts})
        norm = {}
        for n in nombres:
            k = n.strip().upper()
            norm.setdefault(k, []).append(n)
        raros = {k: v for k, v in norm.items() if len(v) > 1}
        if raros:
            print("\n[OJO] Hay nombres que se parecen pero NO son iguales "
                  "(por eso aparecen en tarjetas separadas):")
            for k, v in raros.items():
                print(f"       {k} -> {v}")

        # ── Simulacion ───────────────────────────────────────────────────────
        if "--simular" in argv:
            i = argv.index("--simular")
            try:
                s1_ini, s1_fin, s2_ini = (int(x) for x in argv[i + 1:i + 4])
            except (ValueError, IndexError):
                print("\n[ERROR] Uso: --simular <serie1_inicio> <serie1_fin> "
                      "<serie2_inicio>")
                return 1
            off = s2_ini - s1_ini
            nuevos = _intervalos(s1_ini, s1_fin, 2, off)
            print(f"\n=== SIMULACION tanda nueva ===")
            print(f"    {len(range(s1_ini, s1_fin+1))} boletas | offset {off}")
            print(f"    primera: {s1_ini} - {s1_ini+off}   "
                  f"ultima: {s1_fin} - {s1_fin+off}")
            print(f"    bloques: {nuevos}")
            choques = []
            for t in ts:
                if (t.tipo or "COMUN") != "COMUN":
                    continue
                for (blo_, bhi) in _intervalos(t.numero_inicio, t.numero_fin,
                                               t.num_series, t.offset_series,
                                               t.tipo):
                    for (alo, ahi) in nuevos:
                        lo, hi = max(alo, blo_), min(ahi, bhi)
                        if lo <= hi:
                            choques.append((t.nombre, t.id, lo, hi))
            if choques:
                print("\n    [CHOCA] No se puede crear. Conflictos:")
                for nom, tid, lo, hi in choques:
                    print(f"        con '{nom}' (id={tid}) en {lo}-{hi}")
            else:
                print("\n    [OK] Libre, se puede crear sin solapamiento.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
