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
- **Funciones truncadas silenciosamente** (10/05/2026): un Edit/Write previo dejó `guardar_resultado` cortada después de un comentario `# Normaliza` sin código. FastAPI devolvía `null` (200 OK) → el JS caía en catch con "Error al guardar". Auditar archivos sospechosos cuando aparezcan errores raros 200/null. Auditoría completa del 10/05/2026 verificó que ya no hay otros archivos truncados.
- **Modales dentro de `<div class="tab-pane">` inactivos NO se ven**: los `tab-pane` que no son la activa tienen `display:none`, y los hijos heredan eso (incluido un `<div class="modal">` que está dentro). Los modales SIEMPRE deben ir a nivel raíz del bloque content (o al `<body>`), nunca anidados dentro de un tab-pane que pueda estar inactivo.
- **`data-bs-toggle="collapse"` puede fallar silencioso**: si por algún motivo el data-api de Bootstrap no engancha, el botón parece no hacer nada. Fallback robusto: usar `onclick="bootstrap.Collapse.getOrCreateInstance(el, {toggle:false}).toggle()"` directo.

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
- `vendedor_detalle.html`: tarjetas + leyenda + **nav-tabs (Caja / Liquidaciones)** + modales (ver Sesión 10/05/2026 cont. 4)

#### 3 estados de boleta en el detalle (colores)
- Azul claro (`#e7f1ff`): CAJA sin liq_id — pendiente liquidar
- Verde (`#d1e7dd`): CAJA con liq_id — liquidado, pendiente cargar comprador
- Azul opaco: VENDIDO — comprador registrado

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
- **Navbar horizontal arriba** (10/05/2026) — antes era sidebar lateral 220px. Ahora topbar Bootstrap 5 sticky-top, brand a la izquierda + items + user/Salir a la derecha. Mobile: colapsa en hamburguesa con bloque de usuario al pie. Mismos colores (#1a2a4a azul oscuro + #e63946 rojo activo).
- **Sub-secciones dentro de páginas grandes → preferir nav-tabs Bootstrap** sobre acordeón o segmented. **Listas históricas → modales en vez de collapse inline** (ver sesión 10/05/2026 cont. 4 sobre detalle de vendedor).

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

### Sesión 10/05/2026 — Navbar horizontal + Sorteos rediseñados

#### Navbar horizontal (`app/templates/base.html`)
- Antes: sidebar lateral fijo de 220px que en celular comía mucho ancho
- Ahora: topbar Bootstrap 5 (`navbar-expand-lg`, `sticky-top`)
- Desktop: brand izquierda + items horizontales + user/Salir derecha
- Mobile (<992px): hamburguesa que colapsa el menú vertical, bloque de usuario al pie con borde superior
- Mantiene mismos colores (#1a2a4a azul / #e63946 rojo activo) y todos los `has_permission`
- `main-content` ahora ocupa todo el ancho

#### Módulo Sorteos — agrupado por mes (acordeón)
- Backend (`app/routers/sorteos.py`): el `listar` ahora también devuelve `sorteos_por_mes` (lista de dicts con `key`, `year`, `month`, `label`, `sorteos`, `total`, `con_resultado`) y `mes_actual_key`
- Helper: constante `_MESES_ES` y orden cronológico — mes actual y futuros primero, pasados después (más reciente arriba)
- Template (`app/templates/sorteos.html`): la tabla larga se reemplazó por accordion Bootstrap; mes actual abierto por defecto, otros colapsados; podés abrir varios a la vez (sin `data-bs-parent`)
- Cabecera de mes muestra: ícono calendar3, label "Mayo 2026", badge "N sorteos", badge "X con resultado" o "Sin resultados aún", botón "Extracto" (solo si `con_resultado > 0`)
- **HTML válido**: el `<a>` Extracto va FUERA del `<button>` de accordion (position:absolute, right:48px) — anidar `<a>` dentro de `<button>` es inválido
- JS de filtros (`aplicarFiltros`): selecciona `.tabla-sorteos tbody tr[data-tipo]`, oculta meses sin filas visibles, abre automáticamente meses con coincidencias cuando hay filtro activo
- Selectores antiguos `#tablaSorteos` ya no existen — todo usa `.tabla-sorteos`

#### Generación de Extracto Mensual (Fase 2)
- Endpoint nuevo: `GET /sorteos/extracto/{year}/{month}` en `sorteos.py`
- Lógica de cruce de números ganadores vs boletas:
  - **SEMANAL / MENSUAL / CONTADO**: 1° premio
  - **FINAL**: hasta el 3° premio
  - Cruza con TODAS las cifras configuradas en el sorteo (ej. cifras="3,4" → cruza por 3 y por 4)
  - Coincidencia por sufijo: últimos N dígitos del número de boleta = últimos N del premio (formateado a 4)
  - Filtros: `fecha_venta < fecha_sorteo` y `comprador_id IS NOT NULL`
  - **Deduplica por boleta** (si una boleta coincide en 3 y 4 cifras, sale una sola vez)
  - Para tipo CONTADO: cruza con `numero_especial` y `numero_especial_2` (NO `numero_principal`)
  - Excluye boletas de talonera tipo CONTADO (son pool, no boletas reales)
- Helpers nuevos: `_TIPO_LABEL_PLURAL`, `_ultimo_dia_mes`, `_premios_por_tipo`, `_build_ganador`
- Carga las boletas en una sola query con `joinedload(comprador, talonera)` y filtra por fecha_venta < max(fechas_sorteos)
- Agrupa sorteos del mes por tipo, arma un bloque por tipo (orden: SEMANAL, MENSUAL, CONTADO, FINAL)

#### Plantilla A4 imprimible (Fase 3)
- Template nuevo: `app/templates/sorteo_extracto.html`
- Tipografía Times New Roman para imitar el formato impreso original
- Vista pantalla: hoja simulada 210mm × 297mm con sombra
- Toolbar (oculta en print): selector **columnas 1/2/3** y **tamaño S/M/L** con persistencia en `localStorage` (keys `extracto_cols` y `extracto_size`)
- Multi-columna CSS para los ganadores (`column-count: var(--cols)`); por defecto 2 columnas, tamaño M
- `break-inside: avoid` por línea de ganador → no se corta un nombre entre columnas/páginas
- `overflow-wrap: anywhere` para nombres largos
- `@page { size: A4 portrait; margin: 8mm 10mm }`
- Si un mes tiene varios bloques de tipo distinto (ej. semanal + final), `page-break-before: always` entre bloques → cada uno en su hoja
- Tamaño S = 8.5pt para máxima densidad (~70-80 ganadores por hoja)
- Encabezado: "BOMBEROS VOLUNTARIOS / DE CONCEPCION DEL URUGUAY / TOMBOLA NOCTURNA DE E. RIOS / EXTRACTO SORTEOS [TIPO] DEL MES DE [MES] [AÑO] / A 4 Y 3 CIFRAS."
- Pie: "PEDIDOS Y CONSULTAS AL 3442-484286" (hardcodeado por decisión de Sergio)
- Línea de ganador: `{nombre}-{direccion}` en mayúsculas, ordenado alfabéticamente

#### Bug fix — `guardar_resultado` truncada
- Encontrado al cargar resultado de sorteo en producción (Railway): "Error al guardar" con status 200 pero respuesta `null`
- Causa: la función `guardar_resultado` en `sorteos.py` quedó cortada después de un comentario `# Normaliza` sin cuerpo. FastAPI devolvía implícit `None` → JSON `null` → el JS hacía `data.ok` sobre null → TypeError → catch → toast genérico
- Reconstrucción: normaliza cada número con `zfill(4)`, padding/truncado a `s.num_premios`, valida al menos un número distinto de `0000`, `try/except` con `db.rollback()`, retorna `{"ok": True, "numeros": nums_norm}`
- Auditoría completa de los 19 .py y 25 templates del proyecto: ningún otro archivo está truncado

---

### Sesión 10/05/2026 (cont.) — Ganadores y Extracto: ajustes UX

#### Fix — Conteo de "ganadores" solo cuenta boletas con socio cargado
- **Problema:** badge "N ganadores" en `/sorteos/{id}/ganadores` y per-grupo contaba TODAS las boletas que coincidían numéricamente, incluyendo las que no tenían comprador (socio) asignado. Sergio: "esas taloneras no tienen comprador aun, no se les cargó un socio, o sea no hay ganadores".
- **Fix backend** (`app/routers/sorteos.py`, función `ganadores`): cada grupo ahora trae también `ganadores_reales = sum(1 for f in filas if f["comprador"])`. El `total_ganadores` que va al header suma los `ganadores_reales` de todos los grupos.
- **Fix template** (`app/templates/ganadores.html`):
  - Header (al lado de fecha): badge amarillo "N ganadores" SOLO aparece si `total_ganadores > 0`.
  - Por grupo: si `ganadores_reales > 0` → badge amarillo "N ganadores"; si hay coincidencias pero ningún socio → badge gris claro "N coincidencias sin socio" (informativo); si no hay coincidencias → "Sin ganadores".

#### Fix — Extracto: selectores de columnas/tamaño no daban feedback visible cuando no había ganadores
- **Problema:** los botones S/M/L y 1/2/3 columnas funcionaban (toggleaban active y data-attrs), pero las CSS variables `--ganador-size` y `--cols` solo aplicaban a `.ext-ganadores`. Si el bloque estaba vacío ("Sin ganadores en este bloque"), no se veía cambiar nada.
- **Fix CSS** (`app/templates/sorteo_extracto.html`):
  - `.ext-vacio` ahora usa `font-size: var(--ganador-size, 10pt)` → la línea italic responde al selector de tamaño.
  - `.ext-premios` también responde al tamaño con `calc(var(--ganador-size, 10pt) + 0.5pt)`.
  - Aviso info en cabecera cuando `total_ganadores_mes == 0`, explicando qué afectan los controles.

#### Feature — Selector "Copias por hoja" 1/4/9 con replicación en grilla
- **Caso de uso:** Sergio imprime el extracto en formato 9-up (3×3) en una sola A4 para distribución física (cada copia se recorta).
- **Toolbar:** nuevo grupo de botones "Copias/hoja: 1 / 4 / 9" entre Tamaño y el botón Imprimir, con persistencia en `localStorage` (key `extracto_copies`).
- **HTML:** cada bloque ahora vive dentro de `<div class="extracto-grid">`. El bloque interno tiene clase `extracto-bloque-original` para que el JS lo identifique al clonar.
- **JS** función `replicarBloques(n)`: clona el bloque original N-1 veces dentro de cada `.extracto-grid`. Los clones llevan clase `extracto-bloque-clone`. Se llama al cambiar copies o al restaurar de localStorage.
- **CSS multi-up:**
  - 4-up: grid 2×2, gap 4mm, altura 281mm (A4 menos padding), font-sizes ~7-10pt, borde dashed por copia.
  - 9-up: grid 3×3, gap 2mm, altura 281mm, font-sizes ~5-7pt. Premios cambian a `display: block` (uno por línea). Ganadores forzados a `column-count: 1` (con `!important`).
  - En multi-up se oculta `.stat-line` (queda raro replicada).
- **Print:** `@media print` añade `height: 100vh` a `.extracto-grid` con copies > 1 + `page-break-after: always` para que cada grilla ocupe una hoja completa.
- **Compatibilidad:** se mantiene 1-up como default. El page-break entre tipos (semanal + final) ahora usa `.extracto-grid + .extracto-grid` en lugar de `.extracto-bloque + .extracto-bloque`.

---

### Sesión 10/05/2026 (cont. 2) — Modal Liquidar: select compacto + Detalle ponderado

#### Modal "Liquidar — {vendedor}" (`vendedor_detalle.html`, función `renderBoletas`)
- **Etiquetas del select de modalidad acortadas** para que entren al lado del badge de cada boleta sin romper el layout:
  - `Cuotas` → **Cuo**
  - `Contado` → **Cont**
  - `Contado 2 veces` → **Cont en 2**
- **Estilo del select más justo:** `font-size:.68rem`, `padding:.05rem 1rem .05rem .3rem`, `min-width:0`, flecha del dropdown achicada con `background-size:10px 8px` y pegada al borde con `background-position:right .15rem center`
- Los `option value` siguen siendo `cuotas` / `contado` / `contado2` — el backend no se tocó

#### Modal "Detalle de liquidación" (`vendedor_detalle.html`, función que arma el detalle desde `/vendedores/liquidaciones/{liq_id}/detalle`)
- **Bug:** el badge "Taloneras liquidadas" mostraba el conteo crudo de boletas (ej. 25 = 17+7+1) sin aplicar el multiplicador. Mismo problema en cada cabecera de PATA donde decía "X boleta/s".
- **Fix:** se usa `b.multiplicador` (que el endpoint ya devuelve) para ponderar:
  - **Badge total** = `Σ b.multiplicador` de todas las boletas. Tooltip aclara cuántas boletas reales hay.
  - **Por PATA** = ahora muestra `"N boleta/s × M = X"` siendo X la suma de multiplicadores del grupo (no `length × mult[0]`, por si llegan boletas con mult distinto en el mismo grupo).
- Aplica para **todos los vendedores** porque el cambio es en el template compartido, no en datos de un vendedor puntual.
- Ejemplo: PATA 1 (17 ×1) + PATA 2 (7 ×2) + PATA 3 (1 ×3) = 17 + 14 + 3 = **34** ponderado (antes mostraba 25).

---

### Sesión 10/05/2026 (cont. 3) — Persistencia del ponderado en liquidaciones + Refresh post-entrega + Renombrar zonas

#### Fix — Refresh tras "Pasar a CAJA" en detalle de vendedor (`vendedor_detalle.html`)
- **Bug:** al apretar "Pasar a CAJA" en el modal Entregar a Caja, sólo se agregaba la fila al historial de entregas. Los contadores de arriba ("En caja — sin liquidar", "Liquidado", "En sistema"), el badge "X boleta(s)" del botón "Liquidar vendedor" y los paneles por PATA no se actualizaban — había que salir y volver.
- **Fix:** en `entregarCajaVendedor()` rama `data.ok`, si `total > 0` se programa `setTimeout(() => window.location.reload(), 900)`. El delay deja ver el alert verde con el resumen antes del reload. No recarga si `total === 0` ni en caso de error, para que el usuario pueda corregir el rango.
- Cambio mínimo, sin tocar backend ni el flujo de Liquidar (que ya hace POST normal + redirect).

#### Fix grande — Columna `cuotas_equiv` (ponderado por multiplicador) en LiquidacionVendedor
- **Bug:** el detalle de liquidación mostraba "Cuota 1 (25 boleta/s)" cuando en realidad correspondía 34 (17 PATA1 + 7×2 PATA2 + 1×3 PATA3). El monto $510.000 ya estaba bien (porque `talonera.valor_cuota` ya encodea el multiplicador), pero el conteo de boletas era el literal.
- **Decisión de modelo:** agregar columna nueva `LiquidacionVendedor.cuotas_equiv` (Integer, default 0) para guardar el ponderado al insertar. `cuotas_vendidas` se conserva como el conteo literal de boletas (para tooltip / referencia histórica).
  - `app/models.py`: `cuotas_equiv = Column(Integer, default=0)` entre `cuotas_vendidas` y `cuota_1_total`.
  - `app/main.py`: migración `ALTER TABLE liquidaciones_vendedor ADD COLUMN cuotas_equiv INTEGER DEFAULT 0` con detección de dialect (postgresql usa `IF NOT EXISTS`, SQLite chequea `inspector.get_columns`). En el mismo bloque, **backfill**: para liquidaciones con `cuotas_vendidas > 0` y `cuotas_equiv = 0`, calcula `cuotas_equiv = sum(boleta.talonera.multiplicador)` por las boletas atadas. Si `contados_vendidos > 0` (caso mixto: no se puede distinguir cuotas vs contado por boleta), cae a `cuotas_vendidas` como fallback seguro.
  - `app/routers/vendedores.py` `liquidar`: calcula `cuotas_equiv = sum((b.talonera.multiplicador or 1) for b in cuotas)` y lo pasa al constructor de `LiquidacionVendedor`.
  - `app/routers/vendedores.py` `liquidacion_detalle`: el JSON ahora incluye `cuotas_equiv`. Si la columna estaba en 0 (registro pre-migración), calcula al vuelo desde `boletas_out` (cuando `contados_vendidos == 0`) o cae al literal.
- **Frontend (`vendedor_detalle.html`):**
  - **Historial de liquidaciones**: la columna "Cuotas" muestra `{{ liq.cuotas_equiv or liq.cuotas_vendidas }}` con tooltip "Boletas reales: 25". Encabezado actualizado: "Cuotas ponderadas por multiplicador de PATA (PATA 1 ×1, PATA 2 ×2, ...)".
  - **Modal Detalle de liquidación**: línea "Cuota 1 (${d.cuotas_equiv ?? d.cuotas_vendidas} boleta/s)" usa el ponderado.
  - **Modal Liquidar — preview vivo (`recalcular()`)**: separa `nCuotasReal` (conteo literal) de `nCuotasEq` (ponderado: `nCuotasEq += mult`). El span `lbl-n-cuotas` muestra `nCuotasEq` (que coincide con el historial); `lbl-sel-count` ("X boletas seleccionadas") sigue con el literal (`nCuotasReal + nContados`).
- Ejemplo: 17 PATA1 + 7 PATA2 + 1 PATA3 = literal 25 / ponderado 34. Ambos se muestran (34 en grande, 25 en tooltip).

#### Decisión — `/compradores/` "Todas" badge SÍ pondera, "X socios sin cobrador" NO
- **Discusión iterativa:** primero se quiso ponderar el badge "Todas". Después Sergio aclaró que son socios (personas). Después rectificó: el badge "Todas" SÍ es volumen de bono (debe ponderar como las liquidaciones), pero la alerta "X socios sin cobrador" queda en literal (personas).
- **Resultado** (`app/routers/compradores.py`):
  - `tabs` ahora incluyen `multiplicador` (extraído de `Talonera.multiplicador`). El template no cambia — sólo recibe la clave extra.
  - `total_compradores = sum(t["total"] * t["multiplicador"] for t in tabs)` — antes era `db.query(Comprador).count()`.
  - `sin_cobrador` y `sin_vendedor` siguen como conteos literales (no se modifican).
- Ejemplo: 39 X1 + 9 X2 + 1 X3 + 1 X4 + 3 X8 → badge "Todas" = 39+18+3+4+24 = **88** (antes mostraba 53).

#### Feature — Renombrar zonas con propagación automática a socios
- **Caso de uso:** Sergio quería poder cambiar el nombre de una zona y que los socios ya cargados aparezcan con el nombre nuevo automáticamente. Como `Comprador.zona_id` es FK por ID, sólo falta exponer el endpoint de edición — no hay datos a migrar.
- **Backend (`app/routers/zonas.py`):**
  - Nuevo endpoint `POST /zonas/{zid}/editar` (Form: `nombre`, `descripcion`). Trimea, valida no-vacío, valida unicidad excluyendo la propia zona (porque `Zona.nombre` es UNIQUE). Si el nombre cambió, actualiza; siempre actualiza `descripcion`. Redirige a `/zonas/?ok=editada` (verde), `?err=nombre_duplicado&n=NOMBRE` (rojo) o `?err=nombre_vacio` (amarillo).
  - `listar` ahora acepta `ok`, `err`, `n` (Optional[str]) como query params para mostrar el flash en el template.
- **Frontend (`app/templates/zonas.html`):**
  - Alertas Bootstrap arriba de la tabla según `msg_ok` / `msg_err` / `msg_nombre`.
  - Botón ✏️ outline-primary al lado del 🗑️ rojo en cada fila, con `data-zona-id`, `data-zona-nombre`, `data-zona-desc`, `data-zona-compradores`.
  - Modal compartido `#modalEditar` con script IIFE que escucha `show.bs.modal`, lee los `data-*` del botón disparador, setea `form.action = '/zonas/{id}/editar'` y precarga inputs. Si la zona tiene compradores > 0, muestra hint: "Hay X socio(s) en esta zona — al guardar van a aparecer con el nombre nuevo automáticamente."
  - `setTimeout(() => input.select(), 50)` para preseleccionar el texto del nombre al abrir.

---

### Sesión 10/05/2026 (cont. 4) — Detalle de vendedor: tabs Caja/Liquidaciones + historiales en modal

**Motivación:** la página `vendedor_detalle.html` se había vuelto larga y plana (tarjetas + 2 botones + cards por PATA + tabla de entregas + tabla de liquidaciones todo seguido). Sergio pidió separar en sub-secciones para que cada acción y su historial queden agrupados.

#### Layout final del template `vendedor_detalle.html`
1. **Header** (vendedor + badge jefe, mensajes de alerta `msg=liquidado`/`sin_pendientes`)
2. **Tarjetas resumen** (En caja / Liquidado / En sistema) — comunes a ambas tabs
3. **Leyenda de colores** — común
4. **Nav-tabs Bootstrap** con dos pestañas:
   - **Tab "Caja"** (default activa, badge azul = `ns.en_caja + ns.liq_pend_comp`)
     - Botón rojo **Entregar a Caja** → abre `#modalEntregarCaja` (sin cambios)
     - Botón outline-secondary **Historial de cajas entregadas** → abre `#modalHistEntregas`
     - Cards por PATA con badges de boletas (idéntico al diseño previo)
     - Modales `modalEditarEntrega{{e.id}}` quedaron dentro de la tab Caja (loop sobre `entregas_vendedor`)
   - **Tab "Liquidaciones"** (badge verde = `liquidaciones|length`)
     - Botón verde **Liquidar vendedor** con badge "X boleta(s)" → abre `#modalLiquidar` (sin cambios)
     - Botón outline-secondary **Historial de liquidaciones** → abre `#modalHistLiquidaciones`
5. **Modales a nivel raíz** (FUERA de las tabs, antes del `<script>`):
   - `#modalDetalleLiq` — sin cambios, sigue envuelto en `{% if liquidaciones %}`
   - `#modalLiquidar` — sin cambios
   - `#modalEntregarCaja` — sin cambios
   - **`#modalHistEntregas`** (nuevo): modal-xl scrollable con la tabla de entregas a caja del vendedor. Conserva los IDs `ec-tbody-vendedor` y `ec-row-{{e.id}}` que usa `agregarFilaEntrega()`. Footer muestra "N entrega(s) registrada(s)" + botón Cerrar.
   - **`#modalHistLiquidaciones`** (nuevo): modal-xl scrollable con la tabla de historial. Conserva el `ondblclick="verDetalleLiq({{liq.id}})"` para abrir el modal de detalle existente. Si no hay liquidaciones, muestra estado vacío con ícono.

#### Iteraciones descartadas (lecciones)
- **Primer intento:** historiales como `<div class="collapse">` desplegables debajo del botón con `data-bs-toggle="collapse"`. **Falló silencioso** — el botón no hacía nada (causa probable: data-api no enganchó o caché del navegador). 
- **Segundo intento:** mismo collapse pero con `onclick="bootstrap.Collapse.getOrCreateInstance(...).toggle()"` directo. **Funcionó** pero Sergio prefirió modal.
- **Decisión final:** modal — más limpio, consistente con `modalDetalleLiq` que ya existía, y no compite con el contenido de la tab.

#### Gotcha importante: modales y tab-panes
- **Los modales NO pueden ir DENTRO de un `<div class="tab-pane">` que pueda estar inactivo.** El tab-pane inactivo tiene `display:none` y los hijos heredan eso (incluido el modal con `display:block`). Resultado: el modal "se abre" pero no se ve.
- **Regla:** todos los modales viven a nivel raíz del bloque `{% block content %}` (o se mueven al `<body>` por Bootstrap al abrirse, pero por cómo está el HTML inicial, mejor a nivel raíz desde el principio).

#### Validación de integridad
- 41 `{% if %}` / 41 `{% endif %}`, 14 `{% for %}` / 14 `{% endfor %}` (balanceados)
- `id="ec-tbody-vendedor"` único en el DOM (sigue siendo el target de `agregarFilaEntrega()`)
- Sin código muerto: `toggleColl()` (función JS de la iteración con collapse) eliminada del script

#### JS sin cambios
- Todo el script al final del archivo quedó intacto: `renderPatas()`, `recalcular()`, `entregarCajaVendedor()`, `verDetalleLiq()`, etc. Sólo cambió la estructura HTML de envoltura.

---

### Pendientes / próximos pasos
- **Talonera CONTADO Etapa 3**: sorteo cruzando Boleta.numero_especial y numero_especial_2
- Sección especial de Recaudado (separada del dashboard)
- Posible importación de datos desde el Excel original
- Mejorar scraper automático (Selenium o Playwright)
- **Validar con Sergio**: las columnas "Taloneras" y "Vendidas" en dashboard Por Zona quedaron con el mismo dato — ¿eliminar una o redefinir?
- **Posible extensión visual**: aplicar el reemplazo "PATA N" → "XN" en todas las pantallas de la app (filtro Jinja global)
- **Validar multi-up extracto**: probar 4-up y 9-up con datos reales (extracto con muchos ganadores) para ajustar font-sizes si hay overflow en las celdas. Si los ganadores no entran, bajar a ~4.5pt en 9-up o reducir cantidad de premios mostrados.
- **Posible mejora UX detalle vendedor**: si Sergio lo pide, mostrar la leyenda de colores SOLO cuando la tab Caja está activa (ya que los colores aplican únicamente a las taloneras de esa tab). Hoy quedó arriba común.

---
*Última actualización: 10 de mayo de 2026 (sesión cont. 4) — Detalle de vendedor reorganizado en tabs Caja/Liquidaciones con historiales en modal-xl + lecciones sobre modales dentro de tab-panes inactivos*
