"""Application flow: dataset -> ETL -> profile -> workshop menu."""
import config
from app.errors import AppError
from app.session import LabSession
from app.spark.loader import CsvOptions, detect_format, load_dataset, resolve_path, sniff_csv
from app.spark.session import get_spark, stop_spark
from app.ui import etl as etl_ui
from app.ui import lab_mode, render, workshop
from app.ui.prompts import ask

EXIT_WORDS = {"salir", "exit", "q"}
FORMATS = ("csv", "json", "parquet")


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
        raw = ask("\nIngrese la ruta del dataset" + (" o su numero" if found else "") + " (o 'salir'): ")
        if raw.strip().isdigit() and 1 <= int(raw.strip()) <= len(found):
            return str(found[int(raw.strip()) - 1])
        return raw

    def _ask_format(self):
        print("\nNo se pudo determinar el formato del archivo.")
        while True:
            choice = ask("Elija el formato: 1) CSV  2) JSON  3) Parquet: ").strip()
            if choice in ("1", "2", "3"):
                return FORMATS[int(choice) - 1]
            print("Opcion no valida.")

    def _confirm_csv(self, opts):
        print(f"\nCSV detectado -> separador: {render.SEP_NAMES.get(opts.sep, repr(opts.sep))} | "
              f"encabezado: {'si' if opts.header else 'no'} | codificacion: {opts.encoding}")
        if ask("Es correcto? [S/n]: ").strip().lower() not in ("n", "no"):
            return opts
        sep = ask(f"Separador (Enter = {opts.sep!r}; escriba 'tab' para tabulador): ")
        sep = "\t" if sep.strip().lower() == "tab" else (sep or opts.sep)
        header = ask("La primera fila es encabezado? [S/n]: ").strip().lower() not in ("n", "no")
        encoding = ask(f"Codificacion (Enter = {opts.encoding}; p. ej. UTF-8, ISO-8859-1): ").strip() or opts.encoding
        return CsvOptions(sep=sep, header=header, encoding=encoding)
