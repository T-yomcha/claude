"""
Эмулятор безадресного (стекового) процессора с гарвардской архитектурой.
Вариант 12: задача 1 — поиск максимума в массиве,
            задача 2 — свёртка двух массивов (6 элементов, числа без знака).

Запуск графического интерфейса:
    python main.py                       — откроется пример «Задача 1»
    python main.py programs/task2_convolution.asm

Без графического интерфейса (консоль):
    python main.py --run programs/task1_max.asm      — выполнить и показать результат
    python main.py --listing programs/task1_max.asm  — машинный код (листинг)
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import tasks  # noqa: E402
from assembler import AsmError, assemble, format_listing  # noqa: E402
from cpu import CPUError, StackCPU  # noqa: E402


def _load(path: str):
    with open(path, encoding="utf-8") as f:
        return assemble(f.read())


def console(path: str, listing: bool) -> int:
    try:
        prog = _load(path)
    except (OSError, AsmError) as exc:
        print(f"Ошибка: {exc}")
        return 1
    if listing:
        print(format_listing(prog))
        return 0
    cpu = StackCPU()
    cpu.load(prog.code, prog.data)
    try:
        executed = cpu.run()
    except CPUError as exc:
        print(f"Ошибка выполнения: {exc}")
        return 1
    if not cpu.halted:
        print(f"Выполнено {executed} команд без HALT — возможно, бесконечный цикл.")
        return 1
    print(f"Программа завершена: {cpu.instr_count} команд, {cpu.ticks} тактов")
    print("\n".join(tasks.result_lines(prog, cpu.dmem, True)))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Эмулятор безадресного стекового процессора (вариант 12)")
    parser.add_argument("file", nargs="?", help="файл программы .asm")
    parser.add_argument("--run", action="store_true",
                        help="выполнить программу без графического интерфейса")
    parser.add_argument("--listing", action="store_true",
                        help="вывести листинг (машинный код) без графического интерфейса")
    args = parser.parse_args(argv)

    if args.run or args.listing:
        if not args.file:
            parser.error("укажите файл программы .asm")
        return console(args.file, args.listing)

    try:
        import tkinter  # noqa: F401
    except ImportError:
        print("Модуль tkinter не найден. В Windows переустановите Python с галочкой "
              "«tcl/tk and IDLE»; в Linux: sudo apt install python3-tk")
        return 1
    from gui import run_gui
    run_gui(args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
