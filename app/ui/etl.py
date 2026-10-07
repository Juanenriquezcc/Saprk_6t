"""ETL step of the terminal: lab rules (optional), decisions the data cannot settle, report."""
from app.errors import AppError
from app.etl import pipeline
from app.etl.rules import find_rules_file, parse_rules, read_rules_file
from app.ui import prompts, render

RULES_HELP = """
Reglas de calidad del taller (opcional). Escriba una por linea y termine con una linea vacia.
Si el taller no indica reglas, pulse Enter.
  Ejemplos:  quantity entre 1 y 20          customer_age >= 18 AND customer_age <= 100
             returned_qty <= quantity       año(order_date) = 2025
             requerido: city, product       derivada: ingresos = quantity * unit_price"""


def run(spark, load):
    """Asks for the rules, runs the ETL and prints the report. Returns EtlReport."""
    rules, source = _initial_rules(load)
    answered = {}        # a retry after a rule error does not ask the same decisions again

    def remembered(decision):
        if decision.key not in answered:
            answered[decision.key] = decide(decision)
        return answered[decision.key]

    while True:
        print("\nEjecutando ETL (limpieza y validacion)...")
        try:
            report = pipeline.run(spark, load, rules, remembered, source)
        except AppError as exc:
            render.error(exc)
            print("\nCorrija las reglas (o pulse Enter para continuar sin reglas).")
            rules, source = _typed_rules(load)
            continue
        render.etl_report(report)
        return report


def decide(decision):
    """A decision of the ETL: shown with its details, always chosen by the user."""
    print()
    for line in decision.details:
        print(f"  - {line}")
    return prompts.pick(decision.message, decision.options, allow_cancel=False)


def _initial_rules(load):
    path = find_rules_file(load.path)
    if path is not None:
        try:
            rules = read_rules_file(path)
        except AppError as exc:
            render.error(exc)
            rules = []
        if rules:
            print(f"\nSe encontro el archivo de reglas {path.name}:")
            for r in rules:
                print(f"  - {r.text}")
            if prompts.confirm_yes("Usar estas reglas?"):
                return rules, path.name
    return _typed_rules(load)


def _typed_rules(load):
    print(RULES_HELP)
    print("  Columnas: " + ", ".join(load.columns))
    while True:
        text = prompts.read_block("reglas> ", "reglas> ")
        if not text.strip():
            return [], None
        try:
            return parse_rules(text), "escritas por el usuario"
        except AppError as exc:
            render.error(exc)
