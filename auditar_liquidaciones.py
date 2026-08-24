"""
auditar_liquidaciones.py
========================

Compara, liquidacion por liquidacion, los TOTALES GUARDADOS contra lo que
dicen las BOLETAS realmente atadas a esa liquidacion.

Por que existe
--------------
Los totales de LiquidacionVendedor (cuotas_vendidas, cuotas_equiv,
cuota_1_total, contados_vendidos, contados_equiv, monto_contados,
comision_contados, total_a_rendir) son SNAPSHOTS: se calculan una vez al
liquidar y despues se mantienen sumando/restando cuando se agrega o saca un
numero. Si alguna vez se desincronizan, el historial muestra mas (o menos)
boletas y plata de las que hay de verdad, y el detalle de la liquidacion
muestra la lista real -- por eso los dos numeros no coinciden.

Que detecta
-----------
1. DESFASAJE  : la liquidacion tiene totales distintos a sus boletas atadas.
2. FANTASMA   : liquidacion con totales cargados pero CERO boletas atadas
                (tipico de una doble liquidacion: dos requests que leyeron
                las mismas boletas antes de que la primera guardara; las
                boletas quedan atadas a una sola y la otra queda con los
                numeros colgados).
3. DUPLICADA  : mismo vendedor, mismo dia, misma plata, una con boletas y
                otra sin ninguna. Es el patron de la doble liquidacion.

Como recalcula (misma matematica que POST /vendedores/{vid}/liquidar)
--------------------------------------------------------------------
    modalidad por boleta -> app.routers.vendedores.modalidad_de_boleta
    cuotas_vendidas   = cantidad de boletas en cuotas
    cuotas_equiv      = suma de multiplicadores de PATA
    cuota_1_total     = suma de valor_cuota
    contados_vendidos = cantidad de boletas al contado
    contados_equiv    = suma de multiplicadores
    monto_contados    = suma de cuotas_vigentes(FECHA DE LA LIQUIDACION) x valor_cuota
    comision_contados = monto_contados x comision_contados_pct
    total_a_rendir    = (monto_contados - comision) + extras - comisiones de extras

Las CUOTAS EXTRAS son carga manual (no salen de ninguna boleta): NO se tocan
ni se recalculan, solo se arrastran al total.

Base de datos
-------------
- Con DATABASE_URL exportada  -> Railway / PostgreSQL (PRODUCCION)
- Sin DATABASE_URL            -> SQLite local (bonos.db)

USO
---
Desde la carpeta bono-app/:

    # 1) Solo mirar (no escribe NADA). Empeza siempre por aca:
    $env:DATABASE_URL="postgresql://USUARIO:PASSWORD@HOST:PORT/DBNAME"
    py -3.12 auditar_liquidaciones.py

    # 2) Corregir los totales desfasados (pide confirmacion):
    py -3.12 auditar_liquidaciones.py --reparar

    # 3) Ademas, borrar las liquidaciones fantasma (sin boletas, sin cuotas
    #    extras y sin numeros del pool CONTADO):
    py -3.12 auditar_liquidaciones.py --reparar --borrar-fantasmas

Flags:
    --reparar           Reescribe los totales con lo que dicen las boletas.
    --borrar-fantasmas  Ademas borra las liquidaciones vacias (solo junto a --reparar).
    --yes               No pide confirmacion.
    --todas             Lista tambien las liquidaciones que estan bien.
"""

from __future__ import annotations

import io
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import joinedload, undefer

from app.database import SessionLocal
from app import models
from app.cuotas import cuotas_vigentes
from app.routers.vendedores import modalidad_de_boleta

TOL = 0.51  # tolerancia en pesos: diferencias por redondeo no son desfasaje
INFORME = "auditoria_liquidaciones.txt"


class _Tee:
    """Escribe a la consola y al informe a la vez.

    Antes el .bat redirigia la salida a un archivo y recien al final hacia
    `type`: contra Railway eso son varios minutos con la pantalla en blanco,
    sin saber si esta trabajando o colgado. Ahora se ve en vivo y el archivo
    queda igual.
    """

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


def _recalcular(liq, boletas):
    """Lo que DEBERIA decir la liquidacion segun sus boletas atadas."""
    cuotas, contados = [], []
    for b in boletas:
        (contados if modalidad_de_boleta(b) in ("contado", "contado2") else cuotas).append(b)

    def mult(b):
        return float((b.talonera.multiplicador or 1.0) if b.talonera else 1.0)

    def vc(b):
        return float((b.talonera.valor_cuota or 0.0) if b.talonera else 0.0)

    monto_contados = sum(
        cuotas_vigentes(b.talonera.num_cuotas if b.talonera else None, liq.fecha) * vc(b)
        for b in contados
    )
    pct = float(liq.comision_contados_pct or 0) or 30.0
    com_contados = round(monto_contados * pct / 100, 2)
    cuota_1_total = round(sum(vc(b) for b in cuotas), 2)

    # Las extras son carga manual: se arrastran tal cual al total.
    extras = (float(liq.cuotas_extras_monto or 0) - float(liq.comision_cuotas_extras or 0)
              + float(liq.cuotas_extras_p0_monto or 0) - float(liq.comision_cuotas_extras_p0 or 0))

    return {
        "cuotas_vendidas":   len(cuotas),
        "cuotas_equiv":      round(sum(mult(b) for b in cuotas), 4),
        "cuota_1_total":     cuota_1_total,
        "monto_cuotas":      cuota_1_total,
        "comision_cuotas":   cuota_1_total,
        "contados_vendidos": len(contados),
        "contados_equiv":    round(sum(mult(b) for b in contados), 4),
        "monto_contados":    round(monto_contados, 2),
        "comision_contados": com_contados,
        "total_a_rendir":    round((monto_contados - com_contados) + extras, 2),
    }


def _diferencias(liq, real):
    """Campos guardados que no coinciden con lo recalculado."""
    difs = []
    for campo, valor_real in real.items():
        guardado = getattr(liq, campo, 0) or 0
        if campo in ("cuotas_vendidas", "contados_vendidos"):
            if int(guardado) != int(valor_real):
                difs.append((campo, int(guardado), int(valor_real)))
        elif campo in ("cuotas_equiv", "contados_equiv"):
            if abs(float(guardado) - float(valor_real)) > 0.01:
                difs.append((campo, round(float(guardado), 2), round(float(valor_real), 2)))
        else:
            if abs(float(guardado) - float(valor_real)) > TOL:
                difs.append((campo, round(float(guardado), 2), round(float(valor_real), 2)))
    return difs


def main():
    args = set(sys.argv[1:])
    reparar   = "--reparar" in args
    borrar    = "--borrar-fantasmas" in args
    sin_pedir = "--yes" in args
    todas     = "--todas" in args

    sys.stdout = _Tee(INFORME)
    _print_db_target()
    db = SessionLocal()
    try:
        try:
            print("Trayendo liquidaciones...")
            # undefer("*"): varias columnas del modelo son `deferred` (se cargan
            # sueltas al tocarlas). Sin esto cada una dispara su propia consulta
            # contra Railway -> cientos de idas y vueltas por el proxy publico.
            liqs = (db.query(models.LiquidacionVendedor)
                      .options(undefer("*"))
                      .order_by(models.LiquidacionVendedor.fecha,
                                models.LiquidacionVendedor.id).all())
        except Exception as e:
            # Tipico: se corrio sin DATABASE_URL y la base local no existe o
            # esta vacia. Mejor un mensaje claro que un chorizo de SQLAlchemy.
            print("\n[ERROR] No pude leer las liquidaciones de esta base.")
            print(f"        {type(e).__name__}: {str(e).splitlines()[0][:160]}")
            if not os.environ.get("DATABASE_URL"):
                print("\n        Estas apuntando a la base LOCAL (bonos.db), que esta vacia.")
                print("        Para auditar Railway hace doble click en:")
                print("            auditar_liquidaciones.bat")
                print("        (toma la URL de backup.bat, no hay que configurar nada)")
            return
        if not liqs:
            print("[INFO] No hay liquidaciones registradas en esta base.")
            if not os.environ.get("DATABASE_URL"):
                print("       Estas mirando la base LOCAL (bonos.db). Para auditar")
                print("       Railway hace doble click en: auditar_liquidaciones.bat")
            return

        print(f"  {len(liqs)} liquidacion(es). Trayendo boletas liquidadas...")

        # Todo en UNA consulta: la talonera con joinedload y las columnas
        # deferred con undefer. Con N+1 esto tardaba minutos contra Railway.
        por_liq = defaultdict(list)
        boletas_liq = (db.query(models.Boleta)
                         .options(joinedload(models.Boleta.talonera).undefer("*"),
                                  undefer("*"))
                         .filter(models.Boleta.liquidacion_vendedor_id.isnot(None))
                         .all())
        for b in boletas_liq:
            por_liq[b.liquidacion_vendedor_id].append(b)
        print(f"  {len(boletas_liq)} boleta(s) atada(s) a alguna liquidacion.")

        pool_por_liq = defaultdict(int)
        try:
            for it in db.query(models.LiquidacionContadoItem).all():
                pool_por_liq[it.liquidacion_id] += 1
        except Exception:
            pass

        nombres = {v.id: v.nombre for v in db.query(models.Vendedor).all()}

        desfasadas, fantasmas = [], []
        drift_rendir = 0.0

        print("\n" + "=" * 78)
        print("AUDITORIA DE LIQUIDACIONES")
        print("=" * 78)

        for liq in liqs:
            boletas = por_liq.get(liq.id, [])
            real = _recalcular(liq, boletas)
            difs = _diferencias(liq, real)

            tiene_extras = (int(liq.cuotas_extras_cantidad or 0) > 0
                            or int(getattr(liq, "cuotas_extras_p0_cantidad", 0) or 0) > 0)
            es_fantasma = (not boletas and not tiene_extras
                           and pool_por_liq.get(liq.id, 0) == 0
                           and (int(liq.cuotas_vendidas or 0) > 0
                                or int(liq.contados_vendidos or 0) > 0
                                or float(liq.monto_contados or 0) > 0))

            if not difs and not es_fantasma:
                if todas:
                    print(f"\n[OK]  liq #{liq.id}  {liq.fecha:%d/%m/%Y}  "
                          f"{nombres.get(liq.vendedor_id, '?')}  "
                          f"({len(boletas)} boletas)")
                continue

            etiqueta = "FANTASMA" if es_fantasma else "DESFASAJE"
            print(f"\n[{etiqueta}]  liq #{liq.id}  {liq.fecha:%d/%m/%Y %H:%M}  "
                  f"{nombres.get(liq.vendedor_id, '?')}")
            print(f"    boletas atadas: {len(boletas)}"
                  + (f"  |  pool CONTADO: {pool_por_liq[liq.id]}" if pool_por_liq.get(liq.id) else "")
                  + ("  |  tiene cuotas extras cargadas" if tiene_extras else ""))
            for campo, guardado, valor_real in difs:
                marca = "  <-- PLATA" if campo in ("monto_contados", "comision_contados",
                                                   "cuota_1_total", "total_a_rendir") else ""
                print(f"    {campo:<20} guardado: {guardado:>12}   real: {valor_real:>12}{marca}")

            drift_rendir += (float(liq.total_a_rendir or 0) - float(real["total_a_rendir"]))
            (fantasmas if es_fantasma else desfasadas).append((liq, real, boletas))

        # -- Duplicadas: mismo vendedor y dia, una con boletas y otra sin --
        print("\n" + "-" * 78)
        por_dia = defaultdict(list)
        for liq in liqs:
            por_dia[(liq.vendedor_id, liq.fecha.date() if liq.fecha else None)].append(liq)
        hubo_dup = False
        for (vid, dia), grupo in sorted(por_dia.items(), key=lambda x: (str(x[0][1]), x[0][0] or 0)):
            if len(grupo) < 2:
                continue
            con, sin = [], []
            for liq in grupo:
                (con if por_liq.get(liq.id) else sin).append(liq)
            if con and sin:
                hubo_dup = True
                print(f"[DUPLICADA?] {nombres.get(vid, '?')} el {dia}: "
                      f"#{', #'.join(str(l.id) for l in con)} con boletas, "
                      f"#{', #'.join(str(l.id) for l in sin)} SIN boletas "
                      f"(a rendir: {' / '.join(_money(l.total_a_rendir) for l in grupo)})")
        if not hubo_dup:
            print("[OK] No se detectaron pares duplicados (mismo vendedor y dia).")

        print("\n" + "=" * 78)
        print(f"RESUMEN: {len(liqs)} liquidaciones | "
              f"{len(desfasadas)} desfasadas | {len(fantasmas)} fantasma")
        print(f"Diferencia acumulada en 'total a rendir': {_money(drift_rendir)} "
              f"({'de mas' if drift_rendir > 0 else 'de menos'} en el sistema)")
        print("=" * 78)

        if not reparar:
            if desfasadas or fantasmas:
                print("\nSolo lectura: NO se escribio nada.")
                print("Para corregir los totales:      py -3.12 auditar_liquidaciones.py --reparar")
                print("Para ademas borrar las vacias:  py -3.12 auditar_liquidaciones.py --reparar --borrar-fantasmas")
            return

        if not desfasadas and not (borrar and fantasmas):
            print("\nNada para reparar.")
            return

        if not sin_pedir:
            print(f"\nSe van a reescribir los totales de {len(desfasadas)} liquidacion(es)"
                  + (f" y BORRAR {len(fantasmas)} fantasma(s)." if borrar and fantasmas else ".")
                  + "\nLas boletas NO se tocan.")
            if input("Escribi SI para aplicar: ").strip().upper() != "SI":
                print("Cancelado. No se escribio nada.")
                return

        for liq, real, _bol in desfasadas:
            for campo, valor in real.items():
                setattr(liq, campo, valor)
            liq.total_comision = round(
                float(real["comision_cuotas"]) + float(real["comision_contados"])
                + float(liq.comision_cuotas_extras or 0)
                + float(getattr(liq, "comision_cuotas_extras_p0", 0) or 0), 2)
        print(f"[OK] {len(desfasadas)} liquidacion(es) recalculada(s).")

        if borrar and fantasmas:
            for liq, _real, _bol in fantasmas:
                db.delete(liq)
            print(f"[OK] {len(fantasmas)} liquidacion(es) fantasma borrada(s).")

        db.commit()
        print("[OK] Cambios guardados.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
