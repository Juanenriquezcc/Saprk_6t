# PySpark Lab Analyzer

Aplicación de terminal para analizar datasets con **PySpark** y **Spark SQL** y resolver
laboratorios académicos de ETL. Carga un dataset desconocido, lo perfila, reconoce el
significado de sus columnas y responde preguntas en español (abiertas, de selección múltiple
y de verdadero/falso) mostrando **la consulta Spark SQL que justifica cada respuesta**.

Está pensada para ejecutarse desde una **USB en un PC no preparado**: sin Internet, sin
permisos de administrador y sin Python ni Java instalados.

📘 **Documentación completa: [MANUAL_USUARIO.md](MANUAL_USUARIO.md)** ·
🚑 Si algo falla durante el laboratorio: [EMERGENCY.md](EMERGENCY.md)

## Características

- **Modo Taller**: pregunta → tipo (abierta / A–D / V/F) → respuesta del estudiante →
  **validación automática** (CORRECTA, INCORRECTA, NO DETERMINADA...) → siguiente pregunta →
  **Finalizar taller** genera la evidencia completa (`exports\taller_<fecha>\`: SQL, CSV, JSON,
  TXT, resumen y reporte del ETL).
- **ETL real** (Extract → Transform → Validate → Load): espacios, vacíos (`N/A`, `-`), números
  escritos como texto (`1.250.000`, `12,5`), fechas en varios formatos, categorías con
  distinta escritura (`Bogotá`/`BOGOTA`), duplicados, **reglas de calidad del taller**
  (`quantity entre 1 y 20`, `returned_qty <= quantity`...), registros rechazados con su motivo
  (vista `rechazados`). Lo que los datos no permiten decidir se pregunta; nada se rellena.
- **OLAP sobre Spark SQL**: COUNT/DISTINCT/SUM/AVG/MIN/MAX, mediana, percentiles, desviación,
  GROUP BY, TOP N, HAVING ("ciudades con más de 100 pedidos"), análisis por mes/trimestre/año,
  acumulados (funciones de ventana), comparaciones entre columnas y entre grupos, métricas
  calculadas (cantidad × precio × (1 − descuento)), V/F comparativas ("es mayor a 500000").
- Carga de **CSV, JSON y Parquet** con detección de separador, encabezado y codificación.
- **Perfilado** (tipos, nulos, cardinalidad, estadísticas) y **roles semánticos** (fecha,
  apertura, cierre, volumen, empresa…) aunque las columnas tengan otros nombres.
- **Preguntas en lenguaje natural** con un intérprete determinista basado en reglas (sin IA,
  sin Internet): conteos, promedios, máximos, sumas, agrupaciones, rankings, registro
  extremo, diferencias, variaciones porcentuales, comparaciones, selección múltiple y V/F.
  Si una pregunta es ambigua, pregunta; si no la reconoce, lo dice (nunca inventa).
- **Modo laboratorio** para responder rápido, **análisis guiado** (8 operaciones),
  **análisis completo** acotado y **Spark SQL manual** de solo lectura.
- **Evidencia** de cada operación (pregunta, interpretación, SQL, resultado, respuesta,
  tiempo) exportable a **JSON y CSV**.
- Arranque **portátil y offline** con comprobación del entorno en 9 pasos.

## Requisitos

- Windows 10/11 **x64**.
- 8 GB de RAM recomendados (Spark usa hasta 2 GB) y ≈ 1.3 GB de disco.
- Para **preparar el kit** (una vez): Internet. Para **usarlo**: nada más.

## Instalación

El repositorio contiene el código, las pruebas y la documentación. El **kit offline**
(`tools\python`, `tools\jre`, `wheels\`, ≈ 600 MB) no está en Git, porque el wheel de PySpark
(430 MB) supera el límite de 100 MB por archivo de GitHub. Se descarga con un comando:

```powershell
git clone https://github.com/Juanenriquezcc/Saprk_6t.git pyspark_lab_analyzer
cd pyspark_lab_analyzer
.\run.cmd -PrepareKit            # descarga Python 3.12 portable, JRE 17 y los wheels (con Internet)
.\run.cmd --check --KitOnly      # crea el entorno virtual sin Internet y debe terminar en ENVIRONMENT READY
```

`-PrepareKit` verifica los checksums y la firma digital de Python. Es la única operación que
usa Internet; todo lo demás funciona offline.

## Uso

```powershell
.\run.cmd                               # comprueba el entorno, pide el dataset, ETL y Modo Taller
.\run.cmd datos.csv                     # igual, con el dataset indicado
.\run.cmd datos.csv --lab               # tras el ETL, modo laboratorio rápido (sin validación)
.\run.cmd --check                       # solo comprobar el entorno
.\run.cmd --KitOnly datos.csv --lab     # usar solo el Python/Java del kit
.\run.cmd --rebuild-venv                # recrear el entorno virtual desde wheels\
```

Guía de uso paso a paso: **sección 0 de [MANUAL_USUARIO.md](MANUAL_USUARIO.md)**. Ejemplo del
Modo Taller (dataset de prueba `tests/data/ventas_sucio.csv`, tras el ETL):

```text
> ¿Cuál categoría genera mayores ingresos?
  A) Hogar
  B) Tecnologia
  C) Ropa
  D) Alimentos
Respuesta que marco el estudiante (A/B/C/D; Enter = ninguna): B

METRICA AMBIGUA: se detecto 'ingresos'. Seleccione la interpretacion indicada por el taller.
  1. quantity x unit_price_cop  (cantidad x precio)
  2. quantity x unit_price_cop x (1 - discount)  (con descuento)
Respuesta correcta:      B) Tecnologia
Respuesta seleccionada:  B) Tecnologia
                VALIDACION: CORRECTA
SQL:
  SELECT category, SUM((quantity * unit_price_cop)) AS sum_ingresos
  FROM dataset GROUP BY category ORDER BY sum_ingresos DESC NULLS LAST, category ASC LIMIT 1
```

En el modo laboratorio rápido (`--lab`) se pega la pregunta (con opciones A–D si las tiene) y se pulsa Enter
en una línea vacía:

```text
> 9. ¿Cuál empresa presentó el mayor promedio de variación porcentual entre apertura y cierre?

INTERPRETACION : PCT_CHANGE: se calcula el promedio de variacion % de Open a Close para cada Company y se toma el mayor.
CONSULTA SPARK SQL:
  SELECT Company,
         AVG(((Close - Open) / NULLIF(Open, 0) * 100)) AS avg_variacion_pct
  FROM dataset
  GROUP BY Company
  ORDER BY avg_variacion_pct DESC NULLS LAST, Company ASC
  LIMIT 1
RESPUESTA      : NVDA (promedio = 3.9929%)          (dataset de prueba stocks_small.csv)
```

Para llevarlo en una USB, ver la sección 22 del manual.

## Estructura

```text
run.cmd, run.ps1, bootstrap/   arranque portátil y comprobación del entorno
main.py, config.py             punto de entrada y configuración
app/spark/                     EXTRACT: SparkSession y carga (CSV/JSON/Parquet)
app/etl/                       TRANSFORM (transformer), VALIDATE (validator, rules), LOAD (pipeline)
app/schema/                    perfil y roles semánticos (dimensiones, medidas, tiempo)
app/query/                     QuerySpec -> SQL builder (único generador de SQL) -> executor
app/questions/                 parser, intérprete (intents), resolver, validación de respuestas
app/analysis/, app/export.py   análisis guiado/completo; resumen y exportación del taller
app/ui/                        menú del taller (workshop), ETL, herramientas avanzadas (menu)
tests/                         pruebas pytest y datasets pequeños de prueba
tools/, wheels/                kit offline (no versionado; run.cmd -PrepareKit)
```

## Pruebas

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt   # pytest, pyarrow (con Internet)
.\.venv\Scripts\python.exe -m pytest                                # todas (~10 min)
.\.venv\Scripts\python.exe -m pytest -m "not slow"                  # rápidas (~1 min)
```

Incluyen: ETL y calidad de datos (sobre un CSV sucio con resultados contados a mano), OLAP,
preguntas sobre datos limpiados, validación de respuestas, exportación del taller, el menú del
taller manejado con entradas simuladas, carga, perfil, roles semánticos, generador de SQL, intérprete, las 10 preguntas del
laboratorio sobre dos datasets distintos, SQL manual, análisis guiado y completo, recuperación
tras errores, auditorías (rutas absolutas, acceso a Internet, APIs que traerían el dataset a
Python), un ensayo completo de USB offline con cambio de letra de unidad y una prueba con
500.000 registros (limpios y sucios, con el ETL completo y un oráculo independiente).

## Capacidad: 500.000 registros

Probado con un CSV de **500.000 registros** (`tests/test_smoke_500k.py`): carga en ≈ 3–9 s,
consultas (COUNT, AVG, GROUP BY, TOP, registro extremo, variación porcentual) entre 0.05 y
0.5 s y análisis completo en ≈ 1.6 s. La memoria del proceso Python se mantuvo constante
(66 MB → 66 MB), porque los registros nunca se traen a Python. Equipo de prueba: Windows 11,
8 núcleos, 7.8 GB de RAM. **No es una garantía para cualquier tamaño:** el rendimiento depende
de la RAM, la CPU, las columnas, el formato y las consultas (detalle en la sección 19 del manual).

Con **500.050 registros sucios** (variantes de ciudad y estado, `N/A`, cantidades inválidas,
fechas mezcladas, precios `12.000`, duplicados): ETL completo ≈ 17 s con 8 núcleos (≈ 27 s
con 1 núcleo en las pruebas), preguntas del taller entre 0.07 y 0.7 s; memoria de Python
61 → 62 MB.

## Limitaciones

- Solo Windows x64. Rendimiento sujeto a la RAM y CPU del equipo.
- El intérprete se basa en reglas: no reconoce cualquier redacción (ver sección 8 del manual).
  Para esos casos están el análisis guiado y el SQL manual.
- Las 10 preguntas del laboratorio se validaron con datasets de prueba; el dataset real del
  laboratorio y las opciones del PDF no estaban disponibles (`tests/test_lab_real.py` está
  preparado para ellos).
- Lista completa en la sección 23 del manual.
