# Manual de Usuario — PySpark Lab Analyzer

> Versión del manual: entrega final. Todo lo descrito aquí corresponde al código del
> repositorio; los ejemplos numéricos provienen del dataset de prueba
> `tests/data/stocks_small.csv` (12 registros) salvo que se indique otra cosa.

## Contenido

1. [Descripción del sistema](#1-descripción-del-sistema)
2. [Requisitos](#2-requisitos)
3. [Estructura del proyecto](#3-estructura-del-proyecto)
4. [Inicio del programa](#4-inicio-del-programa)
5. [Comprobación del entorno](#5-comprobación-del-entorno)
6. [Carga de un dataset](#6-carga-de-un-dataset)
7. [Menú principal](#7-menú-principal)
8. [Modo laboratorio](#8-modo-laboratorio)
9. [Las 10 preguntas del laboratorio](#9-las-10-preguntas-del-laboratorio)
10. [Cómo interpretar la evidencia](#10-cómo-interpretar-la-evidencia)
11. [Preguntas de selección múltiple](#11-preguntas-de-selección-múltiple)
12. [Preguntas verdadero/falso](#12-preguntas-verdaderofalso)
13. [Análisis guiado](#13-análisis-guiado)
14. [Análisis completo](#14-análisis-completo)
15. [Ejecutar Spark SQL](#15-ejecutar-spark-sql)
16. [Esquema y perfil](#16-esquema-y-perfil)
17. [Historial](#17-historial)
18. [Exportar evidencia](#18-exportar-evidencia)
19. [Dataset de 500.000 registros](#19-dataset-de-500000-registros)
20. [Recomendaciones para datasets grandes](#20-recomendaciones-para-datasets-grandes)
21. [Errores frecuentes](#21-errores-frecuentes)
22. [Uso desde USB](#22-uso-desde-usb)
23. [Limitaciones conocidas](#23-limitaciones-conocidas)
24. [Flujo recomendado para el laboratorio](#24-flujo-recomendado-para-el-laboratorio)
25. [Ejemplo completo](#25-ejemplo-completo)
26. [Buenas prácticas](#26-buenas-prácticas)
27. [Conclusión](#27-conclusión)

---

## 1. Descripción del sistema

**PySpark Lab Analyzer** es una aplicación de terminal para analizar datasets con
**PySpark** y **Spark SQL**. Fue creada para resolver laboratorios académicos de ETL en los
que se carga un dataset, se consulta con Spark SQL y se responden preguntas analíticas
(conteos, promedios, máximos, agrupaciones, variaciones porcentuales, preguntas de selección
múltiple y de verdadero/falso), mostrando siempre **la consulta que justifica cada respuesta**.

Problema que resuelve:

- En un laboratorio con tiempo limitado hay que escribir muchas consultas parecidas y
  justificar cada respuesta. El sistema genera la consulta, la ejecuta en Spark y entrega
  el resultado junto con su evidencia.
- Los PC del laboratorio no siempre tienen Python, Java ni Internet. El proyecto incluye un
  arranque portátil que funciona desde una USB, sin instalar nada en el equipo.

Capacidades reales (todas implementadas en `app/`):

| Capacidad | Qué hace |
|---|---|
| Carga de datasets | CSV, JSON (líneas o arreglo) y Parquet; detecta separador, encabezado y codificación. |
| Perfilado | Tipos, nulos, cardinalidad aproximada, estadísticas, columnas categóricas y fechas. |
| Roles semánticos | Reconoce el significado probable de las columnas (fecha, apertura, cierre, volumen, empresa…) aunque tengan otros nombres. |
| Preguntas en lenguaje natural | Español (y frases básicas en inglés) mediante un **intérprete determinista basado en reglas**; no usa IA ni Internet. |
| Modo laboratorio | Bucle rápido: se pega la pregunta (con opciones A–D si las tiene) y se obtiene la respuesta con su evidencia. |
| Análisis guiado | Ocho operaciones por menús (estadísticas, agrupación, filtro, ranking, máximo/mínimo, comparación, variación porcentual, conteo condicionado). |
| Análisis completo | Un conjunto acotado (máximo 12 consultas) de análisis útiles elegidos según el dataset. |
| Ejecutar Spark SQL | Consultas manuales de solo lectura sobre la vista `dataset`. |
| Evidencia | Cada operación guarda pregunta, interpretación, SQL, resultado, respuesta, tiempo y fecha; se exporta a JSON y CSV. |

Todas las consultas se ejecutan en Spark con `spark.sql(...)` sobre una vista temporal
llamada **`dataset`**. Internamente existe un único generador de SQL: las preguntas, el
análisis guiado y el análisis completo producen una misma estructura (`QuerySpec`) que se
convierte en SQL en un solo lugar (`app/query/builder.py`).

---

## 2. Requisitos

| Requisito | Detalle |
|---|---|
| Sistema operativo | Windows 10/11 de **64 bits (x64)**. ARM no está soportado por el kit. |
| Python | Incluido en el kit: Python 3.12.10 oficial (paquete NuGet de python.org, firmado por la PSF) en `tools\python`. |
| Java | Incluido en el kit: Eclipse Temurin JRE 17 (firmado) en `tools\jre`. |
| PySpark | 4.2.0, instalado sin Internet desde `wheels\` en un entorno virtual `.venv`. |
| RAM | Spark se configura con hasta 2 GB para el driver. Las pruebas se hicieron en un equipo de 7.8 GB de RAM total con entre 0.7 y 1.9 GB libres; funcionó, pero se recomienda **8 GB de RAM total y al menos 2 GB libres**. |
| Disco | ≈ 600 MB para el kit + ≈ 600 MB para el `.venv` que se crea en el primer arranque (≈ 1.3 GB en total). |
| Permisos | No requiere administrador. |

### Preparación del kit (una sola vez, con Internet)

El repositorio **no incluye** el kit binario (`tools\` y `wheels\`, ≈ 600 MB) porque GitHub
no admite archivos de más de 100 MB (el wheel de PySpark pesa 430 MB). Se descarga con:

```powershell
.\run.cmd -PrepareKit
```

Este comando muestra cada descarga y pide confirmación. Descarga Python portable (verifica
SHA-512 y la firma digital de la PSF), el JRE 17 (verifica SHA-256) y prepara los wheels de
PySpark y py4j. Es **la única operación del proyecto que usa Internet**.

### Uso normal (sin Internet)

Una vez preparado el kit, el sistema funciona **completamente sin Internet**: el entorno
virtual se crea desde `wheels\` con `pip install --no-index`, Spark se ejecuta en modo local
(`local[*]`) y ninguna parte de la aplicación accede a la red. Esto lo verifican las pruebas
`tests/test_audit.py` (análisis del código) y `tests/test_usb_final.py` (ejecución real con
pip sin índice y proxy apuntando a un puerto cerrado).

---

## 3. Estructura del proyecto

```text
pyspark_lab_analyzer/
├── run.cmd                 Punto de entrada (doble clic o desde la terminal)
├── run.ps1                 Orquestador: comprueba el entorno, repara y lanza main.py
├── bootstrap/
│   ├── checks.ps1          Las 9 comprobaciones del entorno (no modifican el sistema)
│   ├── install.ps1         Reparaciones offline: crear .venv e instalar PySpark desde wheels\
│   └── prepare_kit.ps1     Descarga del kit (solo con -PrepareKit, en casa)
├── main.py                 Punto de entrada de Python (argumentos --lab, --debug, --check)
├── config.py               Configuración centralizada (Spark, límites, roles semánticos)
├── app/
│   ├── application.py      Flujo: pedir dataset → cargar → perfilar → menú
│   ├── session.py          Estado de la sesión e historial de evidencia
│   ├── environment.py      Diagnóstico desde Python (python main.py --check)
│   ├── export.py           Exportación de evidencia a JSON y CSV
│   ├── spark/              SparkSession y carga de datasets
│   ├── schema/             Perfilado y detección de roles semánticos
│   ├── query/              QuerySpec, generador de SQL y ejecutor
│   ├── questions/          Parser, intérprete de intenciones y resolución de respuestas
│   ├── analysis/           Análisis guiado y análisis completo
│   └── ui/                 Menús, modo laboratorio, SQL manual y presentación
├── tests/                  Pruebas automáticas (pytest) y datasets pequeños de prueba
├── requirements.txt        Dependencia de ejecución: pyspark==4.2.0
├── requirements-dev.txt    Dependencias de pruebas: pytest, pyarrow
├── pytest.ini              Configuración de pytest
├── README.md               Presentación del proyecto
├── MANUAL_USUARIO.md       Este manual
├── EMERGENCY.md            Qué hacer si algo falla durante el laboratorio
├── tools/                  (kit, no está en git) Python y JRE portables
└── wheels/                 (kit, no está en git) pyspark y py4j para instalar offline
```

Carpetas que se generan durante el uso y no forman parte del repositorio: `.venv\` (entorno
virtual, depende del PC), `exports\` (evidencia exportada) y `.pytest_cache\`. Los
archivos temporales de Spark (log de la JVM y warehouse) se guardan en
`%TEMP%\PySparkLabAnalyzer`, nunca en la carpeta del proyecto.

---

## 4. Inicio del programa

Desde la carpeta del proyecto, en PowerShell o en la terminal:

```powershell
.\run.cmd                               # comprueba el entorno y pide la ruta del dataset
.\run.cmd datos.csv --lab               # carga el dataset y entra directo al modo laboratorio
.\run.cmd --check                       # solo comprueba el entorno
.\run.cmd --check --KitOnly             # comprueba usando SOLO el Python/Java del kit
.\run.cmd --KitOnly datos.csv --lab     # uso normal forzando el kit (PC limpio)
.\run.cmd --rebuild-venv                # recrea el entorno virtual desde wheels\
.\run.cmd datos.csv --debug             # muestra detalles técnicos de los errores
```

`run.cmd` usa `-ExecutionPolicy Bypass` **solo para su propio proceso**: no modifica la
política de PowerShell del equipo. También funciona con doble clic; si algo falla, la
ventana queda abierta para leer el mensaje.

`--KitOnly` ignora cualquier Python o Java instalado en el equipo y usa solo `tools\`.
Sirve para comprobar que la USB funcionará en un PC donde no hay nada instalado.

---

## 5. Comprobación del entorno

Al iniciar, `run.cmd` ejecuta 9 comprobaciones:

```text
[1/9] Python ..................... OK      3.12.10 (kit USB)
[2/9] Java ....................... OK      Java 17.0.20.1 (kit USB)
[3/9] JAVA_HOME .................. OK      .\tools\jre (definido solo para este proceso)
[4/9] Virtual environment ........ OK      .\.venv
[5/9] PySpark .................... OK      4.2.0
[6/9] SparkSession ............... OK      Spark 4.2.0, arranque 9.4 s, ANSI=true, shuffle.partitions=8
[7/9] Spark SQL .................. OK      SELECT 1 y lectura de CSV correctas
[8/9] Resources .................. WARN    RAM libre 1.4/7.8 GB, 8 nucleos, disco libre 44.3 GB
[9/9] Offline kit ................ OK      tools\python, tools\jre y wheels presentes
```

| Comprobación | Qué verifica |
|---|---|
| Python | Un Python ≥ 3.10 que funcione. Primero el del kit; ignora el falso `python.exe` de Microsoft Store. |
| Java | Java 17, 21 o 25. Primero el JRE del kit. |
| JAVA_HOME | Lo define **solo para este proceso** con el Java encontrado (no cambia variables del sistema). |
| Virtual environment | Que `.venv` exista y funcione. Si se creó en otro PC o con otra letra de unidad, ofrece recrearlo. |
| PySpark | Que la versión instalada sea 4.2.0. Si falta, ofrece instalarla desde `wheels\`. |
| SparkSession | Arranca Spark de verdad e informa el tiempo de arranque. |
| Spark SQL | Ejecuta `SELECT 1` y lee un CSV temporal con Spark. |
| Resources | RAM libre, núcleos, disco libre y que la carpeta temporal permita escritura. |
| Offline kit | Que estén `tools\python`, `tools\jre` y el wheel de PySpark. |

Estados: `OK` (correcto), `WARN` (funciona con una advertencia), `ERROR` (impide continuar)
y `SKIP` (no se comprobó porque depende de otra comprobación que falló).

**`ENVIRONMENT READY`** solo aparece cuando no hay ningún `ERROR`; en particular, cuando
Spark arrancó y ejecutó SQL de verdad. En cualquier otro caso el resultado final es
**`ENVIRONMENT NOT READY`**, junto con qué falta, por qué hace falta y cómo solucionarlo.

Cuando algo se puede reparar con los recursos de la USB (crear el `.venv`, instalar
PySpark), el sistema muestra el comando exacto y **pide confirmación** antes de ejecutarlo;
después vuelve a ejecutar todas las comprobaciones.

**Advertencia de recursos (`WARN` en Resources):** aparece si hay menos de 2 GB de RAM libre
(o poco disco para crear el `.venv`). El programa funciona, pero puede ir más lento.
Conviene cerrar navegadores y otras aplicaciones pesadas.

---

## 6. Carga de un dataset

1. **Ruta.** Si no se indicó al iniciar, el programa pide: `Ingrese la ruta del dataset (o 'salir')`.
   Se puede escribir la ruta, pegarla o arrastrar el archivo a la terminal (las comillas se
   ignoran). Si la ruta no existe, se muestra un mensaje y se vuelve a pedir.
2. **Formato.** Se detecta por la extensión (`.csv`, `.tsv`, `.txt`, `.json`, `.jsonl`,
   `.parquet`) y, si no la hay, por el contenido. Si no se puede determinar, se pregunta.
   También se acepta una carpeta con varios archivos del mismo formato.
3. **CSV.** Se leen solo los primeros 64 KB para detectar el **separador** (`,` `;` tabulador
   `|`), el **encabezado** y la **codificación** (UTF-8 o ISO-8859-1). El programa muestra lo
   detectado y pregunta `Es correcto? [S/n]`; con `n` se puede corregir cada opción.
4. **Lectura con Spark.** Spark infiere los tipos (`inferSchema`), cachea el dataset (porque
   la sesión hará muchas consultas sobre los mismos datos) y lo cuenta. Si está vacío, se avisa.
5. **Nombres de columnas.** Los nombres válidos se conservan. Los problemáticos (espacios,
   tildes, puntos, duplicados) se renombran para poder usarlos en SQL, por ejemplo
   `'Valor Total' -> Valor_Total` o `'Año' -> Ano`; la lista se muestra en pantalla.
6. **Vista SQL.** El dataset se registra como la vista temporal **`dataset`**.
7. **Perfilado inicial** (sección 16) y **roles semánticos**.

### Roles semánticos y columnas con otros nombres

El sistema no supone que existan columnas llamadas `Close`, `Open` o `Company`. Compara los
nombres de las columnas (y su tipo) con una lista de alias configurable en `config.py`:

| Rol | Ejemplos de nombres reconocidos |
|---|---|
| DATE | `Date`, `fecha`, `dia`, `timestamp` |
| OPEN / CLOSE | `Open`, `apertura`, `precio_apertura` / `Close`, `cierre`, `precio_cierre`, `closing_price` |
| HIGH / LOW | `High`, `precio_maximo` / `Low`, `precio_minimo` |
| VOLUME | `Volume`, `volumen`, `volumen_negociado` |
| COMPANY | `Company`, `empresa`, `ticker`, `symbol` |
| PRODUCT, QUANTITY, PRICE, AMOUNT, TOTAL, NAME, ID, CATEGORY | `producto`, `cantidad`, `precio_unitario`, `importe`, `total`, `nombre`, `id`, `categoria`… |

Por ejemplo, con un dataset que tiene `empresa;volumen_negociado;fecha;precio_cierre;…`, la
pregunta *"¿Cuál empresa presentó el mayor volumen total negociado?"* genera
`SUM(volumen_negociado) … GROUP BY empresa` sin cambiar nada.

**La detección es una ayuda, no una suposición.** Si dos columnas son igual de plausibles
(por ejemplo `precio_unitario` y `precio_total` para la palabra "precio"), el sistema
muestra la lista y pide elegir; la elección se recuerda durante la sesión. Nunca inventa
una columna que no existe.

---

## 7. Menú principal

```text
   1. Analizar dataset (resumen y roles semanticos)
   2. Hacer pregunta
   3. Modo laboratorio (respuesta rapida)
   4. Ver esquema
   5. Ver perfil
   6. Ver historial
   7. Exportar evidencia (JSON y CSV)
   8. Ejecutar Spark SQL
   9. Analisis guiado
  10. Analisis completo
   0. Salir
```

Una opción no válida muestra `Opcion no valida.` y el menú vuelve a aparecer; nunca cierra
el programa.

| Opción | Qué hace | Cuándo usarla | Resultado |
|---|---|---|---|
| 1. Analizar dataset | Muestra el resumen de carga (archivo, formato, registros, columnas, tiempos, columnas renombradas) y los roles semánticos detectados, indicando los ambiguos. | Al empezar, para confirmar que el dataset se cargó bien. | Texto en pantalla. |
| 2. Hacer pregunta | Pide una pregunta (con opciones si las tiene) y la resuelve mostrando la evidencia completa. | Para una pregunta aislada con todo el detalle. | Evidencia completa (sección 10). |
| 3. Modo laboratorio | Bucle de preguntas con salida compacta (sección 8). | Durante el examen. | Una evidencia por pregunta. |
| 4. Ver esquema | Lista las columnas con su tipo Spark y su clase (numérica, texto, fecha, categórica, identificador). | Para conocer los nombres exactos antes de escribir SQL. | Tabla. |
| 5. Ver perfil | Nulos, % de nulos, distintos aproximados, mínimo, máximo, promedio por columna, columnas por clase, valores de las categóricas y 5 filas de ejemplo. | Para entender el dataset. | Tablas. |
| 6. Ver historial | Lista numerada de todas las operaciones de la sesión. | Para repasar respuestas. | Tabla #, pregunta, respuesta, intención. |
| 7. Exportar evidencia | Guarda el historial en JSON y CSV (sección 18). | Al terminar, o periódicamente. | Dos archivos. |
| 8. Ejecutar Spark SQL | Consultas SQL manuales de solo lectura (sección 15). | Cuando una pregunta no se interpreta o se quiere verificar algo. | Evidencia con tipos y resultado. |
| 9. Análisis guiado | Ocho operaciones por menús (sección 13). | Cuando se sabe qué calcular pero la pregunta no se reconoce. | Evidencia. |
| 10. Análisis completo | Hasta 12 análisis automáticos (sección 14). | Para una visión general rápida del dataset. | Una evidencia por paso. |
| 0. Salir | Cierra Spark y termina. | — | — |

Ejemplo (opción 2):

```text
Pregunta (Enter en linea vacia para terminar):
> ¿Cuál es el volumen total?
  (Enter)
RESPUESTA      : 22200
```

---

## 8. Modo laboratorio

Se entra con la opción 3 del menú o directamente con `.\run.cmd datos.csv --lab`.

```text
Escriba la pregunta (puede pegar tambien las opciones A-D) y termine con una linea vacia.
Comandos: :h historial  :x exportar evidencia  :q volver al menu
>
```

Uso:

1. Escribir o pegar la pregunta. Si tiene opciones A–D o marcas V/F, se pegan también
   (en líneas siguientes).
2. Pulsar Enter en una línea vacía.
3. Si hay una ambigüedad (por ejemplo, varias columnas posibles para "precio"), el sistema
   muestra las opciones numeradas y pide elegir; Enter sin número cancela.
4. Se muestra la evidencia compacta: interpretación, SQL, resultado y respuesta.
5. Los comandos `:h`, `:x` y `:q` actúan de inmediato (no necesitan línea vacía).

Se puede numerar la pregunta como en el enunciado (`7. ¿Cuál…?`): el número se conserva en
el historial y en la exportación.

### Cómo interpreta las preguntas

El intérprete es **determinista y basado en reglas** (`app/questions/`): no usa inteligencia
artificial ni Internet. Combina un vocabulario en español e inglés (`lexicon.py`) con el
esquema y los roles semánticos del dataset para construir un `QuerySpec`. Reconoce:

| Tipo de pregunta | Ejemplo | Intención |
|---|---|---|
| Conteo | ¿Cuántos registros contiene el dataset? | `COUNT_ROWS` |
| Conteo de distintos | ¿Cuántas empresas distintas hay? | `COUNT_DISTINCT` |
| Promedio, suma, máximo, mínimo | ¿Cuál es el precio promedio de cierre? | `AGG_SCALAR` |
| Con filtro | ¿Cuál es el volumen total de AAPL en 2024? | `AGG_WHERE` |
| Conteo condicionado | ¿Cuántos registros tienen cierre mayor que apertura? | `COUNT_WHERE` |
| Agrupación | Promedio de cierre por empresa | `GROUP_AGG` |
| Ranking / el mayor de un grupo | ¿Cuál empresa presentó el mayor volumen total negociado? | `GROUP_TOP` |
| Registro extremo | ¿Cuál fue el registro con mayor volumen individual? | `RECORD_EXTREME` |
| Diferencia entre columnas | El mayor rango diario (High - Low) | `DIFFERENCE` |
| Variación porcentual por registro | Promedio de variación porcentual entre apertura y cierre | `PCT_CHANGE` |
| Variación entre primera y última fecha | Variación porcentual del cierre en el periodo | `PCT_CHANGE_PERIOD` |
| Porcentaje de registros o de un total | ¿Qué porcentaje de registros tiene cierre > apertura? | `PERCENTAGE_OF` |
| Comparación de columnas | ¿Cuál es mayor en promedio, Open o Close? | `COLUMN_COMPARISON` |

Además reconoce filtros por valores de categorías (`de AAPL`), por fechas (`en 2024`,
`en febrero de 2024`, `entre 2024-01-03 y 2024-01-04`), comparaciones (`mayor que`,
`superior al`, `>=`…), preguntas de **selección múltiple** y de **verdadero/falso**.

**No reconoce cualquier pregunta posible.** Cuando no puede decidir con seguridad:

- si hay varias columnas o cálculos posibles, **pregunta** cuál usar (nunca adivina);
- si no reconoce la pregunta, responde `Pregunta no reconocida automaticamente.` y explica
  qué necesita (la operación y la columna). En ese caso se recomienda usar el **Análisis
  guiado** (opción 9) o **Ejecutar Spark SQL** (opción 8).

---

## 9. Las 10 preguntas del laboratorio

Las preguntas siguientes corresponden a la guía de referencia **"ETL – CON PYSPARK"** del
laboratorio, transcritas tal como se entregaron para este proyecto. El sistema **no tiene
estas preguntas programadas**: las resuelve con sus intenciones generales, igual que
resolvería una pregunta equivalente sobre otro dataset. Las pruebas `tests/test_golden.py`
las ejecutan palabra por palabra sobre dos datasets con nombres de columnas distintos.

Los resultados mostrados son del **dataset de prueba `stocks_small.csv`** (12 registros, 3
empresas). **No son los resultados del dataset real del laboratorio**, que no estaba
disponible al preparar esta entrega. Los nombres de columna (`Close`, `Open`, `Company`…) se
sustituyen automáticamente por los del dataset cargado.

### Pregunta 1 — ¿Cuántos registros contiene el dataset?

| Aspecto | Detalle |
|---|---|
| Intención | `COUNT_ROWS` |
| Columnas | Ninguna |
| Operación | Contar todas las filas |
| SQL | `SELECT COUNT(*) AS total_registros FROM dataset` |
| Interpretación del resultado | Un número entero. En el dataset de prueba: **12**. |
| Opciones | El número se compara con cada opción (sección 11). |
| Evidencia | Pregunta, intención, SQL, valor, tiempo y fecha. |

### Pregunta 2 — ¿Cuál es el precio promedio de cierre (Close) considerando todas las acciones?

| Aspecto | Detalle |
|---|---|
| Intención | `AGG_SCALAR` con agregación `AVG` |
| Columnas | El rol CLOSE (`Close`) |
| Operación | Promedio de todos los cierres |
| SQL | `SELECT AVG(Close) AS avg_close FROM dataset` |
| Interpretación | En el dataset de prueba: **154.0417** (valor exacto 154.041666…). |
| Opciones | Se acepta la opción que coincide al redondear a sus decimales: `154.04` coincide. |

### Pregunta 3 — ¿Cuál fue el precio de cierre máximo registrado?

| Aspecto | Detalle |
|---|---|
| Intención | `AGG_SCALAR` con agregación `MAX` |
| Columnas | CLOSE |
| SQL | `SELECT MAX(Close) AS max_close FROM dataset` |
| Interpretación | En el dataset de prueba: **318**. |

### Pregunta 4 — ¿Cuál empresa presentó el mayor volumen total negociado?

| Aspecto | Detalle |
|---|---|
| Intención | `GROUP_TOP` con agregación `SUM` |
| Columnas | COMPANY (`Company`) y VOLUME (`Volume`) |
| Operación | Sumar el volumen por empresa y tomar la mayor |
| SQL | `SELECT Company, SUM(Volume) AS sum_volume FROM dataset GROUP BY Company ORDER BY sum_volume DESC NULLS LAST, Company ASC LIMIT 1` |
| Interpretación | La respuesta es la empresa; también se muestra el total. Dataset de prueba: **NVDA (suma = 13000)**. |
| Empates | El sistema ejecuta una consulta adicional que cuenta cuántas empresas comparten el valor máximo y, si hay empate, lo advierte. |

### Pregunta 5 — ¿Cuál fue el volumen total negociado considerando todas las empresas?

| Aspecto | Detalle |
|---|---|
| Intención | `AGG_SCALAR` con agregación `SUM` |
| Columnas | VOLUME |
| SQL | `SELECT SUM(Volume) AS sum_volume FROM dataset` |
| Interpretación | Dataset de prueba: **22200**. "Considerando todas las empresas" no agrupa: es el total general. |

### Pregunta 6 — ¿Los registros corresponden a días en los que el precio de cierre fue superior al precio de apertura son 15? (V/F, si es falso escribir la respuesta)

| Aspecto | Detalle |
|---|---|
| Intención | `COUNT_WHERE`, tipo de pregunta **verdadero/falso** |
| Columnas | CLOSE y OPEN |
| Operación | Contar los registros con `Close > Open` y comparar con el valor afirmado (15) |
| SQL | `SELECT COUNT(*) AS total_registros FROM dataset WHERE Close > Open` |
| Interpretación | Dataset de prueba: 8 registros → **FALSO (valor correcto: 8)**. |
| V/F | Las líneas `V/F` y `Si es falso escribir la respuesta.` se reconocen como marcas del tipo de pregunta y no se mezclan con la consulta. |

### Pregunta 7 — ¿Cuál empresa presentó el mayor precio promedio de cierre?

| Aspecto | Detalle |
|---|---|
| Intención | `GROUP_TOP` con agregación `AVG` |
| Columnas | COMPANY y CLOSE |
| SQL | `SELECT Company, AVG(Close) AS avg_close FROM dataset GROUP BY Company ORDER BY avg_close DESC NULLS LAST, Company ASC LIMIT 1` |
| Interpretación | Dataset de prueba: **MSFT (promedio = 303.5)**. |

### Pregunta 8 — ¿El mayor rango diario registrado? Recuerde: Rango = High - Low es 9. (V/F)

| Aspecto | Detalle |
|---|---|
| Intención | `DIFFERENCE`, tipo **verdadero/falso** |
| Columnas | HIGH y LOW |
| Operación | `MAX(High - Low)` y comparación con 9 |
| SQL | `SELECT MAX((High - Low)) AS max_high_menos_low FROM dataset` |
| Interpretación | Dataset de prueba: 22 → **FALSO (valor correcto: 22)**. |

### Pregunta 9 — ¿Cuál empresa presentó el mayor promedio de variación porcentual entre apertura y cierre?

| Aspecto | Detalle |
|---|---|
| Intención | `PCT_CHANGE` con agregación `AVG`, agrupado por empresa (forma TOP) |
| Columnas | COMPANY, OPEN y CLOSE |
| Operación | Para **cada registro**: `((Close - Open) / Open) * 100`; después, el **promedio por empresa**; se elige la mayor. |
| SQL | `SELECT Company, AVG(((Close - Open) / NULLIF(Open, 0) * 100)) AS avg_variacion_pct FROM dataset GROUP BY Company ORDER BY avg_variacion_pct DESC NULLS LAST, Company ASC LIMIT 1` |
| Interpretación | Dataset de prueba: **NVDA (promedio = 3.9929 %)**. |
| Nota | `NULLIF(Open, 0)` evita la división por cero (un registro con `Open = 0` queda como nulo y no afecta el promedio). **No** es la variación entre la primera y la última fecha; esa es otra intención (`PCT_CHANGE_PERIOD`), que solo se usa cuando la pregunta habla del "periodo". |

### Pregunta 10 — ¿Cuál fue el registro con mayor volumen individual?

| Aspecto | Detalle |
|---|---|
| Intención | `RECORD_EXTREME` |
| Columnas | VOLUME (todas las columnas se devuelven) |
| Operación | Ordenar por volumen de mayor a menor y tomar **la fila completa** |
| SQL | `SELECT * FROM dataset WHERE Volume IS NOT NULL ORDER BY Volume DESC LIMIT 1` |
| Interpretación | Devuelve el **registro completo**, no solo el valor máximo. Dataset de prueba: `Date=2024-01-04, Company=NVDA, Open=59, High=59.5, Low=51, Close=52, Volume=4000`. |
| Opciones | Una opción como `NVDA - 2024-01-04` se compara campo por campo con el registro (empresa y fecha); una opción `NVDA - 2024-01-03` no coincide porque la fecha lo contradice. |

---

## 10. Cómo interpretar la evidencia

Cada respuesta muestra un bloque como este (dataset de prueba, pregunta 9):

```text
--- PREGUNTA 9 ---------------------------------
9. ¿Cuál empresa presentó el mayor promedio de variación porcentual
entre apertura y cierre?

INTERPRETACION : PCT_CHANGE: se calcula el promedio de variacion % de Open a Close para cada Company y se toma el mayor.

CONSULTA SPARK SQL:
  SELECT Company,
         AVG(((Close - Open) / NULLIF(Open, 0) * 100)) AS avg_variacion_pct
  FROM dataset
  GROUP BY Company
  ORDER BY avg_variacion_pct DESC NULLS LAST, Company ASC
  LIMIT 1

RESULTADO      : NVDA (promedio = 3.9929%)

RESPUESTA      : NVDA (promedio = 3.9929%)
(0.33 s, 2026-10-01T16:55:15)
```

| Elemento | Significado |
|---|---|
| PREGUNTA | El texto exacto que se introdujo (con su número si lo tenía). |
| INTERPRETACION | La intención detectada y, en palabras, qué se calcula, sobre qué columnas y con qué filtros. |
| CONSULTA SPARK SQL | La consulta exacta que se ejecutó con `spark.sql(...)`. Se puede copiar al informe o ejecutar en la opción 8. |
| TIPOS | (cuando aplica) Tipo Spark de cada columna del resultado. |
| RESULTADO | El resultado legible: valor, empresa con su valor, o el registro completo. |
| AFIRMACION / VALOR CALCULADO | En preguntas V/F: lo afirmado y lo calculado. |
| AVISO | Advertencias: empates, resultados nulos, filas truncadas, ambigüedad de opciones. |
| RESPUESTA | La respuesta final: valor, opción elegida (`C) NVDA`) o `VERDADERO` / `FALSO`. |
| Tiempo y fecha | Duración de la consulta y momento exacto (ISO 8601). |

Esta evidencia sirve para justificar la respuesta del laboratorio porque muestra **cómo** se
obtuvo: la consulta Spark SQL se puede reproducir y verificar de forma independiente, y la
interpretación deja explícito qué columnas y qué operación se usaron.

---

## 11. Preguntas de selección múltiple

Las opciones se pegan después de la pregunta, una por línea (también se aceptan en la misma
línea: `A) 10 B) 12 C) 14`):

```text
¿Cuántos registros contiene el dataset?
A) 10
B) 12
C) 14
D) 16
```

Funcionamiento:

1. **Primero se calcula el resultado real** con Spark, sin mirar las opciones.
2. Después se compara con cada opción:
   - **Números:** coincide si el resultado, redondeado a los decimales escritos en la opción,
     es igual a ella. `273.40` acepta 273.396…273.404. Se entienden formatos como `1.234,56`,
     `1,234.56`, `12,5%` o `$1.000`; si un número es ambiguo (`1.234`), se prueban ambas lecturas.
   - **Texto** (empresas, categorías): comparación sin mayúsculas ni tildes.
   - **Registros completos** (pregunta 10): cada dato del registro que aparece en la opción
     suma; una fecha o un número que lo contradice resta.
3. Resultado:
   - **Una opción coincide:** `RESPUESTA : B) 12`.
   - **Ninguna coincide:** `Ninguna opcion coincide con el resultado calculado.` y se
     muestra el valor calculado. **El sistema no inventa ni fuerza una opción.**
   - **Varias coinciden por igual:** no elige ninguna y lo advierte (`Varias opciones
     coinciden con el resultado (…); no se elige ninguna.`).

---

## 12. Preguntas verdadero/falso

Se reconocen como V/F:

- las que empiezan con `Verdadero o falso:`, `V/F`, `¿Es verdadero que…`;
- las que llevan una línea `V/F` o `(V/F)`;
- las afirmaciones del tipo `… son 15.` o `NVDA es la empresa con mayor volumen total`;
- una pregunta que no empieza con un interrogativo (cuál, cuántos, qué…) y termina en un
  número: `¿Los registros … son 15?`.

Ejemplo (dataset de prueba):

```text
¿El mayor rango diario registrado? Recuerde:
Rango = High - Low es 9.
V/F
Si es falso escribir la respuesta.
```

1. **Cálculo:** `SELECT MAX((High - Low)) AS max_high_menos_low FROM dataset`.
2. **Valor obtenido:** 22.
3. **Comparación** con el valor afirmado (9), con la misma regla de redondeo de la sección 11.
4. **Respuesta:** `FALSO`.
5. **Valor correcto:** se muestra junto a la respuesta: `RESPUESTA : FALSO (valor correcto: 22)`.

---

## 13. Análisis guiado

Opción 9 del menú. Cada operación pide los datos con menús numerados (Enter cancela) y
genera la consulta con el mismo generador de SQL que las preguntas:

| Operación | Qué pide | Qué calcula |
|---|---|---|
| 1. Estadísticas de columna | Una columna | Numérica: no nulos, nulos, distintos, mínimo, máximo, promedio, desviación, suma. Texto/fecha: no nulos, nulos, distintos, mínimo, máximo. |
| 2. Agrupación | Columna de agrupación, cálculo (COUNT/SUM/AVG/MAX/MIN) y columna numérica | Un valor por grupo, ordenado de mayor a menor. |
| 3. Filtro (ver registros) | Una o más condiciones (AND) y cuántos registros mostrar | Las filas que cumplen las condiciones (máximo 100). |
| 4. Ranking | Columna de agrupación, cálculo, columna, orden y número de puestos | Top N de grupos. |
| 5. Valor máximo/mínimo | Columna, máximo o mínimo, solo el valor o el registro completo | Un valor o la fila completa. |
| 6. Comparación entre columnas | Dos columnas numéricas y un cálculo | Cuál de las dos tiene el mayor valor del cálculo. |
| 7. Variación porcentual | Columna inicial, final, cálculo (AVG/MAX/MIN) y opcionalmente un grupo | `((final - inicial) / inicial) * 100` por registro, agregado. |
| 8. Conteo condicionado | Una o más condiciones | Cuántos registros las cumplen. |

Las condiciones admiten los operadores `>`, `>=`, `<`, `<=`, `=`, `!=` contra un valor o
contra otra columna (escribiendo su nombre). Los números se escriben con punto decimal y sin
separador de miles; las fechas como `aaaa-mm-dd`. Si un valor no es válido se muestra el
error y se vuelve a pedir.

---

## 14. Análisis completo

Opción 10. Ejecuta un **plan acotado** de análisis, elegido a partir del perfil y de los
roles semánticos, y muestra el progreso:

```text
[1/12] Conteo de registros...
[2/12] Columnas y tipos...
...
```

Pasos posibles, en orden de prioridad:

1. Conteo de registros.
2. Columnas y tipos (`DESCRIBE dataset`).
3. Nulos por columna.
4. Perfil numérico (mínimo, máximo, promedio, desviación).
5. Cardinalidad de las columnas no numéricas.
6. Principales valores de hasta 2 columnas categóricas.
7. Ranking: entidad con mayor suma de la métrica principal.
8. Registro con el mayor valor de la métrica principal.
9. Rango de fechas (si hay una columna de fecha).
10. Registros con cierre > apertura y variación % promedio (solo si existen los roles OPEN y CLOSE).
11. Mayor rango máximo − mínimo (solo si existen HIGH y LOW).
12. Ranking por promedio (el primero que se descarta si se supera el límite).

Límites (en `config.py`): como máximo **12 consultas**, 12 columnas por resumen y 5 filas por
ranking. Si un paso falla, se informa y el análisis continúa con el siguiente. El análisis se
adapta al dataset: con un dataset sin categorías ni fechas tendrá menos pasos. **No es un
análisis universal**; es una vista general rápida. El detalle completo (SQL y evidencia de
cada paso) queda en el historial y en la exportación.

---

## 15. Ejecutar Spark SQL

Opción 8. Se escribe una consulta sobre la vista `dataset`, terminada en `;` o con una línea
vacía. `:q` vuelve al menú.

```sql
SELECT COUNT(*) FROM dataset;

SELECT * FROM dataset LIMIT 10;

SELECT Company, AVG(Close) AS promedio
FROM dataset
GROUP BY Company
ORDER BY promedio DESC;

SELECT Company, MAX(High - Low) AS rango
FROM dataset
GROUP BY Company;
```

Se muestran los **tipos** de las columnas del resultado y como máximo **20 filas**; si hay
más, se avisa (`hay mas filas`) para que se agregue `LIMIT` o un filtro.

Restricciones de seguridad (la vista `dataset` y la sesión no se pueden modificar):

| Permitido | Rechazado |
|---|---|
| `SELECT`, `WITH`, `SHOW`, `DESCRIBE`, `EXPLAIN`, `VALUES`, `TABLE` | `INSERT`, `CREATE`, `DROP`, `ALTER`, `DELETE`, `UPDATE`, `MERGE`, `TRUNCATE`, `CACHE`, `REFRESH`… y comandos que cambian la sesión (`SET`, `USE`, `ADD`…) |
| Una consulta por vez | Varias consultas separadas por `;` |

Los errores se explican sin cerrar el programa, por ejemplo:

```text
ERROR: La columna 'Clos' no existe en el dataset.
  Sugerencia: Quiso decir: Close
```

Spark se ejecuta con el modo **ANSI** activado: una operación inválida (por ejemplo,
promediar una columna de texto) produce un error claro en lugar de un resultado engañoso.

---

## 16. Esquema y perfil

**Esquema** (opción 4): número, nombre, tipo Spark (`int`, `double`, `string`, `date`…) y
clase de cada columna:

| Clase | Significado |
|---|---|
| numérica | Se puede promediar, sumar, etc. |
| texto | Texto libre. |
| fecha | Tipo fecha/hora, o texto con formato de fecha (`aaaa-mm-dd`, `dd/mm/aaaa`). |
| categórica | Texto con pocos valores distintos (hasta 50); sus valores se reconocen en las preguntas (`de AAPL`). |
| identificador | Nombre tipo `id`/`codigo`, o texto con un valor distinto por fila. |

**Perfil** (opción 5): nulos y porcentaje de nulos, valores distintos (**aproximados**, con
el algoritmo HyperLogLog de Spark), mínimo, máximo y promedio de cada columna, las columnas
agrupadas por clase, los valores de las columnas categóricas y 5 filas de ejemplo.

Todo el perfil se calcula con **una sola pasada de agregación de Spark** (más una pequeña
para los valores de las categóricas): no recorre las filas en Python.

**Roles semánticos** (opción 1): el rol y la columna asignada, o `AMBIGUO` con las columnas
candidatas.

---

## 17. Historial

La opción 6 (o `:h` en el modo laboratorio) muestra todas las operaciones de la sesión:
preguntas, análisis guiado, análisis completo y SQL manual, numeradas.

Para cada operación se guarda: pregunta, tipo (abierta, selección múltiple, V/F), intención,
columnas, filtros, SQL (y las consultas auxiliares, como la de empates), filas del resultado
(**ya acotadas**: como máximo 100 en agrupaciones y 20 en SQL manual), respuesta, advertencias,
tiempo y fecha. **No se guarda el dataset**: solo resultados pequeños.

El historial vive en memoria durante la sesión; para conservarlo hay que exportarlo.

---

## 18. Exportar evidencia

Opción 7 del menú o `:x` en el modo laboratorio. Genera dos archivos con el mismo nombre:

| Archivo | Contenido |
|---|---|
| `evidencia_AAAAMMDD_HHMMSS.json` | Todo el historial con todos los campos de cada evidencia (incluida la especificación de la consulta). |
| `evidencia_AAAAMMDD_HHMMSS.csv` | Una fila por operación: número, pregunta, tipo, intención, respuesta, resultado, valor, SQL, explicación, advertencias y fecha. Se abre directamente en Excel. |

Ubicación: carpeta `exports\` dentro del proyecto. Si no se puede escribir allí (por ejemplo,
una USB protegida), se usa `%TEMP%\PySparkLabAnalyzer\exports`. La ruta exacta se muestra al
exportar. Solo existen estos dos formatos (JSON y CSV).

---

## 19. Dataset de 500.000 registros

### ¿Soporta 500.000 registros?

**Sí, en el entorno de prueba utilizado.** La prueba automática `tests/test_smoke_500k.py`
genera un CSV determinista de **500.000 registros y 7 columnas** (fecha, ticker de 50 empresas,
apertura, máximo, mínimo, cierre y volumen) con nombres distintos a los de los otros datasets
(`trade_date`, `ticker`, `open_price`…), y lo procesa con el mismo código que usa la
aplicación. Cada resultado se compara con un cálculo independiente hecho con la API de
DataFrames de Spark.

Resultado de la ejecución de la entrega final (todas las operaciones terminaron correctamente):

| Operación | Tiempo |
|---|---|
| Carga del CSV, inferencia de tipos, creación del DataFrame, caché, conteo y vista `dataset` | 3.4 s |
| Perfilado (una pasada de agregación + valores categóricos) | 1.5 s |
| `COUNT` | 0.05 s |
| `AVG` (precio promedio de cierre) | 0.08 s |
| `GROUP BY` (promedio por ticker, 50 grupos) | 0.20 s |
| TOP (ticker con mayor volumen total, con detección de empates) | 0.29 s |
| Registro extremo (fecha del mayor volumen) | 0.26 s |
| Conteo condicionado (cierre > apertura) | 0.09 s |
| Variación porcentual promedio `((Close-Open)/Open)*100` | 0.10 s |
| Variación % del periodo por ticker | 0.48 s |
| SQL manual `SELECT *` sin `LIMIT` (trae solo 20 filas) | 0.02 s |
| SQL manual con `GROUP BY` | 0.16 s |
| Análisis guiado: estadísticas de columna (mínimo, máximo, etc.) | 0.40 s |
| Análisis guiado: ranking top 5 | 0.17 s |
| Análisis completo (12 consultas) | 1.6 s |

Memoria medida:

- **Proceso Python: 66 MB antes y 66 MB después** de todas las consultas. La prueba falla si
  crece más de 50 MB, lo que comprueba que los registros no se traen a Python.
- JVM de Spark: 339 MB usados (límite configurado del driver: 2 GB).

Entorno de la prueba: Windows 11 x64, 8 núcleos, 7.8 GB de RAM total (entre 0.7 y 1.9 GB
libres durante las pruebas), Spark 4.2.0 en modo local con **un solo núcleo** (`local[1]`,
configuración de pruebas; la aplicación usa todos los núcleos, `local[*]`). En ejecuciones
anteriores, con el disco "frío", la carga tardó entre 6 y 9 s y el perfilado entre 2.8 y 4.2 s.

### Conclusión

El sistema fue probado con un dataset de 500.000 registros y las operaciones evaluadas
finalizaron correctamente bajo el entorno de prueba utilizado, sin traer los registros a
Python. **No se afirma que soporte cualquier tamaño de dataset**: el rendimiento depende de la
RAM disponible, la CPU, el número de columnas, los tipos de datos, el formato del archivo y la
complejidad de las consultas.

Limitaciones observadas:

- La inferencia de tipos del CSV lee el archivo completo dos veces; es la operación más lenta.
- La caché del dataset ocupa memoria de la JVM; con datasets mucho mayores o con muy poca RAM
  libre, Spark puede volverse lento.
- La cardinalidad exacta del análisis guiado (`COUNT(DISTINCT)`) es más costosa que la
  aproximada del perfil.

---

## 20. Recomendaciones para datasets grandes

- **Preferir CSV bien estructurado** (un separador consistente, encabezado, sin filas
  partidas) o, mejor aún, **Parquet**, que ya trae los tipos y es más rápido de leer.
- **Eliminar columnas innecesarias** antes de cargar: cada columna aumenta el tiempo de
  inferencia de tipos, del perfil y la memoria de la caché.
- **Tipos correctos:** números sin símbolos de moneda ni separadores de miles, y fechas en
  formato `aaaa-mm-dd`, para que Spark los reconozca como números y fechas.
- **RAM disponible:** cerrar navegadores e IDE antes de cargar; vigilar el `WARN` de recursos.
- **Una operación a la vez:** no abrir varias instancias del programa sobre el mismo PC.
- **Aprovechar Spark:** filtrar y agregar en SQL (`WHERE`, `GROUP BY`, `LIMIT`) en lugar de
  pedir todas las filas.
- **No convertir el dataset a Python:** el programa nunca lo hace (no usa `toPandas`,
  `collect` sin límite ni UDF de Python); en SQL manual, usar `LIMIT`.

---

## 21. Errores frecuentes

| Síntoma | Causa probable | Solución |
|---|---|---|
| `[1/9] Python ... ERROR` | Falta `tools\python` (kit incompleto) y el PC no tiene Python 3.10+. | Copiar el kit completo; o en casa `run.cmd -PrepareKit`. |
| `bloqueado por Smart App Control` | Windows 11 bloquea ejecutables sin firma. | Usar el kit de `-PrepareKit` (binarios firmados). No desactivar Smart App Control. |
| `[2/9] Java ... ERROR` | Falta `tools\jre` y el PC no tiene Java 17/21/25. | Copiar el kit completo. |
| `Virtual environment ... creado en otro equipo` | Se cambió de PC o de letra de la USB. | Aceptar la recreación (sin Internet, 1-3 minutos). |
| `SparkSession ... ERROR` | Entorno virtual dañado, poca RAM o Java incompatible. | `run.cmd --rebuild-venv`; cerrar aplicaciones; detalle con `.venv\Scripts\python.exe main.py --check --debug`. |
| `Resources ... WARN` (poca RAM) | Otras aplicaciones usando memoria. | Cerrar aplicaciones; Spark usa hasta 2 GB. |
| `No existe: …` al cargar | Ruta incorrecta o archivo movido. | Revisar la ruta o arrastrar el archivo a la terminal. |
| `El dataset esta vacio` / `no contiene columnas legibles` | Archivo vacío, solo encabezado o formato distinto al elegido. | Revisar el archivo y el formato. |
| `Solo se detecto 1 columna … separador … incorrecto` | El separador detectado no es el real. | Responder `n` a `Es correcto?` y escribir el separador. |
| `Se encontraron varias columnas posibles para …` | Ambigüedad de columnas. | Elegir la columna correcta (se recuerda en la sesión). |
| `Pregunta no reconocida automaticamente.` | La redacción no coincide con las reglas del intérprete. | Reformular con la operación y la columna, o usar Análisis guiado / Spark SQL. |
| `La columna 'X' no existe en el dataset.` | Nombre mal escrito en SQL. | Usar la sugerencia o ver el esquema (opción 4). |
| `Solo se permiten consultas de lectura` | Se intentó modificar la vista o la sesión. | Usar solo `SELECT`/`WITH`/`SHOW`/`DESCRIBE`/`EXPLAIN`. |
| `Hay valores que no se pueden convertir al tipo pedido.` | Operación numérica sobre texto (modo ANSI). | Revisar las columnas o usar `try_cast(...)`. |
| `run.ps1 no puede cargarse` | Se ejecutó `run.ps1` directamente con la política `Restricted`. | Usar siempre `run.cmd`. |
| `run.cmd` falla aun con Bypass | Una política de dominio exige scripts firmados. | Usar otro PC. |
| No encuentra `main.py` o el kit | Se ejecutó una copia incompleta del proyecto. | Ejecutar `run.cmd` de la carpeta del proyecto completa; `run.cmd` funciona desde cualquier directorio actual. |
| La USB no permite escritura | Unidad protegida. | El `.venv` se crea en `%LOCALAPPDATA%\PySparkLabAnalyzer` y la exportación en `%TEMP%`. |

Guía corta para el momento del examen: [EMERGENCY.md](EMERGENCY.md).

---

## 22. Uso desde USB

1. **Preparar el kit** (en casa, con Internet): `.\run.cmd -PrepareKit` y luego
   `.\run.cmd --check --KitOnly` hasta ver `ENVIRONMENT READY`.
2. **Copiar a la USB sin `.venv`** (E: es la letra de la USB):
   ```powershell
   robocopy . E:\pyspark_lab_analyzer /E /XD .venv exports .pytest_cache
   ```
3. **Verificar desde la USB:** `E:\pyspark_lab_analyzer\run.cmd --check --KitOnly`
   (la primera vez crea el `.venv` en la USB, sin Internet).
4. **En el laboratorio:** abrir la carpeta de la USB y ejecutar `run.cmd`
   (o `run.cmd datos.csv --lab`).
5. **Comprobación:** esperar `ENVIRONMENT READY`. Si la USB tiene otra letra o es otro PC, el
   sistema ofrece recrear el `.venv` desde la USB (aceptar).
6. **Cargar el dataset y trabajar** sin Internet.

Todas las rutas se resuelven desde la carpeta del proyecto (`$PSScriptRoot` en PowerShell,
`Path(__file__)` en Python), por eso el proyecto funciona aunque la USB cambie de letra. El
sistema no modifica variables de entorno del equipo, el PATH ni las políticas de PowerShell.

---

## 23. Limitaciones conocidas

- **Solo Windows x64.** El kit no incluye binarios para ARM.
- **RAM.** Spark reserva hasta 2 GB; con poca RAM libre funciona más lento.
- **Intérprete basado en reglas.** Reconoce las formas de pregunta descritas en la sección 8.
  Redacciones muy inusuales, agregaciones anidadas ("promedio de los máximos por empresa"),
  condiciones con OR, fechas relativas ("el último mes") o números escritos con palabras
  ("dos millones") no se interpretan automáticamente.
- **Depende de los nombres de las columnas.** Si una columna tiene un nombre que no se parece
  a ningún alias (por ejemplo `x1`), hay que nombrarla tal cual en la pregunta o elegirla
  cuando el sistema lo pregunte.
- **"¿Cuántos días…?"** en datos con varias empresas por día se interpreta como cantidad de
  fechas distintas; para contar registros conviene decir "registros".
- **Valores de categorías:** solo se reconocen en las preguntas los de columnas con hasta 50
  valores distintos.
- **Validación con el dataset real:** al preparar esta entrega no se disponía del dataset real
  del laboratorio ni de las opciones del PDF; las 10 preguntas se validaron con datasets de
  prueba. `tests/test_lab_real.py` está preparado para validarlas cuando se agreguen esos datos.
- **Primer arranque en cada PC:** crear el `.venv` tarda de 1 a 3 minutos.
- **Políticas de dominio** que exigen scripts firmados impiden ejecutar `run.cmd`.

---

## 24. Flujo recomendado para el laboratorio

```text
1. Abrir la carpeta del proyecto (USB) y ejecutar:  run.cmd datos.csv --lab
   (o run.cmd y escribir la ruta cuando la pida)
2. Esperar ENVIRONMENT READY (aceptar la recreación del .venv si la ofrece)
3. Confirmar las opciones del CSV (Enter)
4. Revisar el resumen y los roles semánticos que aparecen tras la carga
5. Si se entró sin --lab: opción 4 (esquema) y 5 (perfil); luego opción 3
6. Pegar la pregunta (con opciones o V/F) y Enter en una línea vacía
7. Revisar la INTERPRETACION y la CONSULTA SPARK SQL
8. Revisar el RESULTADO y la RESPUESTA (y los AVISOS)
9. Si no se entiende la pregunta: :q y opción 9 (Análisis guiado) u 8 (Spark SQL)
10. Al terminar: :x (o opción 7) para exportar la evidencia en JSON y CSV
```

---

## 25. Ejemplo completo

Sesión real con el dataset de prueba `tests/data/stocks_small.csv`:

**Entrada**

```powershell
.\run.cmd --KitOnly tests\data\stocks_small.csv --lab
```

Tras `ENVIRONMENT READY` se confirma el CSV con Enter y aparece el resumen:

```text
CSV detectado -> separador: coma (,) | encabezado: si | codificacion: UTF-8
Es correcto? [S/n]:
Archivo      : stocks_small.csv
Registros    : 12
Columnas     : 7
Vista SQL    : dataset
Roles semanticos detectados (ayuda, no suposicion):
  DATE      -> Date  (nombre = 'date')
  OPEN      -> Open  (nombre = 'open')
  CLOSE     -> Close  (nombre = 'close')
  ...
  PRICE     -> AMBIGUO: Open, High, Low, Close (se preguntara al usarlo)
```

**Pregunta** (pegada en el modo laboratorio, con sus opciones):

```text
10. ¿Cuál fue el registro con mayor volumen individual?
A) AAPL - 2024-01-05
B) NVDA - 2024-01-03
C) NVDA - 2024-01-04
D) MSFT - 2024-01-04
```

**Salida**

```text
INTERPRETACION : RECORD_EXTREME: se busca el registro con mayor Volume.

CONSULTA SPARK SQL:
  SELECT *
  FROM dataset
  WHERE Volume IS NOT NULL
  ORDER BY Volume DESC
  LIMIT 1

RESULTADO      : Volume = 4000 | registro: Date=2024-01-04, Company=NVDA, Open=59, High=59.5, Low=51, Close=52, Volume=4000

RESPUESTA      : C) NVDA - 2024-01-04
```

**Evidencia exportada** (`:x`): la misma información queda en
`exports\evidencia_AAAAMMDD_HHMMSS.json` y `.csv`, con la pregunta, la intención
`RECORD_EXTREME`, el SQL, el resultado, la opción elegida `C` y la fecha.

---

## 26. Buenas prácticas

- Ejecutar `run.cmd --check --KitOnly` en casa después de copiar a la USB, y llegar temprano
  al laboratorio para el primer arranque.
- Leer siempre la **INTERPRETACION** antes de aceptar una respuesta: confirma qué columna y
  qué operación se usaron.
- Copiar la **CONSULTA SPARK SQL** como evidencia en el informe del laboratorio.
- Ante una ambigüedad, elegir con cuidado: la elección de columna se recuerda en la sesión.
- Numerar las preguntas como en el enunciado (`6. ...`) para que el historial coincida.
- Exportar la evidencia (`:x`) al terminar y antes de cerrar el programa.
- En SQL manual, usar `LIMIT` y filtros; no intentar ver todas las filas.
- No instalar paquetes desde Internet, no modificar `JAVA_HOME` de forma permanente y no
  borrar `tools\` (ver [EMERGENCY.md](EMERGENCY.md)).

---

## 27. Conclusión

PySpark Lab Analyzer automatiza la parte repetitiva de un laboratorio de ETL con PySpark:
carga y perfila un dataset desconocido, reconoce el significado de sus columnas, traduce
preguntas en español a consultas Spark SQL mediante un intérprete determinista, y entrega
cada respuesta junto con la consulta que la justifica. Resuelve preguntas abiertas, de
selección múltiple y de verdadero/falso sin forzar respuestas cuando no hay coincidencia, y
ofrece alternativas (análisis guiado, análisis completo y SQL manual) cuando una pregunta no
se reconoce. Se ejecuta desde una USB sin Internet y sin instalar nada en el equipo. Sus
límites están documentados: es un intérprete basado en reglas, no un sistema que entienda
cualquier pregunta, y su rendimiento depende de los recursos del equipo.
