"""Lab mode: type a question (with options if any), get the answer at once, repeat."""
from app.errors import AppError
from app.export import export_history
from app.questions.intents import NeedsInput
from app.ui import prompts, render

HELP = ("Escriba la pregunta (puede pegar tambien las opciones A-D) y termine con una linea vacia.\n"
        "Comandos: :h historial  :x exportar evidencia  :q volver al menu")


def ask_one(session, text, compact):
    """Solves one question and prints it. Shared by 'Hacer pregunta' and lab mode."""
    try:
        outcome = session.ask(text, prompts.choose)
    except AppError as exc:
        print(f"\nERROR: {exc.message}")
        if exc.hint:
            print(f"  Sugerencia: {exc.hint}")
        return
    if outcome is None:
        print("Pregunta cancelada.")
    elif isinstance(outcome, NeedsInput):
        render.not_understood(outcome)
    else:
        render.evidence(outcome, compact=compact)


def run(session):
    render.banner("MODO LABORATORIO")
    print(HELP)
    while True:
        text = prompts.read_block("\n> ", "  ")
        command = text.strip().lower()
        if not command:
            continue
        if command in (":q", "salir", ":salir"):
            return
        if command == ":h":
            render.history(session.history)
        elif command == ":x":
            export(session)
        else:
            ask_one(session, text, compact=True)


def export(session):
    if not session.history:
        print("\nNo hay evidencia para exportar todavia.")
        return
    try:
        json_path, csv_path = export_history(session.history)
    except OSError as exc:
        print(f"\nERROR: no se pudo exportar: {exc}")
        return
    print(f"\nEvidencia exportada ({len(session.history)} preguntas):\n  {json_path}\n  {csv_path}")
