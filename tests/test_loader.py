import shutil

import pytest

import config
from app.errors import AppError
from app.spark.loader import CsvOptions, detect_format, load_dataset, resolve_path, sanitize_columns, sniff_csv


def load(spark, path, csv_options=None):
    path = resolve_path(str(path))
    fmt = detect_format(path)
    if fmt == "csv" and csv_options is None:
        csv_options = sniff_csv(path)
    return load_dataset(spark, path, fmt, csv_options)


def count_view(spark):
    return spark.sql(f"SELECT COUNT(*) AS n FROM {config.VIEW_NAME}").first()["n"]


def write_parquet(path, ids):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    pq.write_table(pa.table({"id": ids, "value": [i * 1.5 for i in ids]}), str(path))


# --- CSV ----------------------------------------------------------------------

def test_csv_schema_types_and_view(spark, data_dir):
    r = load(spark, data_dir / "stocks_small.csv")
    assert (r.fmt, r.rows, len(r.columns)) == ("csv", 12, 7)
    assert (r.csv_options.sep, r.csv_options.header, r.csv_options.encoding) == (",", True, "UTF-8")
    types = dict(r.df.dtypes)
    assert types["Open"] == "double" and types["Close"] == "double"
    assert types["Volume"] in ("int", "bigint")
    assert types["Date"] in ("date", "timestamp")
    assert types["Company"] == "string"
    assert r.renamed == [] and r.malformed == 0
    assert count_view(spark) == 12


def test_csv_semicolon_latin1_with_accents(spark, tmp_path):
    p = tmp_path / "ventas.csv"
    p.write_bytes("producto;categoría;cantidad;precio_unitario\n"
                  "Café;Bebidas;3;2.5\nTé;Bebidas;1;1.75\nPan;Panadería;10;0.5\n".encode("cp1252"))
    r = load(spark, p)
    assert r.csv_options.sep == ";"
    assert r.csv_options.encoding == "ISO-8859-1"
    assert ("categoría", "categoria") in r.renamed
    assert r.rows == 3
    assert r.df.filter("producto = 'Café'").count() == 1  # las tildes se leyeron bien
    assert dict(r.df.dtypes)["cantidad"] == "int"


def test_tab_separated(spark, tmp_path):
    p = tmp_path / "datos.tsv"
    p.write_text("x\ty\n1\t2\n3\t4\n", encoding="utf-8")
    r = load(spark, p)
    assert r.csv_options.sep == "\t" and r.columns == ["x", "y"] and r.rows == 2


def test_problem_and_duplicate_columns_are_sanitized(spark, data_dir):
    r = load(spark, data_dir / "problem_columns.csv")
    assert r.rows == 2 and len(r.columns) == 6
    lowered = [c.lower() for c in r.columns]
    assert len(set(lowered)) == 6, r.columns  # sin duplicados (Spark SQL no distingue mayúsculas)
    renamed = dict(r.renamed)
    assert renamed["Valor Total"] == "Valor_Total"
    assert renamed["Año"] == "Ano"
    assert renamed["2024"] == "c_2024"
    assert renamed["a.b"] == "a_b"
    # Todas las columnas se pueden usar en SQL sin comillas.
    spark.sql("SELECT " + ", ".join(r.columns) + f" FROM {config.VIEW_NAME}").collect()


def test_malformed_csv_rows_are_kept(spark, data_dir):
    # Spark (PERMISSIVE) conserva filas con más/menos campos: la carga no debe fallar.
    r = load(spark, data_dir / "malformed.csv")
    assert r.rows == 4 and r.columns == ["id", "name", "value"]
    assert r.df.filter("id = 4").first()["name"] == "d, con coma"


def test_wrong_separator_produces_warning(spark, data_dir):
    r = load(spark, data_dir / "stocks_small.csv", CsvOptions(sep=";", header=True))
    assert len(r.columns) == 1
    assert any("separador" in w for w in r.warnings)


def test_header_only_csv_is_empty_dataset(spark, data_dir):
    with pytest.raises(AppError, match="vacio"):
        load(spark, data_dir / "header_only.csv")


# --- JSON ---------------------------------------------------------------------

def test_json_lines_with_nested_struct(spark, data_dir):
    r = load(spark, data_dir / "events.jsonl")
    assert r.fmt == "json" and r.rows == 3
    assert set(r.columns) == {"id", "amount", "user_name", "user_age"}
    assert spark.sql(f"SELECT SUM(user_age) AS s FROM {config.VIEW_NAME}").first()["s"] == 96


def test_json_array_multiline(spark, data_dir):
    r = load(spark, data_dir / "array.json")
    assert r.rows == 2 and set(r.columns) == {"a", "b"}


def test_json_malformed_lines_are_counted(spark, data_dir):
    r = load(spark, data_dir / "broken.jsonl")
    assert r.malformed == 1
    assert "_corrupt_record" not in r.columns
    assert any("mal formados" in w for w in r.warnings)


# --- Parquet ------------------------------------------------------------------

def test_parquet_file(spark, tmp_path):
    p = tmp_path / "data.parquet"
    write_parquet(p, [1, 2, 3])
    r = load(spark, p)
    assert r.fmt == "parquet" and r.rows == 3 and dict(r.df.dtypes)["value"] == "double"


def test_parquet_directory_written_by_spark_style(spark, tmp_path):
    d = tmp_path / "out"
    d.mkdir()
    write_parquet(d / "part-00000.parquet", [1, 2])
    write_parquet(d / "part-00001.parquet", [3, 4, 5])
    (d / "_SUCCESS").write_bytes(b"")
    r = load(spark, d)
    assert r.fmt == "parquet" and r.rows == 5


# --- Detección de formato y validaciones --------------------------------------

def test_format_detected_by_content_when_extension_unknown(tmp_path, data_dir):
    csv_copy = tmp_path / "datos.dat"
    shutil.copy(data_dir / "stocks_small.csv", csv_copy)
    assert detect_format(csv_copy) == "csv"

    json_copy = tmp_path / "eventos"
    shutil.copy(data_dir / "events.jsonl", json_copy)
    assert detect_format(json_copy) == "json"

    parquet_blob = tmp_path / "blob.bin"
    write_parquet(parquet_blob, [1])
    assert detect_format(parquet_blob) == "parquet"

    garbage = tmp_path / "x.bin"
    garbage.write_bytes(b"\x00\x01\x02\x03binario")
    assert detect_format(garbage) is None


def test_missing_file_gives_clear_error():
    with pytest.raises(AppError, match="No existe"):
        resolve_path("no/existe/datos.csv")


def test_quoted_path_from_drag_and_drop(data_dir):
    assert resolve_path(f'"{data_dir / "stocks_small.csv"}"').name == "stocks_small.csv"


def test_empty_file_and_empty_dir(tmp_path):
    empty = tmp_path / "vacio.csv"
    empty.write_bytes(b"")
    with pytest.raises(AppError, match="vacio"):
        resolve_path(str(empty))
    empty_dir = tmp_path / "nada"
    empty_dir.mkdir()
    (empty_dir / "_SUCCESS").write_bytes(b"")  # solo marcadores de Spark: no cuenta como dato
    with pytest.raises(AppError, match="no contiene"):
        resolve_path(str(empty_dir))


def test_unsupported_format(spark, data_dir):
    with pytest.raises(AppError, match="no soportado"):
        load_dataset(spark, data_dir / "stocks_small.csv", "xml")


def test_sanitize_columns_unit():
    names, renamed = sanitize_columns(["ok", "OK", "con espacio", "", "9x", "ñandú"])
    assert names == ["ok", "OK_2", "con_espacio", "col_4", "c_9x", "nandu"]
    assert ("OK", "OK_2") in renamed and ("ok", "ok") not in renamed
