"""
Система команд безадресного (стекового) процессора.
Вариант 12: безадресная система команд, гарвардская архитектура.

Все команды имеют одинаковую длину — 16 бит (принцип RISC).

    15 | 14 ................................ 0
     1 |  значение литерала (0 … 32767)            PUSH n (LIT)

    15 | 14 13 12 | 11 10 9 8 | 7 6 5 4 3 2 1 0
     0 |  группа  |  операция | не используются     остальные команды

Операнды берутся из регистрового стека (T0 — вершина, T1 — под ней …),
результат кладётся на вершину стека. Адрес для READ/WRITE и адрес
перехода также берутся с вершины стека, поэтому адресных полей в
командах нет.
"""
from __future__ import annotations

from collections import namedtuple

WORD_BITS = 16
WORD_MASK = 0xFFFF
LIT_BIT = 0x8000          # бит 15 = 1 — команда-литерал
LIT_MAX = 0x7FFF          # литерал занимает 15 бит

GROUPS = {
    0: "управление",
    1: "пересылка, стек",
    2: "арифметика",
    3: "логика, сдвиги",
    4: "переходы",
}

# mnemonic  — мнемоника команды на ассемблере
# group, op — поля «группа» (биты 14..12) и «операция» (биты 11..8)
# pops      — сколько чисел команда снимает со стека
# pushes    — сколько чисел кладёт в стек
# flags     — какие флаги изменяет
# ticks     — сколько тактов занимает этап исполнения
# stack     — стековая нотация Forth: ( до -- после ), справа — вершина
# description — назначение команды
Instr = namedtuple(
    "Instr", "mnemonic group op pops pushes flags ticks stack description")

LIT_INFO = Instr(
    "PUSH", None, None, 0, 1, "—", 1, "( -- n )",
    "Литерал: положить в стек число n (0 … 32767) или адрес метки")

INSTRUCTIONS = [
    Instr("HALT", 0, 0, 0, 0, "—", 1, "( -- )",
          "Останов процессора"),
    Instr("NOP", 0, 1, 0, 0, "—", 1, "( -- )",
          "Нет операции"),

    Instr("READ", 1, 0, 1, 1, "—", 3, "( адр -- знач )",
          "Чтение ОЗУ данных: MAR ← T0, MDR ← ОЗУ[MAR], T0 ← MDR"),
    Instr("WRITE", 1, 1, 2, 0, "—", 3, "( знач адр -- )",
          "Запись в ОЗУ данных: MAR ← T0, MDR ← T1, ОЗУ[MAR] ← MDR"),
    Instr("DUP", 1, 2, 1, 2, "—", 1, "( a -- a a )",
          "Дублировать вершину стека"),
    Instr("DROP", 1, 3, 1, 0, "—", 1, "( a -- )",
          "Удалить вершину стека"),
    Instr("SWAP", 1, 4, 2, 2, "—", 1, "( a b -- b a )",
          "Поменять местами два верхних числа"),
    Instr("OVER", 1, 5, 2, 3, "—", 1, "( a b -- a b a )",
          "Скопировать второе число на вершину"),
    Instr("ROR", 1, 6, 3, 3, "—", 1, "( a b c -- b c a )",
          "Вращение трёх верхних чисел: третье число переходит на вершину"),
    Instr("ROL", 1, 7, 3, 3, "—", 1, "( a b c -- c a b )",
          "Вращение трёх верхних чисел: вершина переходит на место третьего"),

    Instr("ADD", 2, 0, 2, 1, "Z C N", 1, "( a b -- a+b )",
          "Сложение; C — перенос из 15-го разряда"),
    Instr("ADC", 2, 1, 2, 1, "Z C N", 1, "( a b -- a+b+C )",
          "Сложение с переносом (длинная арифметика)"),
    Instr("SUB", 2, 2, 2, 1, "Z C N", 1, "( a b -- a-b )",
          "Вычитание; C — заём (a < b)"),
    Instr("SBB", 2, 3, 2, 1, "Z C N", 1, "( a b -- a-b-C )",
          "Вычитание с заёмом (длинная арифметика)"),
    Instr("INC", 2, 4, 1, 1, "Z C N", 1, "( a -- a+1 )",
          "Инкремент; C — перенос"),
    Instr("DEC", 2, 5, 1, 1, "Z C N", 1, "( a -- a-1 )",
          "Декремент; C — заём"),
    Instr("UMUL", 2, 6, 2, 2, "Z C N", 1, "( a b -- мл ст )",
          "Умножение без знака 16×16 → 32 бита: T0 — старшее слово, "
          "T1 — младшее; C = 1, если старшее слово ≠ 0"),
    Instr("CMP", 2, 7, 2, 0, "Z C N", 1, "( a b -- )",
          "Сравнение: a-b только для флагов, оба числа снимаются; "
          "C = 1, если a < b"),

    Instr("AND", 3, 0, 2, 1, "Z C N", 1, "( a b -- a&b )",
          "Побитовое И; C = 0"),
    Instr("OR", 3, 1, 2, 1, "Z C N", 1, "( a b -- a|b )",
          "Побитовое ИЛИ; C = 0"),
    Instr("XOR", 3, 2, 2, 1, "Z C N", 1, "( a b -- a^b )",
          "Побитовое исключающее ИЛИ; C = 0"),
    Instr("NOT", 3, 3, 1, 1, "Z C N", 1, "( a -- ~a )",
          "Побитовая инверсия; C = 0"),
    Instr("SHL", 3, 4, 1, 1, "Z C N", 1, "( a -- a<<1 )",
          "Логический сдвиг влево; C — выдвинутый 15-й бит"),
    Instr("SHR", 3, 5, 1, 1, "Z C N", 1, "( a -- a>>1 )",
          "Логический сдвиг вправо; C — выдвинутый 0-й бит"),

    Instr("JMP", 4, 0, 1, 0, "—", 1, "( адр -- )",
          "Безусловный переход: PC ← адр"),
    Instr("JZ", 4, 1, 1, 0, "—", 1, "( адр -- )",
          "Переход, если Z = 1 (результат равен нулю)"),
    Instr("JNZ", 4, 2, 1, 0, "—", 1, "( адр -- )",
          "Переход, если Z = 0"),
    Instr("JC", 4, 3, 1, 0, "—", 1, "( адр -- )",
          "Переход, если C = 1 (был перенос / заём)"),
    Instr("JNC", 4, 4, 1, 0, "—", 1, "( адр -- )",
          "Переход, если C = 0"),
    Instr("JN", 4, 5, 1, 0, "—", 1, "( адр -- )",
          "Переход, если N = 1 (15-й бит результата равен 1)"),
    Instr("JNN", 4, 6, 1, 0, "—", 1, "( адр -- )",
          "Переход, если N = 0"),
]

BY_NAME = {i.mnemonic: i for i in INSTRUCTIONS}
JUMPS = {i.mnemonic for i in INSTRUCTIONS if i.group == 4}

# Такты на выборку и дешифрацию, общие для всех команд
FETCH_DECODE_TICKS = 2

Decoded = namedtuple("Decoded", "mnemonic info value")


def opcode(instr: Instr) -> int:
    """Машинный код команды без литерала."""
    return (instr.group << 12) | (instr.op << 8)


BY_CODE = {opcode(i): i for i in INSTRUCTIONS}


def encode_lit(value: int) -> int:
    if not 0 <= value <= LIT_MAX:
        raise ValueError(f"литерал {value} вне диапазона 0 … {LIT_MAX}")
    return LIT_BIT | value


def encode(mnemonic: str) -> int:
    return opcode(BY_NAME[mnemonic.upper()])


def decode(word: int) -> Decoded:
    """Разбор машинного слова на поля. ValueError — неизвестный код."""
    word &= WORD_MASK
    if word & LIT_BIT:
        return Decoded("PUSH", LIT_INFO, word & LIT_MAX)
    instr = BY_CODE.get(word)
    if instr is None:
        raise ValueError(f"неизвестный код операции 0x{word:04X}")
    return Decoded(instr.mnemonic, instr, None)


def disassemble(word: int) -> str:
    try:
        d = decode(word)
    except ValueError:
        return f"??? (0x{word:04X})"
    if d.value is not None:
        return f"PUSH {d.value}"
    return d.mnemonic


def bin16(word: int) -> str:
    s = f"{word & WORD_MASK:016b}"
    return " ".join(s[i:i + 4] for i in range(0, 16, 4))


def fields_text(word: int) -> str:
    """Поля команды в понятном виде (для регистра IR)."""
    word &= WORD_MASK
    if word & LIT_BIT:
        return f"T=1 (литерал) | значение={word & LIT_MAX}"
    group = (word >> 12) & 0x7
    op = (word >> 8) & 0xF
    low = word & 0xFF
    text = f"T=0 | группа={group} ({GROUPS.get(group, '?')}) | операция={op}"
    if low:
        text += f" | биты 7..0={low:08b} (ошибка)"
    return text
