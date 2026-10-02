"""Validated terminal input. Messages in Spanish without accents."""

COMMANDS_BACK = (":q", "salir", ":salir")


def ask(prompt):
    try:
        return input(prompt)
    except EOFError:   # closed input: leave cleanly
        raise KeyboardInterrupt from None


def read_block(first_prompt, more_prompt):
    """Reads a question plus optional option lines; an empty line ends the block."""
    first = ask(first_prompt)
    if not first.strip():
        return ""
    if first.strip().startswith(":"):   # lab commands (:q :h :x) act at once, no extra Enter
        return first
    lines = [first]
    # A pasted block arrives line by line: keep reading until an empty line.
    while True:
        line = ask(more_prompt)
        if not line.strip():
            break
        lines.append(line)
    return "\n".join(lines)


def read_sql(first_prompt, more_prompt):
    """Reads SQL until a line ending with ';' or an empty line."""
    first = ask(first_prompt)
    if not first.strip() or first.strip().lower() in COMMANDS_BACK or first.rstrip().endswith(";"):
        return first
    lines = [first]
    while True:
        line = ask(more_prompt)
        if not line.strip():
            break
        lines.append(line)
        if line.rstrip().endswith(";"):
            break
    return "\n".join(lines)


def pick(message, options, allow_cancel=True):
    """Numbered choice among [(label, value)]; returns the value (None = cancel)."""
    print(f"\n{message}")
    for i, (label, _) in enumerate(options, 1):
        print(f"  {i}. {label}")
    hint = " (Enter = cancelar)" if allow_cancel else ""
    while True:
        answer = ask(f"Seleccione 1-{len(options)}{hint}: ").strip()
        if not answer and allow_cancel:
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1][1]
        print("Opcion no valida.")


def choose(need):
    """Shows NeedsInput options and returns the chosen value (None = cancel)."""
    return pick(need.message, need.options)


def ask_int(prompt, default, low=1, high=100):
    while True:
        answer = ask(f"{prompt} [{default}]: ").strip()
        if not answer:
            return default
        if answer.isdigit() and low <= int(answer) <= high:
            return int(answer)
        print(f"Escriba un numero entre {low} y {high}.")


def confirm(prompt):
    return ask(f"{prompt} [s/N]: ").strip().lower() in ("s", "si", "y", "yes")
