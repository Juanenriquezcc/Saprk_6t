"""Format detection, PySpark reading, validation and registration of a view (`dataset` by default).

Data never travels to Python: only `count()` and schema metadata.
"""
import codecs
import csv
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import config
from app.errors import AppError, short_error

CORRUPT_COL = "_corrupt_record"
_VALID_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class CsvOptions:
    sep: str = ","
    header: bool = True
    encoding: str = "UTF-8"  # Java/Spark charset name


@dataclass
class LoadResult:
    df: object
    path: Path
    fmt: str
    rows: int
    columns: list
    renamed: list = field(default_factory=list)   # [(original, new)]
    malformed: int = 0
    warnings: list = field(default_factory=list)
    seconds: float = 0.0
    csv_options: CsvOptions = None


# --- Path and format ------------------------------------------------------------

def resolve_path(raw):
    text = raw.strip().lstrip("\ufeff").strip('"').strip("'")  # BOM: input piped from PowerShell
    if not text:
        raise AppError("No se indico ninguna ruta.")
    path = Path(text).expanduser().resolve()
    if not path.exists():
        raise AppError(f"No existe: {path}", "Revise la ruta. Puede arrastrar el archivo a la terminal.")
    if path.is_file() and path.stat().st_size == 0:
        raise AppError(f"El archivo esta vacio: {path.name}")
    if path.is_dir() and not _data_files(path):
        raise AppError(f"La carpeta no contiene archivos de datos: {path}")
    return path


def detect_format(path):
    """'csv' | 'json' | 'parquet', or None when it cannot be determined."""
    if path.is_dir():
        found = {config.FORMATS_BY_EXTENSION.get(f.suffix.lower()) for f in _data_files(path)}
        return next((fmt for fmt in ("parquet", "json", "csv") if fmt in found), None)
    return config.FORMATS_BY_EXTENSION.get(path.suffix.lower()) or _sniff_format(path)


def _data_files(directory):
    # Spark also writes _SUCCESS and .crc files: ignored.
    return sorted(f for f in directory.rglob("*") if f.is_file() and not f.name.startswith(("_", ".")))


def _first_file(path):
    return path if path.is_file() else _data_files(path)[0]


def _spark_paths(path, fmt):
    """Explicit file paths for Spark.

    On Windows, listing a directory makes Hadoop call native code (hadoop.dll /
    winutils) that a clean PC does not have; explicit files do not need it.
    ponytail: partitioned folders (col=value/) lose the partition column.
    """
    if path.is_file():
        return [str(path)]
    files = [str(f) for f in _data_files(path) if config.FORMATS_BY_EXTENSION.get(f.suffix.lower()) == fmt]
    if not files:
        raise AppError(f"La carpeta no contiene archivos {fmt.upper()}: {path}")
    return files


def _sniff_format(path):
    head = path.open("rb").read(4096)
    if head.startswith(b"PAR1"):
        return "parquet"
    if b"\x00" in head:
        return None  # unknown binary
    text = head.decode("utf-8", errors="ignore").lstrip("\ufeff \t\r\n")
    if text[:1] in ("{", "["):
        return "json"
    first_line = text.splitlines()[0] if text else ""
    return "csv" if any(d in first_line for d in config.CSV_DELIMITERS) else None


# --- CSV ----------------------------------------------------------------------

def _sample(path):
    """(text of the first KB without the last incomplete line, Spark encoding name)."""
    raw = _first_file(path).open("rb").read(config.CSV_SNIFF_BYTES)
    try:
        # final=False tolerates a multibyte character cut at the end of the sample.
        text = codecs.getincrementaldecoder("utf-8-sig")().decode(raw, final=False)
        encoding = "UTF-8"
    except UnicodeDecodeError:
        # Spark 4.2 rejects windows-1252 for CSV; ISO-8859-1 reads accents and n-tilde the same.
        text = raw.decode("latin-1")
        encoding = "ISO-8859-1"
    if len(raw) == config.CSV_SNIFF_BYTES and "\n" in text:
        text = text[: text.rfind("\n")]  # drop the last, incomplete line
    return text, encoding


def sample_text(path):
    return _sample(path)[0]


def sniff_csv(path):
    """Detects separator, header and encoding reading only the first KB."""
    text, encoding = _sample(path)
    sniffer = csv.Sniffer()
    try:
        sep = sniffer.sniff(text, delimiters=config.CSV_DELIMITERS).delimiter
    except csv.Error:
        first_line = text.splitlines()[0] if text else ""
        sep = max(config.CSV_DELIMITERS, key=first_line.count) if first_line else ","
        if first_line.count(sep) == 0:
            sep = ","
    try:
        header = sniffer.has_header(text)
    except csv.Error:
        header = True
    return CsvOptions(sep=sep, header=header, encoding=encoding)


def _csv_reader(spark, opts):
    return (spark.read.option("header", opts.header).option("sep", opts.sep)
            .option("encoding", opts.encoding).option("mode", "PERMISSIVE"))


def _read_csv(spark, path, opts):
    return (_csv_reader(spark, opts).option("inferSchema", True)
            .option("samplingRatio", config.CSV_INFER_SAMPLING_RATIO).csv(_spark_paths(path, "csv")))


def _csv_warnings(df, opts):
    # A single column whose name contains another separator = probably the wrong separator.
    if len(df.columns) == 1:
        others = [d for d in config.CSV_DELIMITERS if d != opts.sep and d in df.columns[0]]
        if others:
            return [f"Solo se detecto 1 columna y su nombre contiene {others[0]!r}: "
                    f"el separador {opts.sep!r} probablemente es incorrecto."]
    return []


# --- JSON ---------------------------------------------------------------------

def _read_json(spark, path):
    head = _first_file(path).open("rb").read(4096).decode("utf-8", errors="ignore")
    multiline = head.lstrip("\ufeff \t\r\n").startswith("[")
    return spark.read.option("multiLine", multiline).option("mode", "PERMISSIVE").json(_spark_paths(path, "json"))


def _flatten_structs(df):
    """Flattens one level of struct columns: info.city -> info_city."""
    from pyspark.sql import functions as F
    from pyspark.sql.types import StructType

    if not any(isinstance(f.dataType, StructType) for f in df.schema.fields):
        return df
    cols = []
    for f in df.schema.fields:
        if isinstance(f.dataType, StructType):
            cols += [F.col(f"{_q(f.name)}.{_q(sub.name)}").alias(f"{f.name}_{sub.name}")
                     for sub in f.dataType.fields]
        else:
            cols.append(F.col(_q(f.name)))
    return df.select(*cols)


def _q(name):
    return "`" + name.replace("`", "``") + "`"


# --- Column names -------------------------------------------------------------

def sanitize_columns(names):
    """Keeps valid names; cleans problematic ones and removes duplicates.

    Returns (new_names, [(original, new), ...]).
    """
    result, renamed, seen = [], [], set()
    for i, name in enumerate(names):
        new = name if _VALID_NAME.match(name) else _clean_name(name, i)
        base, n = new, 2
        while new.lower() in seen:  # Spark SQL is case-insensitive
            new, n = f"{base}_{n}", n + 1
        seen.add(new.lower())
        result.append(new)
        if new != name:
            renamed.append((name, new))
    return result, renamed


def _clean_name(name, index):
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    clean = re.sub(r"[^A-Za-z0-9]+", "_", ascii_name).strip("_")
    if not clean:
        return f"col_{index + 1}"
    return f"c_{clean}" if clean[0].isdigit() else clean


# --- Load ---------------------------------------------------------------------

def load_dataset(spark, path, fmt, csv_options=None, view=config.VIEW_NAME):
    from pyspark.sql import functions as F

    start = time.perf_counter()
    warnings = []
    try:
        if fmt == "csv":
            csv_options = csv_options or sniff_csv(path)
            df = _read_csv(spark, path, csv_options)
            warnings += _csv_warnings(df, csv_options)
        elif fmt == "json":
            df = _flatten_structs(_read_json(spark, path))
        elif fmt == "parquet":
            df = spark.read.parquet(*_spark_paths(path, "parquet"))
        else:
            raise AppError(f"Formato no soportado: {fmt}", "Formatos validos: CSV, JSON, Parquet.")

        has_corrupt = CORRUPT_COL in df.columns
        if not [c for c in df.columns if c != CORRUPT_COL]:
            raise AppError(f"El archivo no contiene columnas legibles como {fmt.upper()}.",
                           "Verifique que el formato elegido sea el correcto.")

        if config.CACHE_DATASET:   # see the reason in config.py
            df = df.cache()
        rows = df.count()
        malformed = 0
        if has_corrupt:
            # Allowed because the DataFrame is cached (Spark requires it for this column).
            malformed = df.filter(F.col(CORRUPT_COL).isNotNull()).count()
            df = df.drop(CORRUPT_COL)
    except AppError:
        raise
    except Exception as exc:
        raise AppError(f"No se pudo leer el archivo como {fmt.upper()}: {short_error(exc)}",
                       "Puede estar danado o tener otro formato/separador.") from exc

    if rows == 0:
        df.unpersist()
        raise AppError("El dataset esta vacio (no tiene registros de datos).")

    names, renamed = sanitize_columns(df.columns)
    if renamed:
        df = df.toDF(*names)
    if malformed:
        warnings.append(f"{malformed} registro(s) mal formados: se conservaron con valores nulos.")
    df.createOrReplaceTempView(view)

    return LoadResult(df=df, path=path, fmt=fmt, rows=rows, columns=list(df.columns),
                      renamed=renamed, malformed=malformed, warnings=warnings,
                      seconds=time.perf_counter() - start, csv_options=csv_options)
