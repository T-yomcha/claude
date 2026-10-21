"""
Задания варианта 12 и проверка результатов.

Массивы хранятся в ОЗУ данных так: первый элемент — количество
элементов N, затем N элементов (целые числа без знака, 16 бит).

    Задача 1: ARR — массив, MAX — результат (максимум).
    Задача 2: A, B — массивы, S2:S1:S0 — результат свёртки
              S = A[1]·B[1] + … + A[N]·B[N] (48 бит, S0 — младшее слово).
"""
from __future__ import annotations


def read_array(mem, base: int) -> list[int]:
    n = mem[base]
    return list(mem[base + 1: base + 1 + n])


def write_array(data: list[int], base: int, values) -> None:
    """Записать массив (длина + элементы) в образ ОЗУ данных."""
    values = list(values)
    data[base] = len(values)
    data[base + 1: base + 1 + len(values)] = values


def task_kind(program) -> str | None:
    names = {name for name, s in program.symbols.items() if s.section == "data"}
    if {"ARR", "MAX"} <= names:
        return "max"
    if {"A", "B", "S0", "S1", "S2"} <= names:
        return "conv"
    return None


def expected_max(values) -> int:
    return max(values) if values else 0


def expected_convolution(a, b) -> int:
    return sum(x * y for x, y in zip(a, b))


def convolution_result(program, mem) -> int:
    s0, s1, s2 = (mem[program.data_addr(n)] for n in ("S0", "S1", "S2"))
    return s0 | (s1 << 16) | (s2 << 32)


def result_lines(program, mem, finished: bool) -> list[str]:
    """Текст для панели «Результат»."""
    kind = task_kind(program) if program else None
    if kind == "max":
        values = read_array(mem, program.data_addr("ARR"))
        got = mem[program.data_addr("MAX")]
        lines = [f"Задача 1: поиск максимума",
                 f"ARR (N={len(values)}): {values}",
                 f"MAX = {got}"]
        expected = expected_max(values)
    elif kind == "conv":
        a_base, b_base = program.data_addr("A"), program.data_addr("B")
        n = mem[a_base]
        a = read_array(mem, a_base)
        b = list(mem[b_base + 1: b_base + 1 + n])
        got = convolution_result(program, mem)
        lines = [f"Задача 2: свёртка, N = {n}",
                 f"A: {a}",
                 f"B: {b}",
                 f"S = S2·2^32 + S1·2^16 + S0 = {got}",
                 f"  (S2={mem[program.data_addr('S2')]}, "
                 f"S1={mem[program.data_addr('S1')]}, "
                 f"S0={mem[program.data_addr('S0')]})"]
        expected = expected_convolution(a, b)
    else:
        return ["Для этой программы автоматическая проверка не задана.",
                "Значения переменных смотрите в ОЗУ данных."]

    if finished:
        verdict = "ВЕРНО" if got == expected else "НЕВЕРНО"
        lines.append(f"Проверка (вычислено на Python): {expected} — {verdict}")
    else:
        lines.append("Программа ещё выполняется…")
    return lines
