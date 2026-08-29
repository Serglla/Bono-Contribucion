# Scripts históricos

Scripts de un solo uso que **ya se aplicaron** en producción. Se guardan como
registro de qué se tocó y cuándo, no para volver a correrlos.

| script | qué hizo |
|---|---|
| `migrar_vendedor_zona.py` | migración zona → vendedor (01/05/2026) |
| `backfill_zona_vendedor.py` | backfill del vendedor por zona (01/05/2026) |
| `crear_liquidacion_ariel_10052026.py` | liquidación de ARIEL al 10/05/2026 |
| `rearmar_liquidaciones_10052026.py` | rearmó todas las liquidaciones al 10/05/2026 |
| `fix_liq12_ariel.py` | corrección de la liquidación 12 de ARIEL (16/05/2026) |
| `fix_multiplicadores_ariel.py` | corrección de multiplicadores (16/05/2026) |
| `ver_liquidacion_ariel.py` | consulta de diagnóstico (11/05/2026) |
| `check_liquidados_vendedor.py` | chequeo de liquidados por vendedor (28/06/2026) |
| `resync_liquidaciones_equiv.py` | resync de `cuotas_equiv` (28/06/2026) |
| `fix_cuota2_institucion_8689_8694.py` | cuota 2 institución, 8689-8694 (01/07/2026) |
| `fix_pata0_tanda_8924.py` | tanda PATA 0 desde la 8924 (14/08/2026) |

## OJO si alguna vez hay que correr uno

Todos hacen `ROOT = Path(__file__).resolve().parent` y meten `ROOT` en `sys.path`
para importar `app`. Desde esta subcarpeta **eso ya no encuentra `app/`**: hay que
cambiarlo por `ROOT.parent` o copiar el script a la raíz de `bono-app/` antes de
ejecutarlo.

## Las herramientas que SÍ se siguen usando

Quedan en la raíz de `bono-app/`, con su `.bat`:

- `backup.bat` / `backup_pg.py` — backup de la base.
- `auditar_liquidaciones.bat` / `.py` — control de rutina de las liquidaciones.
- `asignar_huerfanas.bat` / `.py` — boletas con socio pero sin liquidación.
- `limpiar_socios.bat` / `limpiar_socios_huerfanos.py` — socios sin boleta.
