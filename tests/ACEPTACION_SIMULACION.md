# Aceptación con 4 datasets simulados (Fase 5.2)

Pruebas: `tests/test_simulation_acceptance.py`. Fixtures: `tests/data/simulation_a` … `simulation_d`.
Todas las preguntas pasan por el código real (parser → QuerySpec → builder → ejecutor → evidencia,
catálogo, relaciones, ETL y menú). Los valores esperados se calcularon a mano (detalle en el docstring
de la prueba) o con SQL de Spark escrito en la prueba, nunca con el parser ni el builder del analizador.
Los defectos están como `xfail(strict=True)`: la prueba describe el comportamiento correcto y se pondrá
en rojo cuando se corrija, para actualizarla.

Reproducir: `.venv\Scripts\python.exe -m pytest tests/test_simulation_acceptance.py -rxX`

## Matriz

Tipos de fallo: INT = interpretación de la pregunta, ETL = limpieza, REL = relación entre tablas,
NI = no implementado.

| # | Escenario | Esperado | Real | Estado | SQL / detalle | Causa | Sev. | Corrección |
|---|---|---|---|---|---|---|---|---|
| A1 | ¿Cuántas filas tiene el dataset? | 8 | 8 | PASS | `COUNT(*)` | | | |
| A2 | Unidades en pedidos entregados | 14 | 14 | PASS | `SUM(quantity) … WHERE status = 'Entregado'` | | | |
| A3 | Producto con más unidades entregadas | Mouse 7 | Mouse 7 | PASS | `SUM` + `GROUP BY product` + `LIMIT 1` | | | |
| A4 | Pedidos entregados | 5 | 5 | PASS | `COUNT(*) … WHERE status` | | | |
| A5 | Unidades entregadas por ciudad | Pasto 7, Cali 4, Bogota 3 | igual | PASS | `GROUP BY city` con filtro | | | |
| A6 | Unidades entregadas por producto | L 3, M 7, T 4 | igual (= SQL independiente) | PASS | | | | |
| A7 | Precio unitario máximo | 100 | 100 | PASS | `MAX(unit_price)` | | | |
| A8 | Producto con el precio unitario máximo | Laptop | pregunta SUM/AVG/registro; con registro: Laptop + aviso de empate (3 filas) | PASS (aclaración) | | Ofrece SUM para un precio | baja (F8) | Ofrecer MAX por grupo en lugar de SUM cuando la medida es un precio |
| A9 | Unidades por producto, todos los estados | L 6, M 10, T 5 | igual | PASS | sin `WHERE` | | | |
| A10 | Suma de unidades por *vendedor* (no existe) | aclaración | 5.2: 21 sin agrupar. **5.3: pide la columna** (columnas reales) | PASS (corregido 5.3) | `SUM(quantity)` | INT: "por <palabra>" sin columna se ignora | **alta (F1)** | Ver F1 |
| B1 | Relación propuesta y medida | 1:N, huérfanos 2 / 1 | igual, con advertencias | PASS | | | | |
| B2 | Relación pendiente | no se usa | rechazada: "esta PENDIENTE" | PASS | | | | |
| B3 | Relación rechazada | no se usa | rechazada: "esta RECHAZADA" | PASS | | | | |
| B4 | Importe total / pedidos totales | 430 / 4 | 430 / 4 | PASS | una tabla, sin JOIN | | | |
| B5 | Importe total por nombre | Luis 200, Ana 150; P4 sin cliente | igual + aviso de P4 | PASS | `JOIN clientes … GROUP BY clientes.nombre` | | | |
| B6 | El JOIN no infla | = SQL independiente; 350 = 430 − 80 | igual | PASS | | | | |
| B7 | Pedidos por nombre, incluyendo sin pedidos | Ana 2, Luis 1, Marta 0, Sofia 0 | igual | PASS | `LEFT JOIN`, `COUNT(pedidos.cliente_id)` | | | |
| B8 | Clientes sin pedidos | Marta, Sofia | igual | PASS | `LEFT ANTI JOIN` | | | |
| B9 | Importe por nombre en pedidos entregados | Ana 150 | Ana 150 | PASS | `WHERE pedidos.estado = 'Entregado'` | | | |
| B10 | Pedidos entregados por nombre | Ana 2 | Ana 2 | PASS | | | | |
| B11 | Importe total de Ana | 150 | 150 | PASS | `WHERE clientes.nombre = 'Ana'` | | | |
| B12 | P4 (cliente C9 inexistente) | no se inventa cliente | aviso INNER; en SQL manual con LEFT, P4 con cliente NULL | PASS | | | | |
| B13 | Pedidos entregados con su cliente (listado) | listado | rechazada; con SQL manual: P1 Ana, P2 Ana, P4 NULL | BLOCKED | | NI: listar filas de un JOIN | media (F5) | Usar SQL manual (Herramientas avanzadas > 8) |
| B14 | Importe total por *cliente* | por cliente o aclaración | 5.2: 350 sin agrupar. **5.3: ofrece las columnas de `clientes`; con `nombre`: Luis 200, Ana 150** | PASS (corregido 5.3) | `SUM(pedidos.importe)` con JOIN | INT: "cliente" es la tabla, no una columna (F1) | **alta (F1)** | Ver F1 |
| B15 | ¿Cuántos pedidos hizo cada *cliente*? | por cliente o aclaración | 5.2: 3 sin agrupar. **5.3: pide la columna; con `nombre`: Ana 2, Luis 1** | PASS (corregido 5.3) | `COUNT(*)` con JOIN | INT (F1) | **alta (F1)** | Ver F1 |
| B16 | Importe total de *pedidos* entregados por nombre | Ana 150 | 5.2: rechazada. **5.4: `SUM(pedidos.importe) … WHERE estado = 'Entregado' GROUP BY nombre` → Ana 150** | PASS (corregido 5.4) | | INT: "total de pedidos" se lee como conteo | media (F4) | Ver F4. Reformular: "…por nombre en pedidos entregados" |
| B17 | Menú: registrar, rechazar, preguntar, confirmar, preguntar | rechazo y luego respuesta con JOIN | igual (Luis 200, Ana 150) | PASS | | | | |
| C1 | Carga original | 14 filas; `cantidad` texto | igual | PASS | | | | |
| C2 | Duplicado exacto | 1 detectado y eliminado | igual | PASS | | | | |
| C3 | Rechazos con motivo | filas 8, 9, 10, 11 | igual, motivo por fila | PASS | | | | |
| C4 | Válidas, total, sin imputar | 9 filas, 15 unidades | igual (14 = 9 + 4 + 1) | PASS | | | | |
| C5 | Espacios, mayúsculas y tildes | unificados | " laptop pro 14 " → "Laptop Pro 14"; "teclado mecanico" → "Teclado Mecánico" | PASS | | | | |
| C6 | Posibles equivalencias | detectadas, no aplicadas | 3 informadas (Laptop, Mouse, R-001/R001), ninguna aplicada | PASS | | | | |
| C7 | Equivalencia Laptop Pro14 confirmada | solo la fila 2 cambia | igual; total 15 | PASS | | | | |
| C8 | Equivalencia no confirmada | no aplicada | no aplicada, con aviso | PASS | | | | |
| C9 | Equivalencia MouseX → Mouse X confirmada | solo la fila 5 | igual | PASS | | | | |
| C10 | Equivalencia sobre `fila` (numérica) | rechazada | rechazada | PASS | | | | |
| C11 | Equivalencia sobre código `referencia` (R001 → R-001) | rechazada | 5.2: aplicada. **5.3: rechazada antes de transformar; ambos códigos se conservan** | PASS (corregido 5.3) | | ETL: la protección depende del nombre y de ≥ 95 % de valores únicos (aquí 13/14 = 93 %) | **alta (F2)** | Ver F2 |
| C12 | Evidencia de decisiones | registrada | decisiones, transformaciones de/a, avisos | PASS | | | | |
| C13 | Repetir el ETL | sin acumulación | idéntico | PASS | | | | |
| D1 | Precio máximo | 3000 | 3000 | PASS | `MAX(precio)` | | | |
| D2 | Producto con precio máximo (empate) | no engañar | aclaración; con registro: aviso "Empate: 2 registros" | PASS | | | | |
| D3 | Categoría con mayor stock total | Accesorios 94 | igual | PASS | | | | |
| D4 | Productos distintos | 11 (o aclaración) | 11 (columna `producto`; los ids también son 11) | PASS | | Interpretación literal de la columna | — | |
| D5 | Productos con stock mayor que 0 | 9 | 9 | PASS | `WHERE stock > 0` | | | |
| D6 | Productos con stock mayor que *cero* | 9 o aclaración | 5.2: 11. **5.3: 9, `WHERE stock > 0`** | PASS (corregido 5.3) | `COUNT(DISTINCT producto)` sin `WHERE` | INT: número escrito en palabras; además la salvaguarda no cubre `COUNT DISTINCT` | **alta (F3)** | Ver F3 |
| D7 | Stock total por categoría | …, Pantallas NULL | igual (NULL, no 0) | PASS | | | | |
| D8 | Producto con mayor stock | Mouse 50 | aclaración; con registro: Mouse 50 | PASS | | | | |
| D9 | Registros con precio nulo | 1 | rechazada (no soportada) | BLOCKED | | NI: condición IS NULL | media (F6) | SQL manual: `WHERE precio IS NULL` |
| D10 | Filtros de fecha / fecha inválida | marzo 2; después del 2025-06-01: 3 | igual; la fecha inválida queda en NULL | PASS | `try_to_date(...)` | | | |
| D11 | Identificadores no se suman | aclaración | pide la medida (stock / precio) | PASS | | | | |
| D12 | "Stock" es producto y columna | aclaración | aclaración | PASS | | No ofrece leerlo como valor | baja (F9) | |
| D13 | Stock total de los productos con precio > 100 | 23 | 5.2: detalle por producto. **5.4: `SELECT SUM(stock) … WHERE precio > 100` → 23** | PASS (corregido 5.4) | `GROUP BY producto` | INT: "los productos con …" se lee como agrupación | media (F7) | Ver F7 |
| D14 | `proveedor_id` distintos | 4 | 4 | PASS | | | | |

**Totales en la fase 5.2: 54 escenarios: 45 PASS, 7 FAIL, 2 BLOCKED.**
**Después de la fase 5.3: 50 PASS, 2 FAIL (F4 y F7, medios), 2 BLOCKED (F5 y F6, no implementados).**
**Después de la fase 5.4: 52 PASS, 0 FAIL, 2 BLOCKED (F5 y F6, no implementados).** F4 y F7 corregidos (pruebas en `tests/test_fixes_f4_f7.py`).
Fallos altos F1, F2 y F3 corregidos en la fase 5.3 (pruebas en `tests/test_fixes_f1_f3.py`). En la fase 5.4 se corrigieron F4 y F7. Siguen pendientes F5 y F6 (no implementados) y los bajos F8 y F9.

## Correcciones (F1, F2 y F3 en la fase 5.3; F4 y F7 en la fase 5.4; el resto, pendiente)

- **F1 (alta, interpretación).** En `intents._Run._shape`, después de buscar la entidad: si el texto
  contiene `por | cada | por cada | de cada …` seguido de una palabra que no es mención, ni palabra de
  pregunta, ni fecha, y no se encontró agrupación, preguntar (NeedsInput) qué columna usar. En modo
  catálogo, si la palabra es una tabla ("cliente"), ofrecer las columnas de esa tabla (`cliente_id`,
  `nombre`). Unas 15 líneas; nunca responde un total sin agrupar.
- **F2 (alta, ETL).** En `pipeline._equivalences`: considerar código a la columna cuyos valores de la
  equivalencia no tienen espacios y contienen dígitos (R001, R-001), o que es casi única sin contar las
  filas duplicadas, y rechazar la equivalencia. Unas 5 líneas.
- **F3 (alta, interpretación).** Aplicar la salvaguarda de "columna numérica no usada" también antes de
  devolver `COUNT_DISTINCT` (mover 6 líneas): rechaza en lugar de responder 11. Opcional: leer los
  números escritos con palabras (cero … diez) en `numbers.read_number`.
- **F4 (media).** `COUNT_ROWS_PHRASE` no debe reescribir "total de pedidos" como conteo cuando delante
  hay una medida ("importe total de pedidos").
- **F5 / F6 (media, no implementado).** Listar filas de un JOIN y la condición "es nulo". Mientras
  tanto, usar SQL manual.
- **F7 (media).** "X total de los productos con <condición>" debe ser un total filtrado, no una agrupación.
- **F8 / F9 (baja).** Mejoras de las aclaraciones.

## Pendiente / no automatizado

Todo lo anterior está automatizado, incluido el menú interactivo, con entradas simuladas. No se
probó a mano la consola real (`run.cmd`); su arranque lo cubre `tests/test_usb_final.py`.
