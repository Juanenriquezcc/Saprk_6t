"""Central configuration for PySpark Lab Analyzer.

Every path is resolved from the project folder, so the project works from any
location (for example a USB drive that gets a different letter on each PC).
"""
import os
import tempfile
from pathlib import Path

# --- Paths (always relative to the project folder) ---------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
REQUIREMENTS_FILE = PROJECT_ROOT / "requirements.txt"
# JVM startup warnings. Kept in %TEMP% because the USB drive may be read-only.
JVM_LOG_FILE = Path(tempfile.gettempdir()) / "PySparkLabAnalyzer" / "spark-jvm.log"
EXPORT_DIR = PROJECT_ROOT / "exports"
DATASETS_DIR = PROJECT_ROOT / "datasets"   # optional: its files are listed by number when loading
EXPORT_FALLBACK_DIR = Path(tempfile.gettempdir()) / "PySparkLabAnalyzer" / "exports"

# --- Environment requirements --------------------------------------------------
MIN_PYTHON = (3, 10)
SUPPORTED_JAVA = (17, 21, 25)  # Java versions supported by Spark 4.2

# --- Spark --------------------------------------------------------------------
SPARK_APP_NAME = "PySparkLabAnalyzer"
SPARK_MASTER = "local[*]"
SPARK_LOG_LEVEL = "ERROR"
SPARK_CONF = {
    # ANSI stays on: generated SQL uses NULLIF / try_to_date so it never fails on bad data.
    "spark.sql.ansi.enabled": "true",
    # The default (200) is far too many partitions for a single machine.
    "spark.sql.shuffle.partitions": str(os.cpu_count() or 4),
    "spark.sql.adaptive.enabled": "true",
    "spark.driver.memory": "2g",
    # Avoids network/VPN problems on lab computers.
    "spark.driver.host": "127.0.0.1",
    "spark.driver.bindAddress": "127.0.0.1",
    # Clean output: no Spark UI and no console progress bar.
    "spark.ui.enabled": "false",
    "spark.ui.showConsoleProgress": "false",
    # Spark would create ./spark-warehouse in the current folder (the USB) on catalog commands.
    "spark.sql.warehouse.dir": (Path(tempfile.gettempdir()) / "PySparkLabAnalyzer" / "spark-warehouse").as_uri(),
}

# --- Dataset ------------------------------------------------------------------
VIEW_NAME = "dataset"
# The dataset is cached once after loading. Reason: an interactive lab session runs
# dozens of queries over the same data, and without the cache every query would parse
# the CSV again. 500k rows x ~10 columns is tens of MB, well within the 2g driver.
CACHE_DATASET = True
FORMATS_BY_EXTENSION = {
    ".csv": "csv", ".tsv": "csv", ".txt": "csv",
    ".json": "json", ".jsonl": "json", ".ndjson": "json",
    ".parquet": "parquet", ".pq": "parquet",
}

# --- CSV ----------------------------------------------------------------------
CSV_SNIFF_BYTES = 64 * 1024
CSV_DELIMITERS = ",;\t|"
CSV_INFER_SAMPLING_RATIO = 1.0  # 1.0 = infer types from the whole file (safest)

# --- ETL ----------------------------------------------------------------------
# Text that means "no value" (compared after trim, lowercase). Turned into NULL.
NULL_TOKENS = ("", "na", "n/a", "n.a.", "null", "none", "nan", "nil", "-", "--", "?", "sin dato", "sin datos",
               "sin informacion", "desconocido", "unknown")
# A text column becomes numeric/date only when at least this share of its non-empty values
# has that shape; the rest are reported as invalid values (never guessed).
ETL_CONVERT_MIN_RATIO = 0.9
# Category normalization ('bogota', 'BOGOTA ', 'Bogotá' -> one spelling) is only looked for
# in text columns with at most this many distinct raw values.
ETL_CATEGORY_MAX_DISTINCT = 5000
# Rules file looked for next to the dataset (optional; rules can also be typed).
RULES_FILE_NAMES = ("reglas.txt", "{stem}.reglas.txt", "{stem}_reglas.txt")
RAW_VIEW_NAME = "dataset_original"      # data as read, before the ETL
REJECTED_VIEW_NAME = "rechazados"       # rows rejected by the ETL, with the reason

# --- Profiling ----------------------------------------------------------------
PROFILE_SAMPLE_ROWS = 5
CATEGORY_MAX_DISTINCT = 50       # text columns with at most this many values are categorical
CATEGORY_MAX_RATIO = 0.5         # ... and fewer distinct values than this share of the rows
CATEGORY_VALUES_LIMIT = 100      # distinct values kept per categorical column (to match questions)
CATEGORY_MAX_COLUMNS = 10        # categorical columns whose values are collected
ID_MIN_UNIQUE_RATIO = 0.95
# Values that questions can name ('Ana', 'José Pérez'): text columns with at most this many distinct
# values (names, codes) are kept in memory with the profile, in the same bounded Spark job as the
# categories. Larger columns are searched with Spark only for a capitalized word nothing explained.
VALUE_INDEX_MAX_DISTINCT = 2000
VALUE_INDEX_MAX_COLUMNS = 10
# Relations between catalog tables: candidates proposed per pair of tables (each one is measured
# with Spark only when the user reviews it).
RELATION_MAX_CANDIDATES_PER_PAIR = 5

# --- Results ------------------------------------------------------------------
MAX_DISPLAY_ROWS = 20            # rows shown on screen
MAX_GROUP_ROWS = 100             # rows returned by GROUP BY queries without an explicit limit
MANUAL_SQL_MAX_ROWS = 20         # rows brought to Python by "Ejecutar Spark SQL"

# --- Full analysis (bounded: it never runs one query per column pair) ---------
FULL_ANALYSIS_MAX_STEPS = 12     # maximum number of queries
FULL_ANALYSIS_MAX_COLUMNS = 12   # columns included in each summary query
FULL_ANALYSIS_CATEGORY_COLUMNS = 2   # categorical columns whose top values are listed
FULL_ANALYSIS_TOP_N = 5          # rows of each ranking / top-values list

# --- Semantic roles -------------------------------------------------------------
# Aliases are compared with normalized column names (lowercase, no accents,
# camelCase and snake_case split into words). "kind" filters compatible columns.
SEMANTIC_MIN_SCORE = 0.5
SEMANTIC_AMBIGUITY_MARGIN = 0.15
SEMANTIC_ROLES = {
    "DATE": {"kind": "date", "aliases": [
        "date", "fecha", "day", "dia", "timestamp", "datetime", "time", "fecha hora", "trade date"]},
    "OPEN": {"kind": "numeric", "aliases": [
        "open", "opening", "apertura", "open price", "opening price", "precio apertura", "valor apertura"]},
    "CLOSE": {"kind": "numeric", "aliases": [
        "close", "closing", "cierre", "close price", "closing price", "precio cierre", "valor cierre",
        "adj close", "adjusted close", "cierre ajustado"]},
    "HIGH": {"kind": "numeric", "aliases": [
        "high", "maximo", "max", "high price", "precio maximo", "valor maximo", "alto"]},
    "LOW": {"kind": "numeric", "aliases": [
        "low", "minimo", "min", "low price", "precio minimo", "valor minimo", "bajo"]},
    "VOLUME": {"kind": "numeric", "aliases": ["volume", "volumen", "vol", "shares", "acciones negociadas"]},
    "COMPANY": {"kind": "text", "aliases": [
        "company", "empresa", "compania", "ticker", "symbol", "simbolo", "emisor", "firm", "stock", "accion"]},
    "PRODUCT": {"kind": "text", "aliases": ["product", "producto", "item", "articulo", "sku"]},
    "QUANTITY": {"kind": "numeric", "aliases": ["quantity", "qty", "cantidad", "cant", "unidades", "units"]},
    "PRICE": {"kind": "numeric", "aliases": [
        "price", "precio", "valor", "cost", "costo", "precio unitario", "unit price",
        "open", "close", "high", "low", "apertura", "cierre", "maximo", "minimo"]},
    "AMOUNT": {"kind": "numeric", "aliases": [
        "amount", "importe", "monto", "valor", "revenue", "ingreso", "ingresos", "venta", "ventas", "sales"]},
    "TOTAL": {"kind": "numeric", "aliases": ["total", "subtotal", "grand total", "valor total", "precio total",
                                             "monto total", "importe total"]},
    "NAME": {"kind": "text", "aliases": ["name", "nombre", "descripcion", "description", "titulo", "title"]},
    "ID": {"kind": "any", "aliases": ["id", "codigo", "code", "key", "identificador", "uuid", "nro"]},
    "CATEGORY": {"kind": "text", "aliases": [
        "category", "categoria", "tipo", "type", "clase", "segment", "segmento", "sector", "grupo",
        "group", "familia", "linea"]},
    # Dimensions and measures of sales / orders datasets. "dias" alone is NOT an alias:
    # "cuantos dias ..." counts rows.
    "CITY": {"kind": "text", "aliases": ["city", "ciudad", "municipio", "town", "localidad"]},
    "DEPARTMENT": {"kind": "text", "aliases": [
        "department", "departamento", "state", "provincia", "region", "estado provincia"]},
    "CHANNEL": {"kind": "text", "aliases": ["channel", "canal", "canal venta", "sales channel"]},
    "PAYMENT": {"kind": "text", "aliases": [
        "payment method", "metodo pago", "forma pago", "medio pago", "payment", "pago", "payment type", "tipo pago"]},
    "CUSTOMER_TYPE": {"kind": "text", "aliases": [
        "customer type", "tipo cliente", "segmento cliente", "customer segment", "client type"]},
    "STATUS": {"kind": "text", "aliases": [
        "status", "estado", "estado pedido", "order status", "situacion", "estatus"]},
    "DISCOUNT": {"kind": "numeric", "aliases": [
        "discount", "descuento", "discount pct", "porcentaje descuento", "dcto", "desc"]},
    "DAYS": {"kind": "numeric", "aliases": [
        "shipping days", "dias envio", "dias entrega", "delivery days", "tiempo entrega", "lead time", "dias despacho"]},
    "RETURNS": {"kind": "numeric", "aliases": [
        "returned qty", "returned quantity", "returns", "devoluciones", "cantidad devuelta", "unidades devueltas"]},
    "AGE": {"kind": "numeric", "aliases": ["age", "edad", "customer age", "edad cliente"]},
}
# What each role is for the OLAP model (shown in "Ver esquema"; never used to guess).
ROLE_CLASS = {
    "DATE": "tiempo", "ID": "identificador",
    "COMPANY": "dimension", "PRODUCT": "dimension", "CATEGORY": "dimension", "NAME": "atributo",
    "CITY": "dimension", "DEPARTMENT": "dimension", "CHANNEL": "dimension", "PAYMENT": "dimension",
    "CUSTOMER_TYPE": "dimension", "STATUS": "atributo",
    "OPEN": "medida", "CLOSE": "medida", "HIGH": "medida", "LOW": "medida", "VOLUME": "medida",
    "QUANTITY": "medida", "PRICE": "medida", "AMOUNT": "medida", "TOTAL": "medida", "DISCOUNT": "medida",
    "DAYS": "medida", "RETURNS": "medida", "AGE": "medida",
}
