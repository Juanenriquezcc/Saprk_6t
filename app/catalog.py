"""Catalog of the datasets of one session: several tables loaded at once, each in its own view.

Alias = Spark view name. A table registered as 'pedidos' is queried as `pedidos`; after its
ETL the data as read is in `pedidos_original` and the rejected rows in `pedidos_rechazados`
(app/etl/pipeline.etl_views). So manual SQL can already combine tables by alias:
    SELECT ... FROM pedidos p JOIN clientes c ON ...
The single-dataset flow (`dataset`, `dataset_original`, `rechazados`) is separate from the
catalog: those names are reserved and a catalog table can never replace them, nor any other
existing view. Registering never overwrites anything; a failed registration leaves nothing.
"""
import re
from dataclasses import dataclass
from pathlib import Path

from app.errors import AppError
from app.etl.pipeline import etl_views
from app.query.builder import ident
from app.schema.profiler import profile_dataset
from app.schema.relations import RelationSet
from app.schema.semantic import detect_roles
from app.spark.loader import detect_format, load_dataset
from app.text import strip_accents

ALIAS = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
RESERVED_VIEWS = set(etl_views())          # views of the single-dataset flow


def describe(load, etl=None):
    """(profile, semantic roles) of the data that is queried: the clean data after an ETL."""
    df, rows = (etl.df, etl.valid_rows) if etl else (load.df, load.rows)
    profile = profile_dataset(df, rows)
    if etl:
        etl.null_counts = {c.name: c.nulls for c in profile.columns}
    return profile, detect_roles(profile)


@dataclass
class CatalogEntry:
    alias: str
    load: object              # LoadResult (data as read)
    profile: object           # DatasetProfile of the queried data
    semantic: object          # SemanticMap
    etl: object = None        # EtlReport, when the table went through the ETL
    shared: bool = False      # the active dataset adopted by the catalog: its data belongs to the session

    @property
    def view(self):
        return self.alias

    @property
    def views(self):
        """Views registered in Spark for this table."""
        return self.etl.views if self.etl and not self.shared else (self.alias,)

    @property
    def df(self):
        return self.etl.df if self.etl else self.load.df


class DatasetCatalog:
    """Datasets of the session by alias, in registration order."""

    def __init__(self):
        self._entries = {}
        self.relations = RelationSet()    # relations among these tables (app/schema/relations.py)

    def __len__(self):
        return len(self._entries)

    def __iter__(self):
        return iter(list(self._entries.values()))

    def __contains__(self, alias):
        return str(alias).strip().lower() in self._entries

    def get(self, alias):
        entry = self._entries.get(str(alias).strip().lower())
        if entry is None:
            raise AppError(f"No hay ningun dataset registrado con el alias '{alias}'.",
                           "Registrados: " + (", ".join(self._entries) or "ninguno"))
        return entry

    def _used_views(self):
        return RESERVED_VIEWS | {v for alias in self._entries for v in etl_views(alias)}

    def check_alias(self, spark, alias):
        """The normalized alias, or AppError when it cannot be used. Runs before loading anything."""
        name = str(alias or "").strip().lower()
        if not ALIAS.match(name) or ident(name) != name:
            raise AppError(f"Alias no valido: '{alias}'.",
                           "Use minusculas, numeros y '_', empezando por una letra (maximo 40), "
                           "y no una palabra de SQL (select, join, order...).")
        if name in self._entries:
            raise AppError(f"Ya existe un dataset con el alias '{name}'.",
                           "Elija otro alias: el dataset ya registrado no se modifica.")
        used = self._used_views()
        taken = [v for v in etl_views(name) if v in used or spark.catalog.tableExists(v)]
        if taken:
            raise AppError(f"El alias '{name}' usaria la vista '{taken[0]}', que ya existe.",
                           "Elija otro alias: las vistas existentes no se reemplazan.")
        return name

    def suggest_alias(self, path):
        """Alias from the file name ('Ventas Colombia.csv' -> 'ventas_colombia'), not yet used."""
        base = re.sub(r"[^a-z0-9]+", "_", strip_accents(Path(path).stem).lower()).strip("_")[:34]
        if not base or not base[0].isalpha() or ident(base) != base:
            base = f"t_{base}".rstrip("_")
        used, alias, n = self._used_views(), base, 2
        while any(v in used for v in etl_views(alias)) or alias in self._entries:
            alias, n = f"{base}_{n}", n + 1
        return alias

    def register(self, spark, alias, path, fmt=None, csv_options=None, etl=None):
        """Loads `path` as the table `alias` (its view has the same name). `etl(load, view)`
        optionally cleans it and returns its EtlReport. Returns the CatalogEntry."""
        alias = self.check_alias(spark, alias)
        path = Path(path)
        fmt = fmt or detect_format(path)
        if fmt is None:
            raise AppError(f"No se pudo determinar el formato de {path.name}.", "Formatos validos: CSV, JSON, Parquet.")
        load = report = None
        try:
            load = load_dataset(spark, path, fmt, csv_options, view=alias)
            report = etl(load, alias) if etl else None
            profile, semantic = describe(load, report)
        except BaseException:          # also a cancelled ETL (Ctrl+C): nothing stays half registered
            _release(spark, alias, load, report)
            raise
        entry = CatalogEntry(alias, load, profile, semantic, report)
        self._entries[alias] = entry
        return entry

    def adopt(self, spark, alias, load, profile, semantic, etl=None):
        """The dataset already loaded (and cleaned) by the session joins the catalog as `alias`, without
        reading or cleaning it again: one more view over the same DataFrame."""
        alias = self.check_alias(spark, alias)
        (etl.df if etl else load.df).createOrReplaceTempView(alias)
        entry = CatalogEntry(alias, load, profile, semantic, etl, shared=True)
        self._entries[alias] = entry
        return entry

    def find_path(self, path):
        """Entry already registered from the same file, or None."""
        target = Path(path).resolve()
        return next((e for e in self if Path(e.load.path).resolve() == target), None)

    def remove(self, spark, alias):
        """Takes the table out of the catalog with its relations, drops its views and frees its cache
        (not the cache of an adopted dataset: the session still uses it)."""
        entry = self.get(alias)
        del self._entries[entry.alias]
        self.relations.drop_table(entry.alias)
        if entry.shared:
            spark.catalog.dropTempView(entry.alias)
        else:
            _release(spark, entry.alias, entry.load, entry.etl)


def _release(spark, alias, load, report):
    for view in etl_views(alias):
        spark.catalog.dropTempView(view)
    for df in (getattr(report, "df", None), getattr(load, "df", None)):
        if df is not None:
            df.unpersist()
