#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Diagnostico del POOL CONTADO (por que el desplegable dice "sin libres").

Reproduce exactamente la logica del endpoint
    GET /compradores/boleta/{id}/contado-disponibles
y muestra, paso por paso, donde se cae cada numero.

USO (doble click en diagnosticar_contado.bat) o manual:
    $env:DATABASE_URL="postgresql://..."
    py -3.12 diagnosticar_contado.py            -> informe de TODOS los vendedores
    py -3.12 diagnosticar_contado.py 0287       -> ademas, el detalle de esa boleta
"""
import os, re, sys

try:
    import psycopg2, psycopg2.extras
except ImportError:
    sys.exit("Falta psycopg2. Instalalo con:  py -3.12 -m pip install psycopg2-binary")

URL = os.getenv("DATABASE_URL")
if not URL:
    sys.exit("No hay DATABASE_URL. Usa diagnosticar_contado.bat (tiene la URL adentro).")
if URL.startswith("postgres://"):
    URL = URL.replace("postgres://", "postgresql://", 1)

con = psycopg2.connect(URL)
cur = con.cursor(cursor_factory=psycopg2.extras.DictCursor)

def q(sql, args=()):
    cur.execute(sql, args)
    return cur.fetchall()

def rol(nombre):
    nm = (nombre or "").strip().upper()
    if not nm.startswith("CONTADO"):
        return "OTRO"
    m = re.search(r"(\d+)", nm)
    return "CONTADO_2" if (m and int(m.group(1)) >= 2) else "CONTADO"

# ── Datos base ────────────────────────────────────────────────────────────
tals = q("select id, nombre, num_digitos from taloneras where tipo = 'CONTADO' order by nombre")
vends = q("select id, nombre from vendedores order by nombre")
entregas = q("select vendedor_id, talonera_nombre, desde, hasta from entregas_caja")
liq = q("""select lv.vendedor_id, lci.talonera_id, lci.numero
             from liquidacion_contado_items lci
             join liquidaciones_vendedor lv on lv.id = lci.liquidacion_id""")
asig = q("""select id, numero_especial, talonera_especial_id,
                   numero_especial_2, talonera_especial_2_id, comprador_id
              from boletas
             where numero_especial is not null or numero_especial_2 is not null""")

nombres_tal = {(t["nombre"] or "").strip().lower(): t["id"] for t in tals}
vnom = {v["id"]: v["nombre"] for v in vends}

# nombres de entrega que NO matchean ninguna talonera CONTADO ni COMUN
tal_all = q("select nombre, tipo from taloneras")
nom_all = {(t["nombre"] or "").strip().lower(): t["tipo"] for t in tal_all}

print("=" * 78)
print("TALONERAS CONTADO")
print("=" * 78)
for t in tals:
    print(f"  id={t['id']:<4} {t['nombre']:<24} rol={rol(t['nombre'])}  digitos={t['num_digitos']}")
if not tals:
    print("  (!!) NO HAY NINGUNA TALONERA tipo CONTADO -> el desplegable siempre va a estar vacio.")

print()
print("=" * 78)
print("NOMBRES USADOS EN 'ENTREGAR A CAJA' QUE NO COINCIDEN CON NINGUNA TALONERA")
print("=" * 78)
huerfanos = sorted({(e["talonera_nombre"] or "").strip() for e in entregas
                    if (e["talonera_nombre"] or "").strip().lower() not in nom_all})
if huerfanos:
    for h in huerfanos:
        print(f"  (!!) '{h}'  -> ninguna talonera se llama asi (el match es por NOMBRE exacto)")
else:
    print("  OK: todos los nombres de entrega matchean una talonera.")

# ── Asignados por talonera ────────────────────────────────────────────────
asignados = {}   # tal_id -> {numero: boleta_id}
for b in asig:
    if b["talonera_especial_id"] and b["numero_especial"] is not None:
        asignados.setdefault(b["talonera_especial_id"], {})[int(b["numero_especial"])] = b["id"]
    if b["talonera_especial_2_id"] and b["numero_especial_2"] is not None:
        asignados.setdefault(b["talonera_especial_2_id"], {})[int(b["numero_especial_2"])] = b["id"]

print()
print("=" * 78)
print("POOL POR VENDEDOR  (lo que ve el desplegable del socio)")
print("=" * 78)
for v in vends:
    vid = v["id"]
    ent_v = [e for e in entregas if e["vendedor_id"] == vid]
    liq_v = [l for l in liq if l["vendedor_id"] == vid]
    if not ent_v and not liq_v:
        continue
    print(f"\n--- {v['nombre']} (id={vid})")
    for t in tals:
        tid, tnom = t["id"], (t["nombre"] or "").strip().lower()
        rangos = [(int(e["desde"]), int(e["hasta"])) for e in ent_v
                  if (e["talonera_nombre"] or "").strip().lower() == tnom]
        entregados = set()
        for d, h in rangos:
            if h >= d:
                entregados.update(range(d, h + 1))
        liquidados = {int(l["numero"]) for l in liq_v if int(l["talonera_id"]) == tid}
        ya = set(asignados.get(tid, {}))
        libres = (entregados & liquidados) - ya
        print(f"    {t['nombre']}:")
        print(f"       entregados (Entregar a Caja) : {len(entregados)}  {sorted(entregados)[:12]}{' ...' if len(entregados)>12 else ''}")
        print(f"       liquidados (rendicion)       : {len(liquidados)}  {sorted(liquidados)[:12]}{' ...' if len(liquidados)>12 else ''}")
        print(f"       ya asignados a un socio      : {len(ya & (entregados|liquidados))}")
        print(f"       ==> LIBRES EN EL DESPLEGABLE : {len(libres)}  {sorted(libres)}")
        # Diagnostico
        solo_liq = liquidados - entregados - ya
        solo_ent = entregados - liquidados - ya
        if not libres:
            if solo_liq:
                print(f"       (!!) {len(solo_liq)} numero(s) LIQUIDADOS pero SIN entrega a caja de esta")
                print(f"            talonera para este vendedor: {sorted(solo_liq)[:20]}")
                print( "            -> falta el 'Entregar a Caja' (o el nombre no coincide).")
            if solo_ent:
                print(f"       (!!) {len(solo_ent)} numero(s) ENTREGADOS pero NO liquidados todavia:")
                print(f"            {sorted(solo_ent)[:20]}")
                print( "            -> el vendedor todavia no los rindio en una liquidacion.")
            if not solo_liq and not solo_ent and (entregados or liquidados):
                print( "       (i) todos los numeros ya estan asignados a algun socio.")

# ── Numeros liquidados que quedaron sin talonera CONTADO ──────────────────
ids_contado = {t["id"] for t in tals}
raros = [l for l in liq if int(l["talonera_id"]) not in ids_contado]
if raros:
    print()
    print("=" * 78)
    print("(!!) ITEMS DE CONTADO LIQUIDADOS CONTRA UNA TALONERA QUE NO ES TIPO CONTADO")
    print("=" * 78)
    for l in raros[:40]:
        print(f"   vendedor={vnom.get(l['vendedor_id'])}  talonera_id={l['talonera_id']}  numero={l['numero']}")
    print("   -> Esos numeros NUNCA van a aparecer en el desplegable del socio.")

# ── Detalle de una boleta puntual ─────────────────────────────────────────
if len(sys.argv) > 1:
    num = int(sys.argv[1])
    print()
    print("=" * 78)
    print(f"DETALLE DE LA BOLETA {num:04d}")
    print("=" * 78)
    rows = q("""select b.id, b.numero_principal, b.vendedor_id, b.comprador_id,
                       b.numero_especial, b.talonera_especial_id,
                       b.numero_especial_2, b.talonera_especial_2_id,
                       b.modalidad_liquidacion, c.apellido_nombre, t.nombre as talonera
                  from boletas b
             left join compradores c on c.id = b.comprador_id
             left join taloneras  t on t.id = b.talonera_id
                 where b.numero_principal = %s""", (num,))
    for b in rows:
        print(f"  boleta id={b['id']}  {b['talonera']}  socio={b['apellido_nombre']}")
        print(f"  vendedor: {vnom.get(b['vendedor_id'], '— SIN VENDEDOR —')} (id={b['vendedor_id']})")
        print(f"  modalidad_liquidacion={b['modalidad_liquidacion']}")
        print(f"  CONTADO actual   : num={b['numero_especial']} tal={b['talonera_especial_id']}")
        print(f"  CONTADO 2 actual : num={b['numero_especial_2']} tal={b['talonera_especial_2_id']}")
        if not b["vendedor_id"]:
            print("  (!!) Sin vendedor -> el pool siempre sale vacio.")

con.close()
print("\nListo.")
