"""ETL step of the terminal: lab rules (optional), decisions the data cannot settle, report."""
import config
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


def run(spark, load, view=config.VIEW_NAME):
    """Asks for the rules, runs the ETL and prints the report. Returns EtlReport."""
    rules, source = _initial_rules(load)
    answered = {}        # a retry after a rule error does not ask the same decisions again

    def remembered(decision):
        if decision.key not in answered:
            answered[decision.key] = decide(decision)
        return answered[decision.key]

    while True:
        rules = _with_sale_value(load, rules, answered)
        print("\nEjecutando ETL (limpieza y validacion)...")
        try:
            report = pipeline.run(spark, load, rules, remembered, source, view=view)
        except AppError as exc:
            render.error(exc)
            print("\nCorrija las reglas (o pulse Enter para continuar sin reglas).")
            rules, source = _typed_rules(load)
            continue
        render.etl_report(report)
        return report


CURRENCY_SUFFIXES = {"cop", "usd", "eur", "mxn", "clp", "pen", "ars", "brl"}


def sale_value_rule(columns):
    """'derivada: sales_cop = round(quantity * unit_price_cop, 0)' when the columns have one clear
    quantity and one clear unit price and no amount of the sale yet; None otherwise (names only)."""
    from app.schema.profiler import ColumnProfile, DatasetProfile
    from app.schema.semantic import detect_roles
    from app.text import name_tokens
    # Text columns too: '1.250' written as text becomes a number in the ETL, before derived columns.
    profile = DatasetProfile(rows=0, samples=[], columns=[
        ColumnProfile(name, dtype, "numeric" if dtype == "string" or dtype.startswith(
            ("tinyint", "smallint", "int", "bigint", "float", "double", "decimal")) else "other")
        for name, dtype in columns])
    semantic = detect_roles(profile)
    quantity, price = semantic.resolved("QUANTITY"), semantic.resolved("PRICE")
    if not quantity or not price or semantic.of("AMOUNT") or semantic.of("TOTAL"):
        return None
    suffix = name_tokens(price)[-1]
    name = f"sales_{suffix}" if suffix in CURRENCY_SUFFIXES else "sales_value"
    if name in (n for n, _ in columns):
        return None
    return parse_rules(f"derivada: {name} = round({quantity} * {price}, 0)")[0]


def _with_sale_value(load, rules, answered):
    """The value of each sale is a derived column only the workshop can declare: when the data has a
    quantity and a unit price but no amount, the reference formula is OFFERED (never added silently)."""
    if any(r.kind == "derived" for r in rules):
        return rules                       # the workshop already declares its derived columns
    rule = sale_value_rule(load.df.dtypes)
    if rule is None:
        return rules
    if "sale_value" not in answered:
        print(f"\nEl dataset tiene cantidad y precio unitario, pero ninguna columna con el valor de venta "
              f"de cada pedido.\nFormula de referencia del proyecto (sin descuento): {rule.text.split(': ', 1)[1]}")
        print("Si el taller indica otra formula (por ejemplo con descuento), elija no crearla y escribala como regla "
              "'derivada: ...'.")
        answered["sale_value"] = prompts.confirm_yes(f"Crear la columna {rule.name} con esa formula?")
    return rules + [rule] if answered["sale_value"] else rules


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
