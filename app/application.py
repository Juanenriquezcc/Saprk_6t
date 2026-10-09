"""Application flow: dataset -> ETL -> profile -> workshop menu."""
import config
from app.errors import AppError
from app.session import LabSession
from app.spark.loader import detect_format, load_dataset, resolve_path, sniff_csv
from app.spark.session import get_spark, stop_spark
from app.catalog import DatasetCatalog
from app.ui import datasets as datasets_ui
from app.ui import etl as etl_ui
from app.ui import lab_mode, render, workshop
from app.ui.prompts import ask

EXIT_WORDS = {"salir", "exit", "q"}
MANY_WORDS = {"varios", "multiples", "multiple", "+"}      # several datasets in the same workshop


def show_error(exc):
    print(f"\nERROR: {exc.message}")
    if exc.hint:
        print(f"  Sugerencia: {exc.hint}")


class Application:
    def __init__(self, debug=False):
        self.debug = debug

    def run(self, dataset=None, lab=False):
        render.banner()
        try:
            load = self._load(dataset)
            if load is None:
                return 0
            if load == "varios":
                session = self._start_many()
                if session is None:
                    return 0
            else:
                render.load_summary(load, None)
                report = etl_ui.run(self.spark, load)
                print("Analizando columnas...")
                session = LabSession.start(self.spark, load, report)
            if lab:
                lab_mode.run(session)
            workshop.run(session)
            return 0
        except KeyboardInterrupt:
            print("\nSaliendo.")
            return 0
        finally:
            stop_spark()

    # --- loading -----------------------------------------------------------------

    def _load(self, dataset):
        pending = dataset
        while True:
            raw = pending if pending is not None else self._ask_dataset()
            pending = None
            if raw.strip().lower() in EXIT_WORDS:
                return None
            if raw.strip().lower() in MANY_WORDS:
                return "varios"
            try:
                path = resolve_path(raw)
                fmt = detect_format(path) or self._ask_format()
                csv_options = self._confirm_csv(sniff_csv(path)) if fmt == "csv" else None
            except AppError as exc:
                show_error(exc)
                continue

            print("Iniciando Spark...")
            # Without Spark nothing can be done: an AppError here reaches main.py.
            self.spark = get_spark(quiet=not self.debug)
            print("Cargando dataset...")
            try:
                return load_dataset(self.spark, path, fmt, csv_options)
            except AppError as exc:
                show_error(exc)

    def _ask_dataset(self):
        """Path typed (or dragged) by the user, or the number of a file in datasets\\."""
        found = []
        if config.DATASETS_DIR.is_dir():
            found = sorted(f for f in config.DATASETS_DIR.iterdir()
                           if f.is_file() and f.suffix.lower() in config.FORMATS_BY_EXTENSION)[:30]
        if found:
            print(f"\nDatasets en la carpeta {config.DATASETS_DIR.name}:")
            for i, f in enumerate(found, 1):
                print(f"  {i}. {f.name}")
        print("\nUn dataset: escriba su ruta. Varios datasets en el mismo taller: escriba 'varios'.")
        raw = ask("Ingrese la ruta del dataset" + (" o su numero" if found else "") + " (o 'varios' / 'salir'): ")
        if raw.strip().isdigit() and 1 <= int(raw.strip()) <= len(found):
            return str(found[int(raw.strip()) - 1])
        return raw

    def _start_many(self):
        """Several datasets: each one registered in the catalog; the first is the active one."""
        print("Iniciando Spark...")
        self.spark = get_spark(quiet=not self.debug)
        catalog = DatasetCatalog()
        if not datasets_ui.register_many(self.spark, catalog):
            print("No se registro ningun dataset.")
            return None
        first = next(iter(catalog))
        print(f"\nDatasets del taller: {', '.join(e.alias for e in catalog)}. Dataset activo: {first.alias}.")
        print("Las preguntas buscan automaticamente sus datasets (menu 10 para cambiarlo).")
        return LabSession.from_catalog(self.spark, catalog, first.alias)

    def _ask_format(self):
        return datasets_ui.ask_format()

    def _confirm_csv(self, opts):
        return datasets_ui.confirm_csv(opts)
