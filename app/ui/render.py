"""Screen output. Messages in Spanish without accents (safe in any console)."""
import datetime

import config
from app.questions.numbers import format_number

LINE = "=" * 52
SEP_NAMES = {",": "coma (,)", ";": "punto y coma (;)", "\t": "tabulador", "|": "barra (|)"}


def banner(title=None):
    print(f"\n{LINE}\n{'PYSPARK LAB ANALYZER':^52}")
    if title:
        print(f"{title:^52}")
    print(LINE)


def cell(value, width=None):
    if value is None:
        text = "NULL"
    elif isinstance(value, float):
        text = format_number(value)
    elif isinstance(value, (datetime.date, datetime.datetime)):
        text = value.isoformat(sep=" ") if isinstance(value, datetime.datetime) else value.isoformat()
    else:
        text = str(value)
    if width and len(text) > width:
        text = text[: width - 2] + ".."
    return text


def table(columns, rows, max_rows=None, width=24):
    max_rows = max_rows or config.MAX_DISPLAY_ROWS
    shown = [[cell(v, width) for v in r] for r in rows[:max_rows]]
    widths = [max([len(str(c))] + [len(r[i]) for r in shown]) for i, c in enumerate(columns)]
    print("  " + " | ".join(str(c).ljust(w) for c, w in zip(columns, widths)))
    print("  " + "-+-".join("-" * w for w in widths))
    for r in shown:
        print("  " + " | ".join(v.ljust(w) for v, w in zip(r, widths)))
    if len(rows) > max_rows:
        print(f"  ... {len(rows) - max_rows} filas mas")


def load_summary(load, profile):
    banner("RESUMEN DEL DATASET")
    fmt = load.fmt.upper()
    if load.csv_options:
        o = load.csv_options
        fmt += f" (separador {SEP_NAMES.get(o.sep, repr(o.sep))}, encabezado {'si' if o.header else 'no'}, {o.encoding})"
    print(f"Archivo      : {load.path.name}")
    print(f"Formato      : {fmt}")
    print(f"Registros    : {load.rows:,}".replace(",", "."))
    print(f"Columnas     : {len(load.columns)}")
    print(f"Vista SQL    : {config.VIEW_NAME}")
    print(f"Carga        : {load.seconds:.1f} s" + (f" / perfil {profile.seconds:.1f} s" if profile else ""))
    if load.renamed:
        print("Columnas renombradas para poder usarlas en SQL:")
        for original, new in load.renamed:
            print(f"  {original!r} -> {new}")
    for warning in load.warnings:
        print(f"AVISO: {warning}")


def schema(profile):
    print("\nEsquema:")
    table(["#", "columna", "tipo spark", "clase"],
          [[i, c.name, c.dtype, _kind_label(c)] for i, c in enumerate(profile.columns, 1)], max_rows=500)


def profile_detail(profile):
    print("\nPerfil por columna (distintos = aproximado):")
    rows = []
    for c in profile.columns:
        rows.append([c.name, c.dtype, _kind_label(c), c.nulls, f"{c.null_pct:.1f}%",
                     c.approx_distinct, c.min, c.max, c.mean])
    table(["columna", "tipo", "clase", "nulos", "% nulos", "distintos", "min", "max", "promedio"], rows, max_rows=500)
    print("\nColumnas por clase:")
    print(f"  Numericas      : {', '.join(c.name for c in profile.of_kind('numeric')) or '-'}")
    print(f"  Texto          : {', '.join(c.name for c in profile.of_kind('text') if not c.is_date) or '-'}")
    print(f"  Fecha/tiempo   : {', '.join(c.name for c in profile.date_columns) or '-'}")
    print(f"  Identificadores: {', '.join(c.name for c in profile.columns if c.is_id) or '-'}")
    print(f"  Categoricas    : {', '.join(c.name for c in profile.categorical) or '-'}")
    for c in profile.categorical:
        values = ", ".join(map(str, c.values[:10])) + (" ..." if len(c.values) > 10 else "")
        print(f"    {c.name}: {values}")
    if profile.samples:
        print(f"\nEjemplos ({len(profile.samples)} filas):")
        cols = [c.name for c in profile.columns]
        table(cols, [[s[c] for c in cols] for s in profile.samples], width=14)


def semantic_roles(semantic):
    print("\nRoles semanticos detectados (ayuda, no suposicion):")
    for role in config.SEMANTIC_ROLES:
        status = semantic.status(role)
        if status == "missing":
            continue
        cands = semantic.tied(role)
        kind = config.ROLE_CLASS.get(role, "")
        if status == "ok":
            print(f"  {role:<13} -> {cands[0].column}  ({kind}; {cands[0].reason})")
        else:
            print(f"  {role:<13} -> AMBIGUO: {', '.join(c.column for c in cands)} (se preguntara al usarlo)")


def evidence(ev, compact=False):
    print()
    title = f"PREGUNTA {ev.number}" if ev.number is not None else "PREGUNTA"
    print(f"--- {title} " + "-" * max(0, 44 - len(title)))
    print(ev.question)
    print(f"\nINTERPRETACION : {ev.interpretation}")
    if not compact and ev.columns_used:
        print(f"COLUMNAS       : {', '.join(ev.columns_used)}")
    print("\nCONSULTA SPARK SQL:")
    print("  " + ev.sql.replace("\n", "\n  "))
    if ev.result_types:
        print("\nTIPOS          : " + ", ".join(f"{c} ({t})" for c, t in zip(ev.result_columns, ev.result_types)))
    if ev.result_rows and (not compact or len(ev.result_rows) > 1 or len(ev.result_columns) > 2
                           or ev.intent == "COLUMN_SUMMARY"):
        print("\nRESULTADO:")
        table(ev.result_columns, ev.result_rows, max_rows=10 if compact else None, width=16)
    print(f"\nRESULTADO      : {ev.result_text}")
    if ev.question_type == "TRUE_FALSE":
        print(f"AFIRMACION     : {ev.claim}")
        print(f"VALOR CALCULADO: {ev.correct_value}")
    for w in ev.warnings:
        print(f"AVISO          : {w}")
    print(f"\nRESPUESTA      : {ev.answer}" + (f"   (valor correcto: {ev.correct_value})" if ev.verdict == "FALSO" else ""))
    print(f"({ev.seconds:.2f} s, {ev.timestamp})")


def error(exc):
    print(f"\nERROR: {exc.message}")
    if exc.hint:
        print(f"  Sugerencia: {exc.hint}")


def progress(i, total, label):
    print(f"[{i}/{total}] {label}...", flush=True)


def step_result(result):
    """Short view of one full-analysis step (the full evidence stays in the history)."""
    if result.error:
        print(f"    ERROR: {result.error.message}" + (f" ({result.error.hint})" if result.error.hint else ""))
        return
    ev = result.evidence
    if ev.result_rows and (len(ev.result_rows) > 1 or len(ev.result_columns) >= 2):
        table(ev.result_columns, ev.result_rows, max_rows=12, width=16)
    else:
        print(f"    {ev.result_text}")
    for w in ev.warnings:
        print(f"    AVISO: {w}")


def not_understood(need):
    print(f"\n{need.message}")
    if need.missing:
        print("Para resolverla el sistema necesita:")
        for item in need.missing:
            print(f"  - {item}")
    print("Sugerencia: mencione la operacion (promedio, suma, maximo, minimo, conteo) y la columna.")


def history(items):
    if not items:
        print("\nTodavia no hay preguntas resueltas.")
        return
    print("\nHISTORIAL DE LA SESION:")
    table(["#", "pregunta", "respuesta", "intencion"],
          [[e.number, e.question.splitlines()[0], e.answer, e.intent] for e in items], max_rows=500, width=48)


def _kind_label(c):
    if c.is_id:
        return "identificador"
    if c.is_categorical:
        return "categorica"
    if c.date_format:
        return f"fecha (texto {c.date_format})"
    return {"numeric": "numerica", "text": "texto", "date": "fecha", "boolean": "booleana"}.get(c.kind, "otra")


# --- workshop -------------------------------------------------------------------

MARKS = {"CORRECTA": "[OK]", "INCORRECTA": "[X] ", "SIN RESPUESTA": "[--]", "NO DETERMINADA": "[??]",
         "NO RESUELTA": "[!!]"}


def box(title):
    print(f"\n{LINE}\n{title:^52}\n{LINE}")


def etl_report(report):
    box("ETL COMPLETADO")
    print(f"Registros originales:   {report.original_rows:>12,}".replace(",", "."))
    print(f"Registros validos:      {report.valid_rows:>12,}".replace(",", "."))
    print(f"Registros rechazados:   {report.rejected_rows:>12,}".replace(",", "."))
    if report.duplicates_found:
        print(f"Duplicados eliminados:  {report.duplicates_removed:>12,}".replace(",", "."))
    print(f"Columnas:               {len(report.columns):>12}")
    print("\nTransformaciones:")
    for label, done in report.checklist():
        print(f"  {'[OK]' if done else '[--]'} {label}")
    for t in report.transformations:
        if t.kind == "equivalence":
            print(f"       {t.description} ({t.affected} valores)")
    if report.similar_values:
        print("\nPosibles equivalencias NO aplicadas (difieren en espacios internos o signos):")
        for line in report.similar_values[:10]:
            print(f"  - {line}")
        print("  Si son el mismo valor, agregue una regla: equivalencia: columna: valor -> valor")
    for r in report.rule_results:
        print(f"       regla '{r.text}': {r.failed} incumplen" + (f", {r.unknown} sin evaluar" if r.unknown else ""))
    for column, count in report.invalid_values.items():
        print(f"       {count} valor(es) no validos en {column}")
    if report.rejected_rows:
        print(f"\nLos rechazados se pueden consultar con SQL: SELECT * FROM {report.views[2]}")
    print("\nDataset listo para consultas.")
    print(LINE)


def workshop_result(ev):
    from app.export import TYPE_NAMES
    from app.questions.validator import claim_text
    if ev.validation == "NO RESUELTA":
        box("ERROR")
        print("No fue posible resolver la pregunta.\n")
        print(f"Motivo:\n  {ev.validation_note}")
        if ev.warnings:
            print("\nPara resolverla el sistema necesita:")
            for w in ev.warnings:
                print(f"  - {w.removeprefix('Falta: ')}")
        print("\nSugerencia: mencione la operacion (promedio, suma, maximo, conteo...) y la columna,\n"
              "o use 'Ejecutar SQL manual'. La pregunta queda registrada como NO RESUELTA.")
        print(LINE)
        return
    box("RESULTADO")
    print(f"Pregunta {ev.number} ({TYPE_NAMES.get(ev.question_type, ev.question_type)}):")
    print("  " + ev.question.replace("\n", "\n  "))
    if ev.result_rows and (len(ev.result_rows) > 1 or len(ev.result_columns) > 2):
        print("\nTabla de resultados:")
        table(ev.result_columns, ev.result_rows, max_rows=10, width=18)
    print(f"\nResultado calculado:\n  {ev.result_text}")
    if ev.question_type == "TRUE_FALSE":
        print(f"\nAfirmacion: {claim_text(ev)}  ->  {ev.verdict or 'NO DETERMINADO'}")
    if ev.options:
        print("\nOpciones:")
        for letter, text in ev.options:
            print(f"  {letter}) {text}")
    print(f"\nRespuesta correcta:\n  {ev.correct_answer or '-'}")
    print(f"\nRespuesta seleccionada:\n  {ev.selected_answer or '(no indicada)'}")
    if ev.material_option:
        print(f"\nOpcion marcada en el material (no se usa para validar):\n  {ev.material_option}")
    for c in ev.clarifications:
        print(f"Aclaracion: {c}")
    print(f"\n{LINE}\n{'VALIDACION: ' + ev.validation:^52}\n{LINE}")
    if ev.validation_note:
        print(ev.validation_note)
    for w in ev.warnings:
        print(f"AVISO: {w}")
    print("\nSQL:")
    print("  " + ev.sql.replace("\n", "\n  "))
    print(LINE)


def question_list(questions):
    if not questions:
        print("\nTodavia no hay preguntas resueltas.")
        return
    print("\nPREGUNTAS DEL TALLER:")
    for e in questions:
        first = e.question.splitlines()[0][:70]
        print(f"\n  {e.number}. {first}")
        print(f"     {MARKS.get(e.validation, '[  ]')} {e.validation or 'sin validar'}   resultado: {e.result_text[:60]}")
