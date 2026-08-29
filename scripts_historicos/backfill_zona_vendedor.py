"""
Backfill: asigna vendedor_id a cada zona según el vendedor más frecuente
en las boletas de los compradores de esa zona.

Ejecutar UNA vez desde la carpeta bono-app:
    py -3.12 backfill_zona_vendedor.py
"""
import sqlite3
from collections import Counter
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "bonos.db")
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Obtener todas las zonas
cur.execute("SELECT id, nombre, vendedor_id FROM zonas ORDER BY nombre")
zonas = cur.fetchall()

print(f"{'ZONA':<20} {'VENDEDOR ASIGNADO':<25} {'ACCIÓN'}")
print("-" * 65)

for zona in zonas:
    zid   = zona["id"]
    znombre = zona["nombre"]
    vid_actual = zona["vendedor_id"]

    # Vendedores de boletas de compradores en esta zona
    cur.execute("""
        SELECT b.vendedor_id, v.nombre, COUNT(*) as cnt
        FROM boletas b
        JOIN compradores c ON c.id = b.comprador_id
        JOIN vendedores v  ON v.id = b.vendedor_id
        WHERE c.zona_id = ? AND b.vendedor_id IS NOT NULL
        GROUP BY b.vendedor_id
        ORDER BY cnt DESC
    """, (zid,))
    rows = cur.fetchall()

    if not rows:
        print(f"{znombre:<20} {'(sin boletas con vendedor)':<25} sin cambios")
        continue

    # Tomar el vendedor más frecuente
    vendedor_id_nuevo = rows[0]["vendedor_id"]
    vendedor_nombre   = rows[0]["nombre"]
    detalle = ", ".join(f"{r['nombre']}({r['cnt']})" for r in rows)

    if vid_actual == vendedor_id_nuevo:
        accion = "ya estaba OK"
    else:
        cur.execute("UPDATE zonas SET vendedor_id = ? WHERE id = ?", (vendedor_id_nuevo, zid))
        accion = f"→ asignado (candidatos: {detalle})"

    print(f"{znombre:<20} {vendedor_nombre:<25} {accion}")

conn.commit()
conn.close()
print("\n✓ Listo. Revisá la tabla Zonas en la app para corregir cualquier asignación incorrecta.")
