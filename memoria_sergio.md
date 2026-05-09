# Memoria sobre Sergio

> Este archivo sirve para que Claude recuerde información importante sobre Sergio entre sesiones.
> Compártelo al inicio de cada conversación para que Claude lo lea.

---

## Datos básicos
- **Nombre:** Sergio
- **Correo:** sergiollamera78@gmail.com

## Preferencias de comunicación
- Habla en español

## Preferencias de trabajo
- Va descubriendo requisitos a medida que prueba en la app — no define todo de antemano
- Prefiere ver el problema funcionando antes de validar
- Directo y al punto, sin rodeos
- Trabaja en Windows con PowerShell/CMD, Python 3.12
- Le gusta ir paso a paso y confirmar antes de avanzar
- Tiene modelos físicos reales (planillas en papel) como referencia — el digital debe replicarlos fielmente
- Ajusta visualmente: ve el resultado en pantalla y pide correcciones puntuales

## Notas adicionales
- Trabaja con planilla de bomberos CDELU 2026-2027 (lista de compradores de bono)

---

## Proyectos y tareas conocidas

### bono-app (app de gestión de bono de bomberos)
- **Ubicación:** `C:\Users\sergi\OneDrive\Escritorio\MeIA\bono-app\`
- **Stack:** Python 3.12 + FastAPI + Jinja2 + Bootstrap 5 + SQLAlchemy 2.0 + SQLite (local) / PostgreSQL (Railway)
- **Levantar:** `py -3.12 -m uvicorn app.main:app --reload` desde la carpeta `bono-app`
- **Login por defecto:** usuario `admin`, contraseña `admin123`
- **Entidades:** Compradores, Taloneras, Boletas, Vendedores, Cobradores, Zonas
- **La app está subida a GitHub y Railway** — el entorno productivo está activo en Railway

#### Despliegue en Railway
- Servicio Bono-Contribucion, conectado a repo GitHub
- PostgreSQL en Railway (postgres-volume), variable DATABASE_URL
- Dockerfile: CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
- SIEMPRE hacer git push para que Railway tome los cambios
- **El sandbox bash NO tiene credenciales de GitHub** → el `git push` siempre debe hacerlo Sergio desde PowerShell en Windows

---

### Fixes técnicos importantes (acumulado)
- Python 3.14 incompatible con SQLAlchemy/passlib → usar siempre py -3.12
- Jinja2 cache bug → templates_config.py con cache_size=0
- bcrypt directo (sin passlib) en app/auth.py
- Starlette nuevo: TemplateResponse(request, "name.html", {ctx_sin_request})
- Pydantic v2 + Form + List[int]: usar siempre request.form().getlist("campo")
- Lock files git stale: `del .git\index.lock` y `del .git\HEAD.lock` desde CMD
- **Escritura de archivos grandes**: usar script Python en bash para evitar truncación del Write/Edit tool
- **Columnas nuevas en ORM (PostgreSQL)**: usar `deferred(Column(...))` si la columna puede no existir aún en la DB al momento del primer SELECT; evita "column does not exist"
- **Migraciones PostgreSQL**: usar `ALTER TABLE x ADD COLUMN IF NOT EXISTS ...` (no soportado en SQLite → detectar dialect con `engine.dialect.name`)
- **Jinja2 + dicts**: acceso por punto (`s.clave`) en Jinja2 lanza `UndefinedError` si la clave no existe en el dict — siempre incluir todas las claves en el dict o usar `s.get("clave", 0)`
- **_stats_bulk**: el dict de stats DEBE incluir la clave `"baja"` aunque sea 0, porque el template accede a `s.baja`
- **Sandbox bash mount stale**: a veces el mount /sessions/.../mnt/ no sincroniza con los Edit del file tool; confiar en lo que devuelve Read y verificar contenido con grep si bash da errores raros. Cuando esto pasa, los cambios SÍ están en disco real (Windows), pero bash no los ve. Sergio debe hacer commit/push desde PowerShell.

---

### Modelo de datos — resumen de relaciones clave

#### Taloneras y boletas
- Cada talonera tiene `num_series` y `offset_series` (separación entre series)
- **PATA 1:** 3 series, offset 1501 → ej: 0001 / 1502 / 3003
- **PATA 2:** 6 series, offset 350 → ej: 4503 / 4853 / 5203 / 5553 / 5903 / 6253
- Función `calcular_numeros(numero_principal, num_series, offset)` en `app/routers/taloneras.py`
- Condiciones de boleta: SIN_VENDER → CAJA → VENDIDO (también BAJA, EN_COBRANZA)
  - BAJA y EN_COBRANZA solo vienen de cobranza, los vendedores no usan BAJA
- `Talonera.num_cuotas` (Integer, default 12, **deferred**) — editable en UI; puede bajar si se regalan cuotas finales con el tiempo
- `Talonera.valor_cuota` (Float, default 0.0) — editable en UI de taloneras
- `Talonera.num_digitos` — COMUN siempre 4 (0001-9999), CONTADO usa 3. CRÍTICO: migración inicial puso DEFAULT 3 para todas; hay migración correctora en startup que hace UPDATE COMUN → 4
- `Talonera.multiplicador` — num_series // 3. PATA 1=1, PATA 2=2, PATA 3=3. Unidad de peso para liquidación

#### Zona-Cobrador (muchos-a-muchos, desde 02/05/2026)
- Tabla `zona_cobradores`: zona_id PK, cobrador_id PK, asignado_en (DateTime)
- Una zona puede tener múltiples cobradores simultáneamente
- `Zona.cobrador_id` legacy: columna en DB ignorada por ORM; datos migrados a zona_cobradores en startup
- `Zona.cobrador_id` (property) → ID del último cobrador (mayor asignado_en)
- `Zona.cobrador` (property) → objeto Cobrador del último asignado
- **CRÍTICO:** ZonaCobrador debe definirse ANTES que Zona y Cobrador en models.py

#### Zona-Vendedor
- `Zona.vendedor_id` FK a vendedores (nullable) — una zona = un vendedor
- Se vincula al crear el primer comprador con esa zona

---

### Módulo Vendedores — actualizado 07/05/2026

#### Modelos nuevos/modificados
- `Vendedor.es_jefe_equipo` (Boolean, default False) — solo un jefe activo a la vez
- `LiquidacionVendedor` (tabla `liquidaciones_vendedor`): registra liquidación de comisión al vendedor
  - Campos: vendedor_id, fecha, cuotas_vendidas, **cuota_1_total**, monto_cuotas, comision_cuotas_pct, comision_cuotas,
    contados_vendidos, monto_contados, comision_contados_pct (default 30%), comision_contados, total_comision, observacion
  - **Posición en models.py:** definida ENTRE Vendedor y Cobrador (relación bidireccional)
- `Boleta.liquidacion_vendedor_id` (FK a liquidaciones_vendedor, nullable) — enlaza boleta con su liquidación
- `Boleta.liquidacion_vendedor` — relación ORM a LiquidacionVendedor
- `EntregaCaja.vendedor_id` (FK a vendedores, nullable) — a qué vendedor se entregó

#### Flujo correcto de boletas por vendedor (CRÍTICO — confirmado con Sergio)
```
SIN_VENDER
  → [Entrega a caja] → CAJA (sin liquidacion_vendedor_id) — boleta en mano del vendedor
  → [Liquidar vendedor] → CAJA (con liquidacion_vendedor_id) — liquidado, pendiente cargar comprador
  → [Cargar comprador en sistema] → VENDIDO — comprador registrado
```
- La liquidación al vendedor se hace sobre boletas en CAJA (sin liq_id) — NO sobre VENDIDO
- Las boletas conservan condición CAJA después de liquidar, solo pasan a VENDIDO cuando se carga el comprador
- Los vendedores NO tienen boletas en BAJA — la baja viene solo de cobranza

#### Modelo de comisión del vendedor — SIMPLIFICADO (confirmado 07/05/2026)
**Unidad base = PATA 1. Todo se calcula en múltiplos de PATA 1.**
- `PATA1_VC` = valor_cuota de la talonera COMUN con multiplicador==1
- `PATA1_NC` = num_cuotas de PATA 1
- **Cuota 1 por boleta** = `multiplicador × PATA1_VC` (PATA 2 = 2 × $15.000 = $30.000)
- **Contado por boleta** = `multiplicador × PATA1_NC × PATA1_VC` (PATA 3 = 3 × 12 × $15.000 = $540.000 → 30% = $162.000)
- **Cuotas extras** (cuota 2, 3, ... cobradas al socio) = cantidad × PATA1_VC
- Backend calcula `pata1_vc` y `pata1_nc` y los pasa al template como constantes JS `PATA1_VC` y `PATA1_NC`
- Cada boleta en `pendientes_json` tiene campo `multiplicador` (int); badge lleva `data-mult`

#### Modal de liquidación — comportamiento
- Boletas agrupadas por PATA con checkboxes; botones Todo/Ninguno por PATA y globales
- Cálculo en tiempo real con PATA1_VC y PATA1_NC como base
- "Valor de cada cuota" en sección extras: auto-fill con PATA1_VC al seleccionar primera boleta; hint "PATA 1: $X.XXX"; botón ↺ para restaurar; se resetea al cerrar modal
- Confirmar deshabilitado hasta seleccionar al menos una boleta

#### Taloneras — campos editables en UI (07/05/2026)
- `valor_cuota` y `num_cuotas` editables en modal Editar y modal Nueva
- Lista de taloneras muestra: `Cuota: $15.000 | 12 cuotas`
- Endpoints `/crear` y `/{id}/editar` aceptan `num_cuotas` como Form param
- Taloneras COMUN nuevas se crean con `num_digitos=4` explícito

#### Router vendedores.py — endpoints clave
- `_stats_bulk(db)`: dict por vendedor con claves `caja`, `liq_pendiente`, `vendido`, `baja`. **Ojo:** desde 09/05/2026 `vendido` cuenta TODAS las boletas con `comprador_id IS NOT NULL` (sin importar condicion). Las claves caja/liq_pendiente/baja siguen contando por condicion específica. Implementación: GROUP BY (vendedor, condicion) para caja/liq/baja + segunda query separada para vendido.
- `GET /vendedores/`: lista vendedores con stats (caja, baja, vendido), jefe_equipo
- `GET /vendedores/{vid}/detalle`: incluye `pendientes_json` (con `multiplicador`), `pata1_vc`, `pata1_nc`
- `POST /vendedores/{vid}/liquidar`: modelo de comisión basado en multiplicador × PATA1
- `POST /vendedores/{vid}/toggle-jefe`: marca jefe (primero resetea todos, luego activa el nuevo)
- `POST /vendedores/entrega-caja`: SIN_VENDER → CAJA + REASIGNAR entre vendedores en CAJA (sin liquidar)
- `POST /vendedores/entrega-caja/{id}/editar` y `eliminar`: gestión del historial de entregas

#### Detalle del vendedor — orden y CONTADO en pool (07/05/2026)
- **Orden jerárquico de PATAs** (función `_pata_sort_key`):
  1. PATA con número (PATA 1, 2, 3...) — numéricamente
  2. Otras COMUN sin número — alfabéticamente
  3. CONTADO al final
- **CONTADO pool**: números entregados al vendedor aún no asignados a boletas; aparecen como badge ★ pero NO son liquidables
- **Flag `contado`**: `b.numero_especial is not None or b.numero_especial_2 is not None`

#### Entrega a Caja — actualizado 06/05/2026
- UI vive en el detalle del vendedor (no en el listado global)
- Dos botones en orden de flujo: Entregar a Caja (rojo) → Liquidar vendedor (verde)
- Backend: SIN_VENDER→CAJA (nuevas) + CAJA sin liq→reasignar (reasignadas). Si total=0 hace rollback y no crea fila
- Para CONTADO: `es_contado=True`, no toca boletas, solo registra rango

#### Templates vendedores
- `vendedores.html`: tabla limpia, doble-click → detalle, badge jefe, stats
- `vendedor_detalle.html`: tarjetas + 2 botones + secciones PATA + tabla entregas + historial liquidaciones + 2 modales

#### 3 estados de boleta en el detalle (colores)
- 🔵 Azul claro (`#e7f1ff`): CAJA sin liq_id — pendiente liquidar
- 🟢 Verde (`#d1e7dd`): CAJA con liq_id — liquidado, pendiente cargar comprador
- 🔵 Azul opaco: VENDIDO — comprador registrado

#### Migraciones en main.py (startup) — todas en try/except
- `es_jefe_equipo` en vendedores, `vendedor_id` en entregas_caja
- CREATE TABLE `liquidaciones_vendedor`
- `liquidacion_vendedor_id` en boletas
- `num_cuotas` en taloneras (INTEGER DEFAULT 12)
- `num_digitos` en taloneras (INTEGER DEFAULT 3) + corrección UPDATE COMUN→4
- `cuota_1_total` en liquidaciones_vendedor
- `numero_especial_2` y `talonera_especial_2_id` en boletas
- **CRÍTICO**: todas en try/except. `inspector.get_columns()` fuera de try/except crashea el startup

---

### Módulo Cobranza (/cobranza/) — implementado 01-02/05/2026
- Página principal: selector mes/año, tarjetas por cobrador
- Planilla: template standalone hoja A4, 3 columnas, 4 bloques de 10 filas (120 slots)
- Cuotas anticipadas: celda negra + X blanca
- Tabs: Planillas | Emplantillado | Liquidación
- Modelos: Planilla (cobrador_id, numero, mes, anio, comision_pct), Liquidacion, LiquidacionDetalle
- **Filtros de boletas activas** (cobranza.py): usan `condicion IN [VENDIDO, EN_COBRANZA]` — CORRECTO. NO incluye CAJA-Al-contado (ya pagada) ni BAJA. NO tocar.

---

### Talonera Especial CONTADO — ETAPA 1 + ETAPA 2 (07/05/2026)

**Modelo Boleta — dos slots:**
- `Talonera.tipo` — "COMUN" o "CONTADO"
- Slot 1: `Boleta.numero_especial` + `Boleta.talonera_especial_id`
- Slot 2: `Boleta.numero_especial_2` + `Boleta.talonera_especial_2_id` (ambos deferred)
- `Talonera.boletas` con `foreign_keys="Boleta.talonera_id"` (3 FK a taloneras)

**Reglas de negocio:**
- CONTADO = pool para sorteo extra al pagar al contado total
- CONTADO 2 VECES = pool para sorteo extra al pagar en 2 cuotas
- Modalidades: cuotas (sin extras) | 1pago (ambos slots) | 2pagos (solo slot 2)

**ETAPA 2 — asignación a boletas:**
- `GET /compradores/boleta/{bid}/contado-disponibles` → numeros_libres por talonera del vendedor
- `POST /compradores/{id}/editar` procesa `modalidad_<bid>`, `te_<bid>`, `ne_<bid>`, `te2_<bid>`, `ne2_<bid>`
- Validación: `_esta_libre(num, talonera_id, except_boleta_id)`

**Pendiente — ETAPA 3:** sorteo CONTADO cruzando Boleta.numero_especial y numero_especial_2

---

### Otras funcionalidades implementadas
- Login/logout JWT (cookies)
- ABM Compradores, Vendedores, Cobradores, Zonas, Taloneras, Boletas
- Generación de boletas por rango (sin duplicar)
- Auto-asignación de cobrador al guardar comprador (por zona)
- Exportación Excel socios: GET /compradores/exportar (openpyxl==3.1.2)
- Dashboard /reportes/: cards + tablas por talonera, zona, top vendedores/cobradores
  - **Top Vendedores** (corregido 08/05/2026): cuenta TODA boleta cargada con socio (`Boleta.comprador_id IS NOT NULL`), sin filtrar por condición. Query va por `Boleta.vendedor_id` (no via Zona — la cadena Vendedor→Zona→Comprador→Boleta excluía boletas de zonas sin vendedor o de vendedores que vendían fuera de su zona). Ponderado por `Talonera.multiplicador` (PATA 1=1, PATA 2=2, PATA 3=3, PATA 4=4, PATA 8=8, etc.). **CRÍTICO: NO filtrar por `condicion=VENDIDO`** — las boletas pueden derivar a EN_COBRANZA (asignadas a planilla), CAJA (al contado pagado), o BAJA, y deben seguir contándose. El criterio correcto del usuario: "contarse cuando son cargados con los socios".
- Módulo Sorteos: ABM Tómbola Nocturna Entre Ríos (SEMANAL/MENSUAL/FINAL/CONTADO) — carga manual
- Módulo Ganadores: cruza 4c/3c/2c con exclusión
- Color por PATA: Talonera.color, picker en UI

---

### Preferencias de UI consolidadas
- Tablas: table-sm + font-size: 0.8rem + filas ~28px (compactas)
- ~~Filtros inline por columna~~ **Ya no — desde 09/05/2026** se usa un solo buscador con selector de columna ("Todo / N° Boleta / Apellido y Nombre / Dirección / …")
- Sortable en todos los encabezados
- Fecha: almacena ISO en data-val, muestra dd/mm/yyyy
- Dirección: CSS text-transform: uppercase en la celda
- Sin botón eliminar en tablas — solo lápiz; el eliminar queda en la pantalla de edición
- **Vendedores**: Entrega a Caja vive en el detalle del vendedor (acceso por doble-click en listado)
- **PATA 1, PATA 2, etc. → mostrar como X1, X2** (preferencia visual desde 09/05/2026, solo display via `replace('PATA ', 'X')`. La DB sigue con "PATA N")

---

### Sesión 09/05/2026 — Auditoría completa del criterio "vendidas/vendido"

**Bug original detectado:** el dashboard "Por Talonera" mostraba 0 vendidas para PATA 2 y PATA 8, aunque había boletas Al contado y En cobranza con socio cargado. Causa: filtro `condicion = VENDIDO`, pero las boletas pasan a `CAJA` (Al contado) o `EN_COBRANZA` (asignadas a planilla) tras cargar el socio.

**Lugares corregidos** (todos cambiaron `condicion = VENDIDO` → `comprador_id IS NOT NULL`):
1. `app/routers/reportes.py` líneas 38-49 → `vendidas` en stats_por_talonera (dashboard "Por Talonera")
   - También `en_caja` ahora cuenta solo `CAJA AND comprador_id IS NULL` (físicas en mano sin cargar)
   - `sin_vender` calculado por consulta directa (no por resta) para evitar overlap
2. `app/routers/reportes.py` línea 171 → `vendidas_zona` en stats_por_zona (dashboard "Por Zona")
3. `app/routers/taloneras.py` línea 306 → validación al ELIMINAR talonera (riesgo serio: permitía borrar boletas con comprador asignado)
4. `app/templates/taloneras.html` línea 130 → badge "X vend." en lista de taloneras: `selectattr('comprador_id')` en lugar de `selectattr('condicion','equalto','VENDIDO')`
5. `app/routers/vendedores.py` `_stats_bulk` → `vendido` cuenta todas las boletas del vendedor con socio (incluye VENDIDO + CAJA-Al-contado + EN_COBRANZA + BAJA). `baja` queda como sub-estado informativo (subconjunto de `vendido`). **Confirmado por Sergio:** una boleta cargada por el vendedor sigue siendo "vendida del vendedor" aunque después pase a EN_COBRANZA o BAJA.

**Filtros revisados que SÍ están bien (no se tocaron):**
- `cobranza.py` (5 lugares con `[VENDIDO, EN_COBRANZA]`) — correcto, las Al contado ya pagadas no van a planilla
- Asignaciones `b.condicion = VENDIDO` en `compradores.py` — son writes, no filtros
- Badge individual de boleta en `ganadores.html:94` — display por fila

**Nota visual:** en el dashboard "Por Zona", las columnas "Taloneras" y "Vendidas" ahora muestran el mismo número (ambas suman boletas con socio ponderadas por multiplicador). Si Sergio quiere eliminar una columna o darle otra semántica, pendiente de validar.

### Sesión 09/05/2026 — UI Socios

**Buscador único** (`app/templates/compradores.html`):
- Reemplazó la fila de filtros por columna (9 inputs) por un solo input + select de columna
- Opciones del select: Todo / N° Boleta / Apellido y Nombre / Dirección / Zona / Fecha compra / Vendedor / Cobrador / Teléfono / Condición
- "Todo" busca en todas las columnas. Filtro client-side en tiempo real, no recarga.
- Botón ✕ rojo para limpiar.
- Función JS: `aplicarBusqueda()` reemplaza la antigua `applyFilters()`.

**Mostrar X1/X2 en lugar de PATA 1/PATA 2:**
- En `compradores.html`: tabs, badges de filas y badge del footer usan `{{ nombre | replace('PATA ', 'X') }}`.
- La DB no se modifica — sigue "PATA 1", "PATA 2", etc. Es solo display.
- El backend filtra por nombre real ("PATA 1") en `?pata=...`, así que los hrefs envían el nombre crudo.
- **Pendiente** (si Sergio lo pide): extender a otras pantallas (Reportes, Taloneras, Vendedores, Cobranza, Boletas) — lo más limpio sería un filtro Jinja personalizado registrado en `templates_config.py` (ej. `pata_label`).

---

### Pendientes / próximos pasos
- **Talonera CONTADO Etapa 3**: sorteo cruzando Boleta.numero_especial y numero_especial_2
- Sección especial de Recaudado (separada del dashboard)
- Posible importación de datos desde el Excel original
- Mejorar scraper automático (Selenium o Playwright)
- **Validar con Sergio**: las columnas "Taloneras" y "Vendidas" en dashboard Por Zona quedaron con el mismo dato — ¿eliminar una o redefinir?
- **Posible extensión visual**: aplicar el reemplazo "PATA N" → "XN" en todas las pantallas de la app (filtro Jinja global)

---
*Última actualización: 09 de mayo de 2026 — Auditoría criterio "vendidas" en 5 lugares (reportes/taloneras/vendedores), buscador único en Socios, display "X1/X2" en lugar de "PATA 1/PATA 2"*
