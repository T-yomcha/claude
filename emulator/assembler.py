"""
Ассемблер (транслятор) для безадресного стекового процессора.

Синтаксис:
    ; комментарий          // тоже комментарий
    .data                  секция данных (ОЗУ данных)
    .code                  секция команд (ОЗУ команд)
    ИМЯ:                   метка — адрес в текущей секции
    ИМЯ: .word 6, 1, 2     слова данных 0 … 65535
    .space 3               три нулевых слова данных

    PUSH n | LIT n | n     положить число (0 … 32767) или адрес метки
    ИМЯ@                   = PUSH ИМЯ  READ    (прочитать переменную)
    ИМЯ.                   = PUSH ИМЯ  WRITE   (записать вершину в переменную)
    JZ МЕТКА               = PUSH МЕТКА  JZ    (так же JMP, JNZ, JC, JNC, JN, JNN)

В одной строке можно записать несколько команд. Регистр букв не важен.
Выражения в операндах: ARR+1, 0x10, 0b101 (без пробелов).

Ассемблер двухпроходный: на первом проходе вычисляются адреса меток,
на втором — значения операндов, поэтому переходы вперёд разрешены.
"""
from __future__ import annotations

import re
from collections import namedtuple

import isa

CODE_SIZE = 32
DATA_SIZE = 32

NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
EXPR_RE = re.compile(r"^[A-Z0-9_+\-]+$")
RESERVED = set(isa.BY_NAME) | {"PUSH", "LIT"}

Symbol = namedtuple("Symbol", "name section addr")
# addr — адрес в ОЗУ команд, word — машинный код, text — команда,
# line — номер строки исходника, origin — как команда записана в исходнике
ListingLine = namedtuple("ListingLine", "addr word text line origin")


class AsmError(Exception):
    def __init__(self, line: int, message: str):
        super().__init__(f"Строка {line}: {message}")
        self.line = line
        self.message = message


class Program:
    """Результат ассемблирования: образы двух памятей и таблица меток."""

    def __init__(self):
        self.code: list[int] = []
        self.data: list[int] = []
        self.symbols: dict[str, Symbol] = {}
        self.listing: list[ListingLine] = []
        self.code_labels: dict[int, str] = {}
        self.data_names: dict[int, str] = {}

    def data_addr(self, name: str) -> int | None:
        sym = self.symbols.get(name.upper())
        if sym is None or sym.section != "data":
            return None
        return sym.addr

    def line_of(self, addr: int) -> int | None:
        if addr is not None and 0 <= addr < len(self.listing):
            return self.listing[addr].line
        return None


def parse_number(token: str) -> int | None:
    t = token.strip().upper()
    try:
        if t.startswith("0X"):
            return int(t[2:], 16)
        if t.startswith("0B"):
            return int(t[2:], 2)
        if t.isdigit():
            return int(t)
    except ValueError:
        return None
    return None


def _eval(expr: str, symbols: dict, line: int) -> int:
    parts = re.findall(r"[+-]|[^+-]+", expr)
    total, sign, need_term = 0, 1, True
    for p in parts:
        if p in ("+", "-"):
            if need_term:             # унарный знак
                if p == "-":
                    sign = -sign
            else:                     # бинарная операция
                sign = 1 if p == "+" else -1
                need_term = True
            continue
        if not need_term:
            raise AsmError(line, f"некорректное выражение '{expr}'")
        value = parse_number(p)
        if value is None:
            if not NAME_RE.match(p):
                raise AsmError(line, f"некорректный операнд '{p}'")
            if p not in symbols:
                raise AsmError(line, f"неизвестная метка '{p}'")
            value = symbols[p].addr
        total += sign * value
        sign, need_term = 1, False
    if need_term:
        raise AsmError(line, f"некорректное выражение '{expr}'")
    return total


def _is_operand(token: str) -> bool:
    """Может ли токен быть операндом перехода (JZ МЕТКА)."""
    return (bool(EXPR_RE.match(token)) and token not in RESERVED
            and not token.endswith(("@", ".", ":")))


def _strip_comment(line: str) -> str:
    for mark in (";", "//"):
        pos = line.find(mark)
        if pos >= 0:
            line = line[:pos]
    return line


def assemble(source: str, code_size: int = CODE_SIZE,
             data_size: int = DATA_SIZE) -> Program:
    prog = Program()
    code_items = []      # (kind, payload, line, origin); kind: "op" | "lit"
    data_items = []      # (expr, line)
    section = "code"

    def define(name: str, line: int):
        if not NAME_RE.match(name):
            raise AsmError(line, f"недопустимое имя метки '{name}'")
        if name in RESERVED:
            raise AsmError(line, f"имя метки '{name}' совпадает с командой")
        if name in prog.symbols:
            raise AsmError(line, f"метка '{name}' уже определена")
        addr = len(code_items) if section == "code" else len(data_items)
        prog.symbols[name] = Symbol(name, section, addr)

    # ---------- проход 1: разбор строк и вычисление адресов меток ----------
    for line_no, raw in enumerate(source.splitlines(), start=1):
        tokens = _strip_comment(raw).replace(",", " ").upper().split()
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            nxt = tokens[i + 1] if i + 1 < len(tokens) else None

            if tok.endswith(":"):
                define(tok[:-1], line_no)
                i += 1
                continue
            if tok == ".DATA":
                section = "data"
                i += 1
                continue
            if tok in (".CODE", ".TEXT"):
                section = "code"
                i += 1
                continue
            if tok == ".WORD":
                if section != "data":
                    raise AsmError(line_no, ".word допускается только в секции .data")
                values = tokens[i + 1:]
                if not values:
                    raise AsmError(line_no, "после .word нужны значения")
                for v in values:
                    data_items.append((v, line_no))
                break
            if tok == ".SPACE":
                if section != "data":
                    raise AsmError(line_no, ".space допускается только в секции .data")
                count = parse_number(nxt) if nxt else None
                if count is None:
                    raise AsmError(line_no, "после .space нужно число слов")
                data_items.extend(("0", line_no) for _ in range(count))
                i += 2
                continue
            if tok.startswith("."):
                raise AsmError(line_no, f"неизвестная директива '{tok}'")
            if section == "data":
                raise AsmError(line_no, f"в секции .data не может быть команды '{tok}'")

            # ----- секция команд -----
            if tok in ("PUSH", "LIT"):
                if nxt is None or not _is_operand(nxt):
                    raise AsmError(line_no, f"после {tok} нужно число или метка")
                code_items.append(("lit", nxt, line_no, f"{tok} {nxt}"))
                i += 2
            elif tok in isa.JUMPS and nxt is not None and _is_operand(nxt):
                origin = f"{tok} {nxt}"
                code_items.append(("lit", nxt, line_no, origin))
                code_items.append(("op", tok, line_no, origin))
                i += 2
            elif tok in isa.BY_NAME:
                code_items.append(("op", tok, line_no, tok))
                i += 1
            elif len(tok) > 1 and tok.endswith("@"):
                code_items.append(("lit", tok[:-1], line_no, tok))
                code_items.append(("op", "READ", line_no, tok))
                i += 1
            elif len(tok) > 1 and tok.endswith("."):
                code_items.append(("lit", tok[:-1], line_no, tok))
                code_items.append(("op", "WRITE", line_no, tok))
                i += 1
            elif parse_number(tok) is not None:
                code_items.append(("lit", tok, line_no, tok))
                i += 1
            else:
                raise AsmError(line_no, f"неизвестная команда '{tok}'")

    if len(code_items) > code_size:
        raise AsmError(code_items[code_size][2],
                       f"программа не помещается в ОЗУ команд ({code_size} слов)")
    if len(data_items) > data_size:
        raise AsmError(data_items[data_size][1],
                       f"данные не помещаются в ОЗУ данных ({data_size} слов)")

    # ---------- проход 2: подстановка значений ----------
    for addr, (kind, payload, line_no, origin) in enumerate(code_items):
        if kind == "op":
            word = isa.encode(payload)
            text = payload
        else:
            value = _eval(payload, prog.symbols, line_no)
            if not 0 <= value <= isa.LIT_MAX:
                raise AsmError(line_no, f"литерал {value} вне диапазона "
                                        f"0 … {isa.LIT_MAX} (15 бит)")
            word = isa.encode_lit(value)
            text = f"PUSH {value}"
            if parse_number(payload) is None:
                text += f"  ({payload})"
        prog.code.append(word)
        prog.listing.append(ListingLine(addr, word, text, line_no, origin))

    for expr, line_no in data_items:
        value = _eval(expr, prog.symbols, line_no)
        if not 0 <= value <= isa.WORD_MASK:
            raise AsmError(line_no, f"значение {value} вне диапазона 0 … 65535")
        prog.data.append(value)

    # ---------- имена ячеек для отображения ----------
    for sym in prog.symbols.values():
        if sym.section == "code":
            old = prog.code_labels.get(sym.addr)
            prog.code_labels[sym.addr] = f"{old}, {sym.name}" if old else sym.name
    data_syms = sorted((s for s in prog.symbols.values() if s.section == "data"),
                       key=lambda s: s.addr)
    for k, sym in enumerate(data_syms):
        end = data_syms[k + 1].addr if k + 1 < len(data_syms) else len(prog.data)
        if end - sym.addr <= 1:
            prog.data_names[sym.addr] = sym.name
        else:
            for a in range(sym.addr, end):
                prog.data_names[a] = f"{sym.name}[{a - sym.addr}]"
    return prog


def format_listing(prog: Program) -> str:
    """Листинг: машинный код и ассемблер (для отчёта)."""
    out = ["ОЗУ КОМАНД (машинные инструкции по 16 бит)",
           f"{'Адр':>4}  {'Код':<6} {'Двоичный код':<20} {'Метка':<8} "
           f"{'Команда':<22} Исходная запись (строка)",
           "-" * 92]
    for item in prog.listing:
        label = prog.code_labels.get(item.addr, "")
        out.append(f"{item.addr:>4}  {item.word:04X}   {isa.bin16(item.word):<20} "
                   f"{label:<8} {item.text:<22} {item.origin} ({item.line})")
    out += ["", "ОЗУ ДАННЫХ (начальное содержимое)",
            f"{'Адр':>4}  {'Имя':<10} {'Dec':>6}  Hex", "-" * 32]
    for addr, value in enumerate(prog.data):
        out.append(f"{addr:>4}  {prog.data_names.get(addr, ''):<10} {value:>6}  {value:04X}")
    return "\n".join(out) + "\n"
