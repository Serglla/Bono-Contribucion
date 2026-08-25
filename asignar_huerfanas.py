"""
asignar_huerfanas.py
====================

Mete en la liquidacion de su vendedor las boletas que tienen SOCIO CARGADO pero
que nunca pasaron por ninguna liquidacion ("huerfanas").

Por que existen
---------------
La cuota 1 la cobra el vendedor en el acto, al vender. Si despues el socio se
carga sobre un numero que nunca se rindio, la boleta queda con comprador pero
sin `liquidacion_vendedor_id`: la plata ya la cobro el vendedor, lo que falta es
el REGISTRO. El vendedor figura ganando menos de lo que gano y el numero no
cuenta en su liquidado. Tambien es la diferencia que se ve en el dashboard entre
"Vendidas" y "Liquidado por vendedor".

Que hace
--------
Para cada huerfana:
  1. Busca la liquidacion de SU vendedor (`Boleta.vendedor_id`) mas cercana ANTES
     o EL MISMO DIA de la fecha de venta de la boleta. Asi la plata cae en el mes
     que corresponde. Si no hay ninguna previa, usa la primera del vendedor.
  2. La suma a esa liquidacion con `_sumar_boleta_a_liq` -- el MISMO helper que
     usa el endpoint de liquidar, para que no haya una segunda matematica dando
     vueltas. La modalidad se deduce con `modalidad_de_boleta`.
  3. Enlaza la boleta (`liquidacion_vendedor_id`).

Que NO toca
-----------
- `condicion`: las que estan EN_COBRANZA siguen EN_COBRANZA. Esto es contabilidad
  del vendedor, no saca ni mete boletas en cobranza.
- `cuotas_pactadas`, `cuotas_anticipadas`, `cuotas_pagadas`, cobrador, socio.

Excluidas
---------
`EXCLUIDAS` lista los numeros que NO se tocan por ser institucionales o de
cortesia (nadie las vendio, corresponde que no tengan liquidacion). Hoy:
la 0001 de PATA 1 (a nombre del presidente de bomberos).
Con `--incluir-todas` se procesan igual.

Base de datos
-------------
- Con DATABASE_URL exportada  -> Railway / PostgreSQL (PRODUCCION)
- Sin DATABASE_URL            -> SQLite local (bonos.db)

USO
---
Desde la carpeta bono-app/ (o doble click en asignar_huerfanas.bat):

    py -3.12 asignar_huerfanas.py            # solo muestra, NO escribe
    py -3.12 asignar_huerfanas.py --aplicar  # asigna (pide confirmacion)

Flags:
    --aplicar         Escribe los cambios.
    --yes             No pide confirmacion.
    --incluir-todas   Procesa tambien las de la lista EXCLUIDAS.
"""

from __future__ import annotations

import io
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import joinedload, undefer

from app.database import SessionLocal
from app import models
from app.routers.vendedores import (modalidad_de_boleta, _sumar_boleta_a_liq,
                                    _monto_contado_boleta, MODALIDADES_LIQ)

INFORME = "asignar_huerfanas.txt"

# (nombre de talonera, numero) que NO se tocan: nadie las vendio.
EXCLUIDAS = {
    ("PATA 1", 1),   # institucional - presidente de bomberos
}


class _Tee:
    """Consola y archivo a la vez (la ventana del .bat no guarda scroll)."""

    def __init__(self, path):
        self._f = None
        try:
            self._f = io.open(path, "w", encoding="utf-8", newline="")
        except Exception:
            pass

    def write(self, s):
        sys.__stdout__.write(s)
        sys.__stdout__.flush()
        if self._f:
            try:
                self._f.write(s)
            except Exception:
                pass
        return len(s)

    def flush(self):
        sys.__stdout__.flush()
        if self._f:
            try:
                self._f.flush()
            except Exception:
                pass


def _print_db_target():
    url = os.environ.get("DATABASE_URL")
    if url:
        safe = url
        if "@" in url and "://" in url:
            scheme, rest = url.split("://", 1)
            if "@" in rest:
                creds, host = rest.split("@", 1)
                safe = f"{scheme}://{creds.split(':', 1)[0]}:***@{host}"
        print(f"[INFO] DATABASE_URL detectada -> {safe}")
    else:
        print("[INFO] Sin DATABASE_URL -> usando SQLite local (bonos.db)")


def _money(x) -> str:
    return f"${float(x or 0):,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


def _fecha(f):
    if f is None:
        return None
    return f.date() if isinstance(f, datetime) else f


def _elegir_liquidacion(b, liqs_del_vendedor):
    """La liquidacion mas cercana ANTES o el mismo dia de la venta.
    Si no hay ninguna previa, la primera del vendedor (asi no queda sin asignar).
    Devuelve (liq, motivo)."""
    if not liqs_del_vendedor:
        return None, "el vendedor no tiene ninguna liquidacion"
    fv = _fecha(b.fecha_venta)
    if fv is not None:
        previas = [l for l in liqs_del_vendedor if _fecha(l.fecha) and _fecha(l.fecha) <= fv]
        if previas:
            elegida = max(previas, key=lambda l: (_fecha(l.fecha), l.id))
            return elegida, f"ultima rendicion antes del {fv.strftime('%d/%m/%Y')}"
    primera = min(liqs_del_vendedor, key=lambda l: (_fecha(l.fecha) or fv, l.id))
    return primera, "no hay rendicion previa a la venta: va a la primera del vendedor"


def main():
    args = set(sys.argv[1:])
    aplicar = "--aplicar" in args
    sin_pedir = "--yes" in args
    todas = "--incluir-todas" in args

    sys.stdout = _Tee(INFORME)
    _print_db_target()
    db = SessionLocal()
    try:
        B = models.Boleta
        try:
            huerfanas = (db.query(B)
                           .options(joinedload(B.talonera).undefer("*"),
                                    joinedload(B.vendedor),
                                    joinedload(B.comprador),
                                    undefer("*"))
                           .filter(B.comprador_id.isnot(None),
                                   B.liquidacion_vendedor_id.is_(None))
                           .order_by(B.numero_principal).all())
        except Exception as e:
            print("\n[ERROR] No pude leer las boletas de esta base.")
            print(f"        {type(e).__name__}: {str(e).splitlines()[0][:160]}")
            if not os.environ.get("DATABASE_URL"):
                print("\n        Estas apuntando a la base LOCAL (bonos.db).")
                print("        Para trabajar sobre Railway hace doble click en:")
                print("            asignar_huerfanas.bat")
            return

        print("\n" + "=" * 78)
        print("BOLETAS CON SOCIO PERO SIN LIQUIDACION")
        print("=" * 78)
        if not huerfanas:
            print("No hay ninguna. Nada que hacer.")
            return

        liqs_por_vend = defaultdict(list)
        for l in (db.query(models.LiquidacionVendedor).options(undefer("*")).all()):
            liqs_por_vend[l.vendedor_id].append(l)

        plan, saltadas = [], []
        for b in huerfanas:
            tal = b.talonera.nombre if b.talonera else "?"
            nd = (b.talonera.num_digitos or 4) if b.talonera else 4
            num = str(b.numero_principal or 0).zfill(nd)
            socio = (b.comprador.apellido_nombre if b.comprador else "?")[:30]
            vend = b.vendedor.nombre if b.vendedor else None

            if not todas and (tal, b.numero_principal) in EXCLUIDAS:
                saltadas.append((num, tal, socio, "excluida (institucional / cortesia)"))
                continue
            if not vend:
                saltadas.append((num, tal, socio, "la boleta no tiene vendedor asignado"))
                continue
            liq, motivo = _elegir_liquidacion(b, liqs_por_vend.get(b.vendedor_id, []))
            if liq is None:
                saltadas.append((num, tal, socio, motivo))
                continue
            plan.append((b, num, tal, socio, vend, liq, motivo,
                         modalidad_de_boleta(b)))

        for num, tal, socio, motivo in saltadas:
            print(f"\n  [SE SALTA] {num}  {tal:<8} {socio}")
            print(f"             {motivo}")

        for b, num, tal, socio, vend, liq, motivo, mod in plan:
            vc = float((b.talonera.valor_cuota or 0) if b.talonera else 0)
            if mod == "cuotas":
                efecto = f"cuota 1 {_money(vc)} (se la queda el vendedor)"
            else:
                # Al contado el vendedor cobro la talonera entera: rinde el total
                # menos su comision. Se valua a la fecha de ESA liquidacion.
                _mc = _monto_contado_boleta(b, liq)
                _pct = float(liq.comision_contados_pct or 0) or 30.0
                efecto = (f"contado {_money(_mc)} - {_pct:g}% comision "
                          f"= rinde {_money(_mc - round(_mc * _pct / 100, 2))}")
            print(f"\n  {num}  {tal:<8} {socio}")
            print(f"      vendedor: {vend}   modalidad: {mod.upper()}")
            print(f"      {efecto}")
            print(f"      -> liquidacion #{liq.id} del "
                  f"{_fecha(liq.fecha).strftime('%d/%m/%Y') if _fecha(liq.fecha) else '?'}"
                  f"  ({motivo})")

        print("\n" + "=" * 78)
        print(f"RESUMEN: {len(huerfanas)} huerfana(s) | {len(plan)} a asignar | "
              f"{len(saltadas)} sin tocar")
        por_vend = defaultdict(lambda: [0.0, 0.0])   # [cuota 1, a rendir por contado]
        for b, _n, _t, _s, vend, liq, _m, mod in plan:
            if mod == "cuotas":
                por_vend[vend][0] += float((b.talonera.valor_cuota or 0) if b.talonera else 0)
            else:
                _mc = _monto_contado_boleta(b, liq)
                _pct = float(liq.comision_contados_pct or 0) or 30.0
                por_vend[vend][1] += _mc - round(_mc * _pct / 100, 2)
        for vend, (c1, rend) in sorted(por_vend.items()):
            partes = []
            if c1:
                partes.append(f"{_money(c1)} de cuota 1")
            if rend:
                partes.append(f"{_money(rend)} mas a rendir por contado")
            print(f"  {vend}: " + " | ".join(partes))
        print("=" * 78)

        if not aplicar:
            if plan:
                print("\nSolo lectura: NO se escribio nada.")
                print("Para aplicarlo:  py -3.12 asignar_huerfanas.py --aplicar")
            return
        if not plan:
            print("\nNada para asignar.")
            return

        if not sin_pedir:
            print(f"\nSe van a asignar {len(plan)} boleta(s) a la liquidacion de su vendedor.")
            print("NO se toca la condicion (las que estan en cobranza siguen en cobranza).")
            if input("Escribi SI para aplicar: ").strip().upper() != "SI":
                print("Cancelado. No se escribio nada.")
                return

        for b, num, _t, _s, _v, liq, _m, mod in plan:
            if mod not in MODALIDADES_LIQ:
                mod = "cuotas"
            _sumar_boleta_a_liq(liq, b, mod)      # mismo helper que usa liquidar()
            b.liquidacion_vendedor_id = liq.id
            b.modalidad_liquidacion = mod         # deja de inferirse
            print(f"  {num} -> liquidacion #{liq.id} ({mod})")
        db.commit()
        print(f"\n[OK] {len(plan)} boleta(s) asignada(s).")
        print("     Corre auditar_liquidaciones.bat para verificar que todo cuadra.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
