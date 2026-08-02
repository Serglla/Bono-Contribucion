#!/usr/bin/env python3
"""
Limpia los socios FANTASMA que quedaron por los reintentos del "Error de conexión".

QUE SON: cuando el servidor tardaba y el navegador cortaba el fetch, el alta ya
había entrado igual. Al reintentar, se creaba OTRO socio y la boleta pasaba al
último. Quedaron socios sin ninguna boleta: sin zona, sin fecha, sin vendedor,
sin cuotas. En la pantalla de Socios se ven como filas con "—" en todo.

QUE HACE: busca compradores sin NINGUNA boleta y los borra. Nada más apunta a
compradores salvo boletas.comprador_id, así que borrarlos no arrastra otros datos.

Por defecto NO borra: lista lo que encontró para que lo revises.

USO (PowerShell, desde D:\\MeIA\\bono-app):

  $env:DATABASE_URL="postgresql://...proxy.rlwy.net:PUERTO/railway"   # URL PUBLICA

  py -3.12 limpiar_socios_huerfanos.py           # solo lista (no toca nada)
  py -3.12 limpiar_socios_huerfanos.py --borrar  # borra, pidiendo confirmacion
  py -3.12 limpiar_socios_huerfanos.py --borrar --yes   # sin preguntar

HACE UN BACKUP ANTES. En serio: backup.bat
"""

import os
import sys

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    sys.exit("Falta psycopg2:  py -3.12 -m pip install psycopg2-binary")


def get_url():
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        sys.exit('Falta DATABASE_URL.  $env:DATABASE_URL="postgresql://..."')
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if "railway.internal" in url:
        sys.exit("Esa es la URL interna de Railway. Usa DATABASE_PUBLIC_URL (*.proxy.rlwy.net).")
    return url


SQL_HUERFANOS = """
SELECT c.id, c.apellido_nombre, c.direccion, c.telefono, c.zona_id
FROM compradores c
LEFT JOIN boletas b ON b.comprador_id = c.id
WHERE b.id IS NULL
ORDER BY c.apellido_nombre, c.id
"""

# Para cada huerfano, cuantos socios CON boleta comparten el mismo nombre.
# Si hay al menos uno, es casi seguro un duplicado por reintento.
SQL_GEMELOS = """
SELECT c.apellido_nombre, COUNT(DISTINCT c.id) AS con_boleta
FROM compradores c
JOIN boletas b ON b.comprador_id = c.id
WHERE c.apellido_nombre = ANY(%s)
GROUP BY c.apellido_nombre
"""


def main():
    borrar = "--borrar" in sys.argv
    sin_preguntar = "--yes" in sys.argv

    conn = psycopg2.connect(get_url())
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM compradores")
    total_socios = cur.fetchone()[0]

    cur.execute(SQL_HUERFANOS)
    huerfanos = cur.fetchall()

    if not huerfanos:
        print("No hay socios huerfanos. La base esta limpia.")
        print("(%d socios en total, todos con al menos una boleta.)" % total_socios)
        conn.close()
        return

    nombres = list({h[1] for h in huerfanos})
    cur.execute(SQL_GEMELOS, (nombres,))
    con_boleta = {r[0]: r[1] for r in cur.fetchall()}

    dup = [h for h in huerfanos if h[1] in con_boleta]
    solos = [h for h in huerfanos if h[1] not in con_boleta]

    print("=" * 70)
    print("SOCIOS SIN NINGUNA BOLETA:  %d  (de %d socios en total)" % (len(huerfanos), total_socios))
    print("=" * 70)

    if dup:
        print("\nDUPLICADOS — hay otro socio con el mismo nombre que SI tiene boleta.")
        print("Estos son los fantasmas de los reintentos. Borrarlos es seguro.\n")
        print("   %-6s %-34s %-24s %s" % ("ID", "APELLIDO Y NOMBRE", "DIRECCION", "TEL"))
        for cid, nom, dire, tel, _z in dup:
            print("   %-6s %-34s %-24s %s" % (cid, (nom or "")[:34], (dire or "-")[:24], tel or "-"))

    if solos:
        print("\n" + "!" * 70)
        print("REVISAR A MANO — sin boleta y SIN otro socio del mismo nombre.")
        print("Puede ser un alta que quedo por la mitad, o un socio real cuya")
        print("boleta se borro. Fijate si alguno te suena antes de borrarlo.")
        print("!" * 70 + "\n")
        print("   %-6s %-34s %-24s %s" % ("ID", "APELLIDO Y NOMBRE", "DIRECCION", "TEL"))
        for cid, nom, dire, tel, _z in solos:
            print("   %-6s %-34s %-24s %s" % (cid, (nom or "")[:34], (dire or "-")[:24], tel or "-"))

    if not borrar:
        print("\n" + "-" * 70)
        print("No se borro nada (modo lista).")
        print("Para borrar los %d duplicados seguros:" % len(dup))
        print("   py -3.12 limpiar_socios_huerfanos.py --borrar")
        if solos:
            print("\nOJO: --borrar elimina TAMBIEN los %d de 'revisar a mano'." % len(solos))
            print("Si querés conservar alguno, cargale la boleta primero y volve a correr esto.")
        conn.close()
        return

    print("\n" + "=" * 70)
    print("Se van a BORRAR %d socios (%d duplicados + %d a revisar)."
          % (len(huerfanos), len(dup), len(solos)))
    print("=" * 70)
    if not sin_preguntar:
        if input('Escribi "BORRAR" para confirmar: ').strip() != "BORRAR":
            print("Cancelado. No se toco nada.")
            conn.close()
            return

    ids = [h[0] for h in huerfanos]
    try:
        # Red de seguridad: aunque ya filtramos por "sin boletas", el DELETE
        # vuelve a exigirlo. Si entre el listado y el borrado alguien le cargo
        # una boleta a uno de estos, no se borra.
        cur.execute("""
            DELETE FROM compradores c
            WHERE c.id = ANY(%s)
              AND NOT EXISTS (SELECT 1 FROM boletas b WHERE b.comprador_id = c.id)
        """, (ids,))
        borrados = cur.rowcount
        conn.commit()
        print("\nBorrados: %d socios." % borrados)
        if borrados != len(ids):
            print("(%d se salvaron porque mientras tanto les cargaron una boleta.)"
                  % (len(ids) - borrados))
        cur.execute("SELECT COUNT(*) FROM compradores")
        print("Quedan %d socios, todos con boleta." % cur.fetchone()[0])
    except Exception as e:
        conn.rollback()
        sys.exit("\nERROR: %s\nNo se borro nada." % e)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
