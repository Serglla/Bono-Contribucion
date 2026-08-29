"""
Migración: agrega columna vendedor_id a la tabla zonas
Ejecutar UNA sola vez desde la carpeta bono-app:
    py -3.12 migrar_vendedor_zona.py
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "bonos.db")

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# Verificar columnas actuales
cur.execute("PRAGMA table_info(zonas)")
cols = [row[1] for row in cur.fetchall()]
print("Columnas actuales:", cols)

if "vendedor_id" in cols:
    print("✓ La columna vendedor_id ya existe. No se requiere migración.")
else:
    cur.execute("ALTER TABLE zonas ADD COLUMN vendedor_id INTEGER REFERENCES vendedores(id)")
    conn.commit()
    print("✓ Columna vendedor_id agregada exitosamente.")

# Confirmar resultado
cur.execute("PRAGMA table_info(zonas)")
cols_final = [row[1] for row in cur.fetchall()]
print("Columnas finales:", cols_final)

conn.close()
