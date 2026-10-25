"""
Модель безадресного стекового процессора с гарвардской архитектурой.

Компоненты:
    ОЗУ команд   cmem  32 × 16 бит — адресуется счётчиком команд PC
    ОЗУ данных   dmem  32 × 16 бит — доступ только через MAR / MDR
    PC    — счётчик команд (адрес следующей команды)
    IR    — регистр команд (текущая команда)
    MAR   — регистр адреса ОЗУ данных
    MDR   — регистр данных ОЗУ данных
    стек  — регистровый стек 16 × 16 бит, T0 — вершина; SP — число элементов
    FLAGS — регистр флагов: бит 2 — N, бит 1 — Z, бит 0 — C

Каждая команда выполняется за несколько тактов:
    1) выборка      IR ← ОЗУ_команд[PC], PC ← PC + 1
    2) дешифрация   разбор полей IR, проверка глубины стека
    3…) исполнение  микрооперации команды (1 такт, у READ/WRITE — 3 такта)
"""
from __future__ import annotations

from collections import deque, namedtuple

import isa

CODE_SIZE = 32
DATA_SIZE = 32
STACK_DEPTH = 16
MASK = isa.WORD_MASK

TickInfo = namedtuple("TickInfo", "tick phase text done")

_JUMP_COND = {
    "JMP": (None, None),
    "JZ": ("z", 1), "JNZ": ("z", 0),
    "JC": ("c", 1), "JNC": ("c", 0),
    "JN": ("n", 1), "JNN": ("n", 0),
}


class CPUError(Exception):
    """Аварийная ситуация при выполнении программы."""


class StackCPU:
    def __init__(self, code_size: int = CODE_SIZE, data_size: int = DATA_SIZE,
                 stack_depth: int = STACK_DEPTH):
        self.code_size = code_size
        self.data_size = data_size
        self.stack_depth = stack_depth
        self._code_image: list[int] = []
        self._data_image: list[int] = []
        self.reset()

    # ------------------------------------------------------------ загрузка
    def load(self, code, data):
        if len(code) > self.code_size or len(data) > self.data_size:
            raise ValueError("программа или данные не помещаются в память")
        self._code_image = [w & MASK for w in code]
        self._data_image = [w & MASK for w in data]
        self.reset()

    def reset(self):
        """Сброс: регистры обнуляются, обе памяти заново заполняются образом программы."""
        self.cmem = self._code_image + [0] * (self.code_size - len(self._code_image))
        self.dmem = self._data_image + [0] * (self.data_size - len(self._data_image))
        self.pc = 0
        self.ir = 0
        self.mar = 0
        self.mdr = 0
        self.stack: list[int] = []        # stack[-1] — вершина T0
        self.z = self.c = self.n = 0
        self.ticks = 0
        self.instr_count = 0
        self.halted = False
        self.error: str | None = None
        self.cur_addr: int | None = None  # адрес команды, находящейся в IR
        self.phase = "—"
        self.micro = "Процессор сброшен"
        self.last_read: int | None = None
        self.last_write: int | None = None
        self._queue: deque = deque()

    # ------------------------------------------------------------ состояние
    @property
    def sp(self) -> int:
        return len(self.stack)

    @property
    def flags(self) -> int:
        return (self.n << 2) | (self.z << 1) | self.c

    def top(self, i: int = 0) -> int:
        return self.stack[-1 - i]

    @property
    def mid_instruction(self) -> bool:
        return bool(self._queue)

    def flags_text(self) -> str:
        return f"Z={self.z} C={self.c} N={self.n}"

    # ------------------------------------------------------------ выполнение
    def tick(self) -> TickInfo | None:
        """Один такт процессора (одна микрооперация)."""
        if self.halted:
            return None
        if not self._queue:
            self._queue.append(("Выборка", self._fetch))
        phase, action = self._queue.popleft()
        try:
            text = action()
        except CPUError as exc:
            self._queue.clear()
            self.halted = True
            self.error = str(exc)
            self.phase = "Ошибка"
            self.micro = str(exc)
            raise
        self.ticks += 1
        self.phase = phase
        self.micro = text
        done = not self._queue
        if done:
            self.instr_count += 1
        return TickInfo(self.ticks, phase, text, done)

    def step(self) -> list[TickInfo]:
        """Выполнить одну команду (или завершить начатую по тактам)."""
        infos = []
        while not self.halted:
            info = self.tick()
            infos.append(info)
            if info.done:
                break
        return infos

    def run(self, max_instructions: int = 100_000) -> int:
        """Выполнять до HALT. Возвращает число выполненных команд."""
        executed = 0
        while not self.halted and executed < max_instructions:
            self.step()
            executed += 1
        return executed

    # ------------------------------------------------------------ стек
    def _push(self, value: int):
        if len(self.stack) >= self.stack_depth:
            raise CPUError(f"Переполнение стека (максимум {self.stack_depth} чисел)")
        self.stack.append(value & MASK)

    def _pop(self) -> int:
        if not self.stack:
            raise CPUError("Стек пуст — нечего снимать")
        return self.stack.pop()

    def _check_data_addr(self, addr: int):
        if not 0 <= addr < self.data_size:
            raise CPUError(f"Адрес {addr} вне ОЗУ данных (0 … {self.data_size - 1})")

    # ------------------------------------------------------------ такты
    def _fetch(self) -> str:
        if not 0 <= self.pc < self.code_size:
            raise CPUError(f"PC = {self.pc} вне ОЗУ команд (0 … {self.code_size - 1})")
        self.cur_addr = self.pc
        self.ir = self.cmem[self.pc]
        self.pc += 1
        self._queue.append(("Дешифрация", self._decode))
        return (f"IR ← ОЗУ_команд[{self.cur_addr}] = 0x{self.ir:04X}; "
                f"PC ← PC + 1 = {self.pc}")

    def _decode(self) -> str:
        try:
            d = isa.decode(self.ir)
        except ValueError as exc:
            raise CPUError(f"Адрес {self.cur_addr}: {exc}") from None
        info = d.info
        depth = len(self.stack)
        if depth < info.pops:
            raise CPUError(f"{d.mnemonic}: в стеке {depth} чисел, "
                           f"а команде нужно {info.pops}")
        if depth - info.pops + info.pushes > self.stack_depth:
            raise CPUError(f"{d.mnemonic}: переполнение стека "
                           f"(максимум {self.stack_depth} чисел)")
        for action in self._plan(d):
            self._queue.append(("Исполнение", action))
        return f"0x{self.ir:04X} → {isa.disassemble(self.ir)}   [{isa.fields_text(self.ir)}]"

    def _plan(self, d: isa.Decoded):
        """Список микроопераций этапа исполнения."""
        m = d.mnemonic
        if m == "PUSH":
            return [lambda: self._ex_lit(d.value)]
        if m == "READ":
            return [self._read_mar, self._read_mem, self._read_push]
        if m == "WRITE":
            return [self._write_mar, self._write_mdr, self._write_mem]
        if m in _JUMP_COND:
            return [lambda: self._ex_jump(m)]
        if d.info.group in (2, 3):
            return [lambda: self._ex_alu(m)]
        return [getattr(self, "_ex_" + m.lower())]

    # --- пересылки
    def _ex_lit(self, value: int) -> str:
        self._push(value)
        return f"T0 ← {value} (литерал из IR[14..0])"

    def _read_mar(self) -> str:
        self.mar = self._pop()
        return f"MAR ← T0 = {self.mar} (адрес снят со стека)"

    def _read_mem(self) -> str:
        self._check_data_addr(self.mar)
        self.mdr = self.dmem[self.mar]
        self.last_read = self.mar
        return f"MDR ← ОЗУ_данных[{self.mar}] = {self.mdr}"

    def _read_push(self) -> str:
        self._push(self.mdr)
        return f"T0 ← MDR = {self.mdr}"

    def _write_mar(self) -> str:
        self.mar = self._pop()
        return f"MAR ← T0 = {self.mar} (адрес снят со стека)"

    def _write_mdr(self) -> str:
        self.mdr = self._pop()
        return f"MDR ← T0 = {self.mdr} (значение снято со стека)"

    def _write_mem(self) -> str:
        self._check_data_addr(self.mar)
        self.dmem[self.mar] = self.mdr
        self.last_write = self.mar
        return f"ОЗУ_данных[{self.mar}] ← MDR = {self.mdr}"

    # --- управление
    def _ex_halt(self) -> str:
        self.halted = True
        return "Останов процессора (HALT)"

    def _ex_nop(self) -> str:
        return "Нет операции"

    # --- стек
    def _ex_dup(self) -> str:
        self._push(self.top())
        return f"T0 ← T0 = {self.top()}"

    def _ex_drop(self) -> str:
        return f"Снято со стека: {self._pop()}"

    def _ex_swap(self) -> str:
        b, a = self._pop(), self._pop()
        self._push(b)
        self._push(a)
        return f"T0 ↔ T1: T0 = {a}, T1 = {b}"

    def _ex_over(self) -> str:
        self._push(self.top(1))
        return f"T0 ← T1 = {self.top()}"

    def _ex_ror(self) -> str:
        c, b, a = self._pop(), self._pop(), self._pop()
        for v in (b, c, a):
            self._push(v)
        return f"( {a} {b} {c} -- {b} {c} {a} )"

    def _ex_rol(self) -> str:
        c, b, a = self._pop(), self._pop(), self._pop()
        for v in (c, a, b):
            self._push(v)
        return f"( {a} {b} {c} -- {c} {a} {b} )"

    # --- АЛУ
    def _set_flags(self, result: int, carry: int):
        self.z = int(result == 0)
        self.n = (result >> 15) & 1
        self.c = int(bool(carry))

    def _ex_alu(self, m: str) -> str:
        if m == "UMUL":
            b, a = self._pop(), self._pop()
            product = a * b
            lo, hi = product & MASK, product >> 16
            self._push(lo)
            self._push(hi)
            self.z = int(product == 0)
            self.n = (hi >> 15) & 1
            self.c = int(hi != 0)
            return (f"АЛУ: {a} × {b} = {product} → T0 = ст. {hi}, T1 = мл. {lo}; "
                    f"{self.flags_text()}")

        if m in ("INC", "DEC", "NOT", "SHL", "SHR"):
            a = self._pop()
            if m == "INC":
                raw = a + 1
                result, carry, expr = raw & MASK, raw > MASK, f"{a} + 1"
            elif m == "DEC":
                raw = a - 1
                result, carry, expr = raw & MASK, raw < 0, f"{a} - 1"
            elif m == "NOT":
                result, carry, expr = ~a & MASK, 0, f"NOT {a}"
            elif m == "SHL":
                result, carry, expr = (a << 1) & MASK, a >> 15, f"{a} << 1"
            else:
                result, carry, expr = a >> 1, a & 1, f"{a} >> 1"
            self._set_flags(result, carry)
            self._push(result)
            return f"АЛУ: {expr} = {result} → T0; {self.flags_text()}"

        b, a = self._pop(), self._pop()
        c_in = self.c
        if m == "ADD":
            raw = a + b
            result, carry, expr = raw & MASK, raw > MASK, f"{a} + {b}"
        elif m == "ADC":
            raw = a + b + c_in
            result, carry, expr = raw & MASK, raw > MASK, f"{a} + {b} + C({c_in})"
        elif m in ("SUB", "CMP"):
            raw = a - b
            result, carry, expr = raw & MASK, raw < 0, f"{a} - {b}"
        elif m == "SBB":
            raw = a - b - c_in
            result, carry, expr = raw & MASK, raw < 0, f"{a} - {b} - C({c_in})"
        elif m == "AND":
            result, carry, expr = a & b, 0, f"{a} AND {b}"
        elif m == "OR":
            result, carry, expr = a | b, 0, f"{a} OR {b}"
        elif m == "XOR":
            result, carry, expr = a ^ b, 0, f"{a} XOR {b}"
        else:
            raise CPUError(f"АЛУ не поддерживает операцию {m}")
        self._set_flags(result, carry)
        if m == "CMP":
            return f"АЛУ: сравнение {expr} = {result} (результат не сохраняется); {self.flags_text()}"
        self._push(result)
        return f"АЛУ: {expr} = {result} → T0; {self.flags_text()}"

    # --- переходы
    def _ex_jump(self, m: str) -> str:
        addr = self._pop()
        flag, need = _JUMP_COND[m]
        if flag is None:
            taken, cond = True, "безусловный переход"
        else:
            value = getattr(self, flag)
            taken = value == need
            cond = f"{flag.upper()} = {value}, " + ("условие выполнено" if taken
                                                   else "условие не выполнено")
        if not taken:
            return f"Адрес перехода {addr} снят со стека; {cond} → PC = {self.pc}"
        if not 0 <= addr < self.code_size:
            raise CPUError(f"Адрес перехода {addr} вне ОЗУ команд (0 … {self.code_size - 1})")
        self.pc = addr
        return f"Адрес перехода {addr} снят со стека; {cond} → PC ← {addr}"
