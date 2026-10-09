"""Datasets of the workshop: register several (start or later), see their schema, choose the active
one and where questions are answered. Everything goes through app/catalog.py (no parallel registry)."""
import config
from app.errors import AppError
from app.spark.loader import CsvOptions, detect_format, resolve_path, sniff_csv
from app.ui import etl as etl_ui
from app.ui import prompts, render
from app.ui import relations as relations_ui

FORMATS = ("csv", "json", "parquet")
SCOPE_NAMES = {"automatico": "AUTOMATICO (cada pregunta busca sus datasets)",
               "dataset": "SOLO EL DATASET ACTIVO", "catalogo": "CATALOGO (todos los datasets, con JOINs confirmados)"}


# --- reading options (shared with the start of the application) --------------------------------

def ask_format():
    print("\nNo se pudo determinar el formato del archivo.")
    while True:
        choice = prompts.ask("Elija el formato: 1) CSV  2) JSON  3) Parquet: ").strip()
        if choice in ("1", "2", "3"):
            return FORMATS[int(choice) - 1]
        print("Opcion no valida.")


def confirm_csv(opts):
    print(f"\nCSV detectado -> separador: {render.SEP_NAMES.get(opts.sep, repr(opts.sep))} | "
          f"encabezado: {'si' if opts.header else 'no'} | codificacion: {opts.encoding}")
    if prompts.ask("Es correcto? [S/n]: ").strip().lower() not in ("n", "no"):
        return opts
    sep = prompts.ask(f"Separador (Enter = {opts.sep!r}; escriba 'tab' para tabulador): ")
    sep = "\t" if sep.strip().lower() == "tab" else (sep or opts.sep)
    header = prompts.ask("La primera fila es encabezado? [S/n]: ").strip().lower() not in ("n", "no")
    encoding = prompts.ask(f"Codificacion (Enter = {opts.encoding}; p. ej. UTF-8, ISO-8859-1): ").strip() or opts.encoding
    return CsvOptions(sep=sep, header=header, encoding=encoding)


# --- registering ---------------------------------------------------------------------------------

def ask_count():
    """'Cuantos datasets desea agregar?' -> a positive integer, or None (Enter / 0 = cancel)."""
    while True:
        raw = prompts.ask("\nCuantos datasets desea agregar? (Enter = cancelar): ").strip()
        if raw in ("", "0") or raw.lower() in prompts.COMMANDS_BACK:
            return None
        if raw.isdigit() and int(raw) > 0:
            return int(raw)
        print("Escriba un numero entero positivo (1, 2, 3...).")


def register_many(spark, catalog, count=None):
    """Registers `count` datasets (asked when None). Stops when the user cancels; the ones already
    registered stay. Returns the new entries."""
    count = count or ask_count()
    added = []
    for n in range(1, (count or 0) + 1):
        entry = register_one(spark, catalog, f"{n} de {count}")
        if entry is None:
            print("Registro cancelado: se continua con los datasets ya registrados.")
            break
        added.append(entry)
    return added


def register_one(spark, catalog, label=""):
    """One dataset: path, format, reading options, alias and ETL. None when the user cancels."""
    while True:
        raw = prompts.ask(f"\nRuta del dataset {label} (Enter = terminar): ").strip()
        if not raw or raw.lower() in prompts.COMMANDS_BACK:
            return None
        try:
            path = resolve_path(raw)
        except AppError as exc:
            render.error(exc)
            continue
        same = catalog.find_path(path)
        if same is not None:
            print(f"Atencion: ese archivo ya esta registrado como '{same.alias}'.")
            if not prompts.confirm("Registrarlo otra vez con otro alias?"):
                continue
        fmt = detect_format(path) or ask_format()
        options = confirm_csv(sniff_csv(path)) if fmt == "csv" else None
        alias = _ask_alias(spark, catalog, path)
        if alias is None:
            return None
        clean = prompts.confirm_yes("Ejecutar el ETL (limpieza y reglas) sobre este dataset?")
        print("Cargando dataset...")
        try:
            entry = catalog.register(spark, alias, path, fmt, options,
                                     etl=(lambda load, view: etl_ui.run(spark, load, view)) if clean else None)
        except AppError as exc:
            render.error(exc)
            continue
        print(f"Dataset '{entry.alias}' registrado: {entry.profile.rows:,} registros, ".replace(",", ".")
              + f"columnas: {', '.join(c.name for c in entry.profile.columns)}")
        return entry


def _ask_alias(spark, catalog, path):
    """A valid, free alias; asked again until it is (Enter on the suggestion accepts it)."""
    suggested = catalog.suggest_alias(path)
    while True:
        raw = prompts.ask(f"Alias del dataset (Enter = {suggested}; 'cancelar' para no registrarlo): ").strip()
        if raw.lower() in ("cancelar",) + prompts.COMMANDS_BACK:
            return None
        try:
            return catalog.check_alias(spark, raw or suggested)
        except AppError as exc:
            render.error(exc)


def adopt_active(session):
    """Offers the active dataset to the catalog (explicit decision), so questions can use it too."""
    if session.active_alias in session.catalog:
        return True
    suggested = session.catalog.suggest_alias(session.load.path)
    print(f"\nEl dataset activo ({session.load.path.name}) no esta en el catalogo: la seleccion automatica no lo usa.")
    if not prompts.confirm_yes(f"Incorporarlo al catalogo con el alias '{suggested}' (sin volver a cargarlo)?"):
        return False
    try:
        entry = session.catalog.adopt(session.spark, suggested, session.load, session.profile, session.semantic,
                                      session.etl)
    except AppError as exc:
        render.error(exc)
        return False
    session.active_alias = entry.alias
    return True


# --- the manager (workshop menu) ----------------------------------------------------------------

OPTIONS = [
    ("1", "Ver datasets registrados y ambito actual"),
    ("2", "Agregar dataset(s)"),
    ("3", "Ver esquema de un dataset"),
    ("4", "Cambiar el dataset activo"),
    ("5", "Cambiar el ambito de las preguntas (automatico / dataset activo / catalogo)"),
    ("6", "Relaciones entre datasets"),
    ("0", "Volver"),
]


def run(session):
    while True:
        render.banner("DATASETS DEL TALLER")
        status(session)
        print()
        for key, text in OPTIONS:
            print(f"  {key}. {text}")
        choice = prompts.ask("\nSeleccione una opcion: ").strip()
        if choice == "0":
            return
        if choice == "1":
            show(session)
        elif choice == "2":
            adopt_active(session)
            register_many(session.spark, session.catalog)
            status(session)
        elif choice == "3":
            entry = _pick_entry(session, "Dataset cuyo esquema desea ver:")
            if entry is not None:
                render.schema(entry.profile)
        elif choice == "4":
            entry = _pick_entry(session, "Nuevo dataset activo (vista 'dataset'):")
            if entry is not None:
                session.activate(entry.alias)
                print(f"Dataset activo: {entry.alias}")
        elif choice == "5":
            choose_scope(session)
        elif choice == "6":
            relations_ui.run(session)
            continue
        else:
            print("Opcion no valida.")
            continue
        prompts.ask("\n(Enter para continuar)")


def status(session):
    names = ", ".join(e.alias + (" (activo)" if e.alias == session.active_alias else "") for e in session.catalog)
    print(f"Dataset activo: {session.active_alias or session.load.path.name} (vista '{config.VIEW_NAME}')")
    print(f"Catalogo: {names or 'vacio'} | Relaciones confirmadas: {len(session.relations.confirmed())}")
    print(f"Ambito de las preguntas: {SCOPE_NAMES[session.scope]}"
          + (" -> ahora usa el catalogo" if session.scope == "automatico" and session.uses_catalog else
             " -> ahora usa el dataset activo" if session.scope == "automatico" else ""))


def show(session):
    if not len(session.catalog):
        print("\nNo hay datasets en el catalogo: las preguntas usan el dataset activo.")
        return
    rows = [[e.alias, e.load.path.name, e.profile.rows, len(e.profile.columns), "si" if e.etl else "no",
             ", ".join(c.name for c in e.profile.columns)] for e in session.catalog]
    render.table(["alias", "archivo", "registros", "cols", "ETL", "columnas"], rows, width=60)
    if len(session.relations):
        relations_ui.show(session.relations)


def choose_scope(session):
    options = [(SCOPE_NAMES["automatico"], "automatico"), (SCOPE_NAMES["dataset"], "dataset"),
               (SCOPE_NAMES["catalogo"], "catalogo")]
    scope = prompts.pick("Donde se responden las preguntas?", options)
    if scope is None:
        return
    if scope != "dataset" and not len(session.catalog):
        print("No hay datasets en el catalogo: agregue datasets primero (opcion 2).")
        return
    if scope == "automatico":
        adopt_active(session)
    session.scope = scope
    status(session)


def _pick_entry(session, message):
    if not len(session.catalog):
        print("\nNo hay datasets en el catalogo.")
        return None
    return prompts.pick(message, [(f"{e.alias} ({e.load.path.name})", e) for e in session.catalog])
