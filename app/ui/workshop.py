"""Main menu and 'Modo Taller': question -> type -> options -> student's answer -> validation.

Simple outside: the student never needs to know about QuerySpec, roles or SQL. Every
question is kept (also the unresolved ones) and 'Finalizar taller' exports everything.
"""
import re

import config
from app.errors import AppError
from app.export import TYPE_NAMES, etl_text, export_workshop, question_text, summary_text, workshop_summary
from app.query import spec as s
from app.questions.parser import parse_question
from app.questions.validator import read_letter, read_verdict
from app.ui import datasets as datasets_ui
from app.ui import menu, prompts, render, sql_mode

OPTIONS = [
    ("1", "Resolver pregunta (Modo Taller)"),
    ("2", "Ver preguntas resueltas"),
    ("3", "Ver resultado de una pregunta"),
    ("4", "Ver consultas SQL"),
    ("5", "Ver calidad del dataset (ETL)"),
    ("6", "Ver esquema"),
    ("7", "Ejecutar SQL manual"),
    ("8", "Finalizar taller"),
    ("9", "Herramientas avanzadas"),
    ("10", "Datasets del taller (agregar, esquema, dataset activo, ambito)"),
    ("0", "Salir"),
]
KINDS = {"1": s.OPEN, "2": s.MULTIPLE_CHOICE, "3": s.TRUE_FALSE}
LETTERS = "ABCDEFGH"


def run(session):
    exported = 0          # questions already included in an export
    while True:
        header(session)
        for key, label in OPTIONS:
            print(f"  {key}. {label}")
        choice = prompts.ask("\nSeleccione una opcion: ").strip()
        if choice == "0":
            pending = len(session.questions) - exported
            if pending > 0 and prompts.confirm_yes(f"\nHay {pending} pregunta(s) sin exportar. Generar la evidencia antes de salir?"):
                finish(session, ask_first=False)
            return
        if choice == "1":
            question_loop(session)
            continue
        if choice == "2":
            render.question_list(session.questions)
        elif choice == "3":
            show_question(session)
        elif choice == "4":
            show_sql(session)
        elif choice == "5":
            show_quality(session)
        elif choice == "6":
            render.schema(session.profile)
            render.semantic_roles(session.semantic)
        elif choice == "7":
            sql_mode.run(session)
            continue
        elif choice == "8":
            if finish(session):
                exported = len(session.questions)
        elif choice == "9":
            menu.run(session)
            continue
        elif choice == "10":
            datasets_ui.run(session)
            continue
        else:
            print("Opcion no valida. Escriba un numero del menu.")
            continue
        prompts.ask("\n(Enter para volver al menu)")


def header(session):
    render.banner()
    etl = session.etl
    rules = bool(etl and etl.rule_results)
    print(f"Dataset: {session.load.path.name}\n")
    print("Estado:")
    print("  [OK] Dataset cargado")
    print(f"  {'[OK]' if etl else '[--]'} ETL ejecutado")
    print(f"  {'[OK]' if rules else '[--]'} Datos validados" + ("" if rules else " (sin reglas del taller)"))
    print("  [OK] Spark SQL disponible (vista 'dataset')\n")
    if session.uses_catalog:
        print(f"Ambito de las preguntas: {session.scope.upper()} ({', '.join(e.alias for e in session.catalog)}), "
              f"{len(session.relations.confirmed())} relacion(es) confirmada(s)\n")
    original = etl.original_rows if etl else session.load.rows
    print(f"Registros originales: {original:,}".replace(",", "."))
    print(f"Registros validos:    {session.profile.rows:,}".replace(",", "."))
    questions = session.questions
    if questions:
        ok = sum(1 for e in questions if e.validation == "CORRECTA")
        print(f"Preguntas del taller: {len(questions)} ({ok} correctas)")
    print("-" * 52)


# --- one question -------------------------------------------------------------------

def question_loop(session):
    while True:
        if not ask_question(session):
            return
        if prompts.ask("\nEnter = siguiente pregunta | 0 = volver al menu: ").strip() == "0":
            return


def ask_question(session):
    """Returns False when the user goes back to the menu."""
    n = session._next_number(None)
    render.box(f"PREGUNTA {n}")
    print("Escriba o pegue la pregunta (puede incluir las opciones A, B, C, D).\n"
          "Termine con una linea vacia. Escriba 0 para volver al menu.")
    text = prompts.read_block("> ", "  ")
    if text.strip().lower() in ("", "0", ":q", "salir"):
        return False

    parsed = parse_question(text)
    kind = choose_kind(parsed)
    options = list(parsed.options)
    if parsed.options_issue:     # the options cannot be read safely: the user types them again
        print(f"\nNo se pudieron leer las opciones con seguridad ({parsed.options_issue}). Escribalas de nuevo.")
        options = read_options()
        text = parsed.body if parsed.body else text      # the statement is kept; only the options are rebuilt
        parsed.options = []
    if kind == s.MULTIPLE_CHOICE and len(options) < 2:
        options = read_options()
        if len(options) < 2:
            print("Se necesitan al menos dos opciones. La pregunta se tratara como abierta.")
            kind = s.OPEN
    full = text if parsed.options or kind != s.MULTIPLE_CHOICE else \
        text + "\n" + "\n".join(f"{letter}) {value}" for letter, value in options)
    if kind == s.TRUE_FALSE and parsed.kind != s.TRUE_FALSE:
        full += "\nV/F"
    selected = read_student_answer(kind, options)

    print("\nCalculando con Spark...")
    ev = session.solve_question(full, prompts.choose, kind=kind, selected=selected)
    render.workshop_result(ev)
    return True


def choose_kind(parsed):
    detected = parsed.kind
    extra = f" ({len(parsed.options)} opciones)" if parsed.options else ""
    print(f"\nTipo detectado: {TYPE_NAMES[detected].upper()}{extra}")
    print("  Enter = correcto | 1 = Abierta | 2 = Seleccion multiple | 3 = Verdadero/Falso")
    while True:
        answer = prompts.ask("Tipo: ").strip()
        if not answer:
            return detected
        if answer in KINDS:
            return KINDS[answer]
        print("Opcion no valida.")


def read_options():
    print("\nIngrese las opciones, una por linea (ej. 'A) Bogota'). Linea vacia para terminar.")
    options = []
    while len(options) < len(LETTERS):
        line = prompts.ask(f"  {LETTERS[len(options)]}) ").strip()
        if not line:
            break
        written = re.match(r"^\(?[A-Ha-h]\s*[).:\-]\s*(.+)$", line)     # 'B) Pasto' or just 'Pasto'
        options.append((LETTERS[len(options)], written.group(1).strip() if written else line))
    return options


def read_student_answer(kind, options):
    if kind == s.MULTIPLE_CHOICE:
        letters = "/".join(letter for letter, _ in options)
        while True:
            answer = prompts.ask(f"\nRespuesta que marco el estudiante ({letters}; Enter = ninguna): ").strip()
            if not answer or read_letter(answer, options):
                return answer or None
            print("Escriba una de las letras de las opciones.")
    if kind == s.TRUE_FALSE:
        while True:
            answer = prompts.ask("\nRespuesta del estudiante (V/F; Enter = ninguna): ").strip()
            if not answer or read_verdict(answer):
                return answer or None
            print("Escriba V o F.")
    return prompts.ask("\nRespuesta del estudiante (Enter = ninguna): ").strip() or None


# --- views -------------------------------------------------------------------------

def show_question(session):
    questions = session.questions
    if not questions:
        print("\nTodavia no hay preguntas resueltas.")
        return
    render.question_list(questions)
    number = prompts.ask("\nNumero de la pregunta: ").strip()
    ev = next((e for e in questions if str(e.number) == number), None)
    if ev is None:
        print("No existe esa pregunta.")
        return
    print()
    print(question_text(ev))
    if ev.result_rows:
        render.table(ev.result_columns, ev.result_rows, max_rows=20, width=18)


def show_sql(session):
    if not session.history:
        print("\nTodavia no hay consultas.")
        return
    for e in session.history:
        title = f"Pregunta {e.number}" if e.source == "pregunta" else f"[{e.number}] {e.source}"
        print(f"\n-- {title}: {e.question.splitlines()[0][:70]}")
        print(e.sql + ";" if e.sql else "-- (sin consulta: no resuelta)")


def show_quality(session):
    if session.etl is None:
        print("\nEl ETL no se ejecuto en esta sesion.")
        return
    print()
    print(etl_text(session.etl))
    nulls = {c: n for c, n in session.etl.null_counts.items() if n}
    if nulls:
        print("Valores nulos en los datos limpios:")
        for column, count in nulls.items():
            print(f"  {column}: {count}")
    print(f"\nVistas SQL: {config.VIEW_NAME} (limpio), {config.RAW_VIEW_NAME} (original), "
          f"{config.REJECTED_VIEW_NAME} (rechazados, con motivo_rechazo)")


def finish(session, ask_first=True):
    """Summary + export. Returns True when the evidence was written."""
    print()
    print(summary_text(workshop_summary(session)))
    if ask_first and not prompts.confirm_yes("Generar evidencia?"):
        return False
    try:
        folder = export_workshop(session)
    except OSError as exc:
        render.error(AppError(f"No se pudo exportar: {exc}"))
        return False
    print(f"\nEvidencia generada en:\n  {folder}")
    for name in ("taller.sql", "respuestas.csv", "evidencia.json", "evidencia.txt", "resumen.txt", "etl_report.json"):
        print(f"    {name}")
    return True
