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
.\run.cmd                               # comprueba el entorno y pide la ruta del dataset
.\run.cmd datos.csv --lab               # directo al modo laboratorio
.\run.cmd --check                       # solo comprobar el entorno
.\run.cmd --KitOnly datos.csv --lab     # usar solo el Python/Java del kit
.\run.cmd --rebuild-venv                # recrear el entorno virtual desde wheels\
```

En el modo laboratorio se pega la pregunta (con opciones A–D si las tiene) y se pulsa Enter
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
app/                           carga (spark/), perfil (schema/), SQL (query/),
                               preguntas (questions/), análisis (analysis/), interfaz (ui/)
tests/                         pruebas pytest y datasets pequeños de prueba
tools/, wheels/                kit offline (no versionado; run.cmd -PrepareKit)
```

## Pruebas

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt   # pytest, pyarrow (con Internet)
.\.venv\Scripts\python.exe -m pytest                                # todas (~10 min)
.\.venv\Scripts\python.exe -m pytest -m "not slow"                  # rápidas (~1 min)
```

Incluyen: carga, perfil, roles semánticos, generador de SQL, intérprete, las 10 preguntas del
laboratorio sobre dos datasets distintos, SQL manual, análisis guiado y completo, recuperación
tras errores, auditorías (rutas absolutas, acceso a Internet, APIs que traerían el dataset a
Python), un ensayo completo de USB offline con cambio de letra de unidad y una prueba con
500.000 registros.

## Capacidad: 500.000 registros

Probado con un CSV de **500.000 registros** (`tests/test_smoke_500k.py`): carga en ≈ 3–9 s,
consultas (COUNT, AVG, GROUP BY, TOP, registro extremo, variación porcentual) entre 0.05 y
0.5 s y análisis completo en ≈ 1.6 s. La memoria del proceso Python se mantuvo constante
(66 MB → 66 MB), porque los registros nunca se traen a Python. Equipo de prueba: Windows 11,
8 núcleos, 7.8 GB de RAM. **No es una garantía para cualquier tamaño:** el rendimiento depende
de la RAM, la CPU, las columnas, el formato y las consultas (detalle en la sección 19 del manual).

## Limitaciones

- Solo Windows x64. Rendimiento sujeto a la RAM y CPU del equipo.
- El intérprete se basa en reglas: no reconoce cualquier redacción (ver sección 8 del manual).
  Para esos casos están el análisis guiado y el SQL manual.
- Las 10 preguntas del laboratorio se validaron con datasets de prueba; el dataset real del
  laboratorio y las opciones del PDF no estaban disponibles (`tests/test_lab_real.py` está
  preparado para ellos).
- Lista completa en la sección 23 del manual.
