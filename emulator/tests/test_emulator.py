"""
Тесты эмулятора. Запуск из папки emulator:
    python -m unittest discover -s tests -v
"""
import os
import random
import re
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import isa  # noqa: E402
import tasks  # noqa: E402
from assembler import AsmError, assemble, format_listing  # noqa: E402
from cpu import CPUError, StackCPU  # noqa: E402

TASK1 = os.path.join(ROOT, "programs", "task1_max.asm")
TASK2 = os.path.join(ROOT, "programs", "task2_convolution.asm")


def load_source(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def run_source(source, max_instructions=100_000):
    prog = assemble(source)
    cpu = StackCPU()
    cpu.load(prog.code, prog.data)
    cpu.run(max_instructions)
    return prog, cpu


def run_code(body):
    """Выполнить фрагмент кода (HALT добавляется автоматически)."""
    return run_source(body + "\nHALT")[1]


class IsaTest(unittest.TestCase):
    def test_encode_decode_all(self):
        for ins in isa.INSTRUCTIONS:
            word = isa.encode(ins.mnemonic)
            self.assertEqual(word & isa.LIT_BIT, 0)
            self.assertEqual(word & 0xFF, 0)
            d = isa.decode(word)
            self.assertEqual(d.mnemonic, ins.mnemonic)
            self.assertIsNone(d.value)
            self.assertEqual(isa.disassemble(word), ins.mnemonic)

    def test_unique_codes(self):
        codes = [isa.opcode(i) for i in isa.INSTRUCTIONS]
        self.assertEqual(len(codes), len(set(codes)))

    def test_literal(self):
        self.assertEqual(isa.encode_lit(5), 0x8005)
        self.assertEqual(isa.encode_lit(32767), 0xFFFF)
        d = isa.decode(0x8005)
        self.assertEqual((d.mnemonic, d.value), ("PUSH", 5))
        with self.assertRaises(ValueError):
            isa.encode_lit(32768)
        with self.assertRaises(ValueError):
            isa.encode_lit(-1)

    def test_invalid_codes(self):
        for word in (0x0001, 0x0F00, 0x2800, 0x5000, 0x7F00):
            with self.assertRaises(ValueError):
                isa.decode(word)
        self.assertTrue(isa.disassemble(0x5000).startswith("???"))

    def test_fields_and_bin(self):
        self.assertEqual(isa.bin16(0x8005), "1000 0000 0000 0101")
        self.assertIn("значение=5", isa.fields_text(0x8005))
        self.assertIn("группа=2", isa.fields_text(isa.encode("ADD")))
        self.assertIn("операция=6", isa.fields_text(isa.encode("UMUL")))


class AssemblerTest(unittest.TestCase):
    def test_methodology_example(self):
        # Пример из методички: C = A - B  →  A@ B@ SUB C.
        src = """
        .data
        A: .word 9
        B: .word 4
        C: .word 0
        .code
        A@ B@ SUB C.
        HALT
        """
        prog = assemble(src)
        self.assertEqual([isa.disassemble(w) for w in prog.code],
                         ["PUSH 0", "READ", "PUSH 1", "READ", "SUB",
                          "PUSH 2", "WRITE", "HALT"])
        cpu = StackCPU()
        cpu.load(prog.code, prog.data)
        cpu.run()
        self.assertEqual(cpu.dmem[2], 5)

    def test_numeric_address_forms(self):
        prog = assemble("40@ 41.  HALT")
        self.assertEqual([isa.disassemble(w) for w in prog.code],
                         ["PUSH 40", "READ", "PUSH 41", "WRITE", "HALT"])

    def test_labels_forward_and_jump_sugar(self):
        prog = assemble("""
        START: JMP END     ; переход вперёд
               NOP
        END:   HALT
        """)
        self.assertEqual([isa.disassemble(w) for w in prog.code],
                         ["PUSH 3", "JMP", "NOP", "HALT"])
        self.assertEqual(prog.code_labels, {0: "START", 3: "END"})
        self.assertEqual(prog.line_of(1), 2)

    def test_jump_without_operand_uses_stack(self):
        prog = assemble("PUSH 3 JZ\nDUP")
        self.assertEqual([isa.disassemble(w) for w in prog.code], ["PUSH 3", "JZ", "DUP"])
        prog = assemble("PUSH 3 JZ DUP")
        self.assertEqual([isa.disassemble(w) for w in prog.code], ["PUSH 3", "JZ", "DUP"])

    def test_expressions_and_number_formats(self):
        prog = assemble("""
        .data
        X: .word 0x10, 0b101, 7, X+2
        Y: .space 2
        Z: .word Y
        .code
        PUSH X+1 PUSH 0x7FFF lit 3 12 PUSH Z-1
        """)
        self.assertEqual(prog.data, [16, 5, 7, 2, 0, 0, 4])
        self.assertEqual([isa.disassemble(w) for w in prog.code],
                         ["PUSH 1", "PUSH 32767", "PUSH 3", "PUSH 12", "PUSH 5"])
        self.assertEqual(prog.data_names[0], "X[0]")
        self.assertEqual(prog.data_names[3], "X[3]")
        self.assertEqual(prog.data_names[6], "Z")

    def test_case_and_comments(self):
        prog = assemble("push 1 // комментарий\n  dup ; ещё\nhalt")
        self.assertEqual([isa.disassemble(w) for w in prog.code], ["PUSH 1", "DUP", "HALT"])

    def test_errors(self):
        cases = {
            "FOO": "неизвестная команда",
            "PUSH": "после PUSH",
            "PUSH 40000": "вне диапазона",
            "PUSH NOWHERE": "неизвестная метка",
            "A: NOP\nA: NOP": "уже определена",
            "ADD: NOP": "совпадает с командой",
            ".data\nX: .word 70000": "вне диапазона",
            ".data\nX: .word -1": "вне диапазона",
            ".data\nDUP": "секции .data",
            ".word 1": "только в секции .data",
            ".bogus": "неизвестная директива",
            "PUSH 1+": "некорректное выражение",
            "PUSH X@": "после PUSH",
            "JMP X@": "неизвестная метка 'X'",       # JMP по адресу из стека, затем X@
        }
        for src, fragment in cases.items():
            with self.subTest(src=src):
                with self.assertRaises(AsmError) as ctx:
                    assemble(src)
                self.assertIn(fragment, str(ctx.exception))

    def test_error_line_number(self):
        with self.assertRaises(AsmError) as ctx:
            assemble("NOP\nNOP\nBAD\nNOP")
        self.assertEqual(ctx.exception.line, 3)

    def test_code_overflow(self):
        with self.assertRaises(AsmError):
            assemble("NOP " * 33)
        self.assertEqual(len(assemble("NOP " * 32).code), 32)
        with self.assertRaises(AsmError):
            assemble(".data\n.space 33")

    def test_listing(self):
        text = format_listing(assemble(load_source(TASK1)))
        self.assertIn("ОЗУ КОМАНД", text)
        self.assertIn("ОЗУ ДАННЫХ", text)
        self.assertIn("1000 0000 0000 0000", text)


class CpuTest(unittest.TestCase):
    def test_initial_state(self):
        cpu = StackCPU()
        self.assertEqual((cpu.pc, cpu.ir, cpu.mar, cpu.mdr, cpu.sp, cpu.flags), (0, 0, 0, 0, 0, 0))
        self.assertEqual(len(cpu.cmem), 32)
        self.assertEqual(len(cpu.dmem), 32)

    def test_ticks_per_instruction(self):
        cpu = StackCPU()
        prog = assemble(".data\nV: .word 7\n.code\nPUSH 0 READ DUP HALT")
        cpu.load(prog.code, prog.data)

        info = cpu.tick()
        self.assertEqual(info.phase, "Выборка")
        self.assertEqual((cpu.ir, cpu.pc, cpu.cur_addr), (0x8000, 1, 0))
        self.assertFalse(info.done)
        info = cpu.tick()
        self.assertEqual(info.phase, "Дешифрация")
        self.assertFalse(info.done)
        info = cpu.tick()
        self.assertEqual(info.phase, "Исполнение")
        self.assertTrue(info.done)
        self.assertEqual(cpu.stack, [0])

        infos = cpu.step()                       # READ = 2 + 3 такта
        self.assertEqual(len(infos), 5)
        self.assertEqual((cpu.mar, cpu.mdr, cpu.stack), (0, 7, [7]))
        self.assertEqual(len(cpu.step()), 3)     # DUP
        self.assertEqual(cpu.stack, [7, 7])
        cpu.step()                               # HALT
        self.assertTrue(cpu.halted)
        self.assertEqual((cpu.ticks, cpu.instr_count), (14, 4))
        self.assertIsNone(cpu.tick())

    def test_read_write_registers(self):
        cpu = StackCPU()
        prog = assemble(".data\nV: .word 0\n.code\nPUSH 1234 PUSH V WRITE HALT")
        cpu.load(prog.code, prog.data)
        cpu.step()
        cpu.step()
        cpu.tick(); cpu.tick()                  # выборка, дешифрация WRITE
        cpu.tick()
        self.assertEqual(cpu.mar, 0)
        cpu.tick()
        self.assertEqual(cpu.mdr, 1234)
        self.assertEqual(cpu.dmem[0], 0)        # запись ещё не произошла
        cpu.tick()
        self.assertEqual(cpu.dmem[0], 1234)
        self.assertEqual(cpu.last_write, 0)
        self.assertEqual(cpu.stack, [])

    def test_stack_ops(self):
        self.assertEqual(run_code("PUSH 5 DUP").stack, [5, 5])
        self.assertEqual(run_code("PUSH 5 PUSH 6 DROP").stack, [5])
        self.assertEqual(run_code("PUSH 1 PUSH 2 SWAP").stack, [2, 1])
        self.assertEqual(run_code("PUSH 1 PUSH 2 OVER").stack, [1, 2, 1])
        # Методичка: ROR (1 2 3 4) => (3 1 2 4), ROL (1 2 3 4) => (2 3 1 4),
        # где левое число — вершина. В списке stack вершина — последний элемент.
        self.assertEqual(run_code("PUSH 4 PUSH 3 PUSH 2 PUSH 1 ROR").stack, [4, 2, 1, 3])
        self.assertEqual(run_code("PUSH 4 PUSH 3 PUSH 2 PUSH 1 ROL").stack, [4, 1, 3, 2])

    def alu(self, a, b, op):
        cpu = StackCPU()
        cpu.load([isa.encode(op), isa.encode("HALT")], [])
        cpu.stack = [a, b] if b is not None else [a]
        cpu.run()
        return cpu

    def test_add_adc(self):
        cpu = self.alu(0xFFFF, 1, "ADD")
        self.assertEqual((cpu.stack, cpu.z, cpu.c, cpu.n), ([0], 1, 1, 0))
        cpu = self.alu(0x7FFF, 1, "ADD")
        self.assertEqual((cpu.stack, cpu.z, cpu.c, cpu.n), ([0x8000], 0, 0, 1))
        cpu = run_code("PUSH 0 PUSH 0 NOT ADD PUSH 5 PUSH 6 ADC")  # 0+FFFF → C=0
        self.assertEqual(cpu.stack, [0xFFFF, 11])
        cpu = run_code("PUSH 1 PUSH 0 NOT ADD PUSH 5 PUSH 6 ADC")  # 1+FFFF → C=1
        self.assertEqual(cpu.stack, [0, 12])

    def test_sub_sbb_cmp(self):
        cpu = self.alu(3, 5, "SUB")
        self.assertEqual((cpu.stack, cpu.c, cpu.n, cpu.z), ([0xFFFE], 1, 1, 0))
        cpu = self.alu(5, 5, "SUB")
        self.assertEqual((cpu.stack, cpu.c, cpu.z), ([0], 0, 1))
        cpu = run_code("PUSH 0 PUSH 1 SUB DROP PUSH 10 PUSH 3 SBB")  # заём → 10-3-1
        self.assertEqual(cpu.stack, [6])
        cpu = self.alu(40000, 1200, "CMP")
        self.assertEqual((cpu.stack, cpu.c, cpu.z), ([], 0, 0))
        cpu = self.alu(1200, 40000, "CMP")
        self.assertEqual((cpu.stack, cpu.c), ([], 1))
        cpu = self.alu(7, 7, "CMP")
        self.assertEqual((cpu.z, cpu.c), (1, 0))

    def test_inc_dec(self):
        cpu = self.alu(0xFFFF, None, "INC")
        self.assertEqual((cpu.stack, cpu.z, cpu.c), ([0], 1, 1))
        cpu = self.alu(0, None, "DEC")
        self.assertEqual((cpu.stack, cpu.c, cpu.n), ([0xFFFF], 1, 1))
        cpu = self.alu(1, None, "DEC")
        self.assertEqual((cpu.stack, cpu.z, cpu.c), ([0], 1, 0))

    def test_umul(self):
        cpu = self.alu(65535, 65535, "UMUL")
        product = 65535 * 65535
        self.assertEqual(cpu.stack, [product & 0xFFFF, product >> 16])  # вершина — старшее
        self.assertEqual((cpu.c, cpu.z, cpu.n), (1, 0, 1))
        cpu = self.alu(300, 200, "UMUL")
        self.assertEqual(cpu.stack, [60000, 0])
        self.assertEqual(cpu.c, 0)
        cpu = self.alu(0, 1234, "UMUL")
        self.assertEqual((cpu.stack, cpu.z), ([0, 0], 1))

    def test_logic_and_shifts(self):
        self.assertEqual(self.alu(0b1100, 0b1010, "AND").stack, [0b1000])
        self.assertEqual(self.alu(0b1100, 0b1010, "OR").stack, [0b1110])
        cpu = self.alu(0b1100, 0b1100, "XOR")
        self.assertEqual((cpu.stack, cpu.z, cpu.c), ([0], 1, 0))
        cpu = self.alu(0, None, "NOT")
        self.assertEqual((cpu.stack, cpu.n), ([0xFFFF], 1))
        cpu = self.alu(0x8001, None, "SHL")
        self.assertEqual((cpu.stack, cpu.c), ([0x0002], 1))
        cpu = self.alu(0x8001, None, "SHR")
        self.assertEqual((cpu.stack, cpu.c), ([0x4000], 1))

    def test_transfers_do_not_change_flags(self):
        cpu = run_code(".data\nV: .word 9\n.code\n"
                       "PUSH 0 PUSH 1 SUB DROP PUSH 0 READ DUP DUP V. SWAP OVER NOP JMP NEXT\n"
                       "NEXT: DROP")
        self.assertEqual((cpu.c, cpu.n, cpu.z), (1, 1, 0))

    def test_jumps(self):
        def taken(setup, jump):
            cpu = run_code(f"{setup} {jump} T  PUSH 0 HALT\nT: PUSH 1")
            return cpu.stack[-1] == 1

        zero, nonzero = "PUSH 1 DEC", "PUSH 1 INC"
        carry, nocarry = "PUSH 0 DEC", "PUSH 0 INC"
        neg = "PUSH 0 NOT"
        self.assertTrue(taken("", "JMP"))
        self.assertTrue(taken(zero, "JZ"))
        self.assertFalse(taken(nonzero, "JZ"))
        self.assertTrue(taken(nonzero, "JNZ"))
        self.assertFalse(taken(zero, "JNZ"))
        self.assertTrue(taken(carry, "JC"))
        self.assertFalse(taken(nocarry, "JC"))
        self.assertTrue(taken(nocarry, "JNC"))
        self.assertTrue(taken(neg, "JN"))
        self.assertFalse(taken(neg, "JNN"))
        self.assertTrue(taken(nonzero, "JNN"))

    def test_loop_counter(self):
        # сумма 1..10 циклом с декрементом и JNZ
        src = """
        .data
        I: .word 10
        S: .word 0
        .code
        L: S@ I@ ADD S.
           I@ DEC I.
           JNZ L
           HALT
        """
        prog, cpu = run_source(src)
        self.assertEqual(cpu.dmem[prog.data_addr("S")], 55)

    def test_errors(self):
        cases = [
            ("ADD", "нужно 2"),
            ("DROP", "нужно 1"),
            ("PUSH 300 READ", "вне ОЗУ данных"),
            ("PUSH 1 PUSH 300 WRITE", "вне ОЗУ данных"),
            ("PUSH 300 JMP", "вне ОЗУ команд"),
            (" ".join(["PUSH 1"] * 17), "переполнение стека"),
        ]
        for src, fragment in cases:
            with self.subTest(src=src):
                prog = assemble(src + " HALT")
                cpu = StackCPU()
                cpu.load(prog.code, prog.data)
                with self.assertRaises(CPUError) as ctx:
                    cpu.run()
                self.assertIn(fragment, str(ctx.exception))
                self.assertTrue(cpu.halted)
                self.assertEqual(cpu.error, str(ctx.exception))

    def test_invalid_opcode_and_pc_overflow(self):
        cpu = StackCPU()
        cpu.load([0x5000], [])
        with self.assertRaises(CPUError):
            cpu.run()
        cpu = StackCPU()
        cpu.load([isa.encode("NOP")] * 32, [])
        with self.assertRaises(CPUError) as ctx:
            cpu.run()
        self.assertIn("PC = 32", str(ctx.exception))

    def test_run_limit(self):
        prog = assemble("L: JMP L")
        cpu = StackCPU()
        cpu.load(prog.code, prog.data)
        self.assertEqual(cpu.run(1000), 1000)
        self.assertFalse(cpu.halted)

    def test_reset_restores_memory(self):
        prog = assemble(".data\nV: .word 5\n.code\nPUSH 9 V. HALT")
        cpu = StackCPU()
        cpu.load(prog.code, prog.data)
        cpu.run()
        self.assertEqual(cpu.dmem[0], 9)
        cpu.reset()
        self.assertEqual((cpu.dmem[0], cpu.pc, cpu.ticks, cpu.halted), (5, 0, 0, False))
        cpu.run()
        self.assertEqual(cpu.dmem[0], 9)

    def test_harvard_separate_memories(self):
        # адрес 0 в ОЗУ команд и адрес 0 в ОЗУ данных — разные ячейки
        prog = assemble(".data\nV: .word 77\n.code\nPUSH 0 READ PUSH 0 WRITE HALT")
        cpu = StackCPU()
        cpu.load(prog.code, prog.data)
        cpu.run()
        self.assertEqual(cpu.cmem[0], 0x8000)
        self.assertEqual(cpu.dmem[0], 77)


def with_array(source, label, values):
    """Заменить строку «LABEL: .word …» в тексте программы новым массивом."""
    values = list(values)
    line = f"{label}: .word " + ", ".join(map(str, [len(values)] + values))
    new, count = re.subn(rf"^{label}:.*$", line, source, flags=re.M)
    assert count == 1
    return new


class Task1MaxTest(unittest.TestCase):
    def setUp(self):
        self.source = load_source(TASK1)

    def run_with(self, values):
        prog, cpu = run_source(with_array(self.source, "ARR", values))
        self.assertTrue(cpu.halted)
        self.assertIsNone(cpu.error)
        self.assertEqual(cpu.stack, [], "стек должен остаться пустым")
        return cpu.dmem[prog.data_addr("MAX")]

    def test_fits_memory(self):
        prog = assemble(self.source)
        self.assertLessEqual(len(prog.code), 32)
        self.assertEqual(prog.data_addr("ARR"), 0)   # адрес ARR[i] = i

    def test_example(self):
        prog, cpu = run_source(load_source(TASK1))
        self.assertEqual(cpu.dmem[prog.data_addr("MAX")], 65000)
        lines = tasks.result_lines(prog, cpu.dmem, True)
        self.assertTrue(any("ВЕРНО" in s for s in lines))

    def test_edge_cases(self):
        cases = [
            [0, 0, 0, 0, 0, 0],
            [65535, 1, 2, 3, 4, 5],              # максимум первый
            [1, 2, 3, 4, 5, 65535],              # максимум последний
            [7, 7, 7, 7, 7, 7, 7],
            [32767, 32768, 1, 2, 3, 4],          # граница знакового бита
            [40000, 30000, 50000, 100, 200, 300],
            [42],
            [],
            list(range(1, 16)),                  # 15 элементов
            list(range(30, 0, -1)),              # 30 элементов — заняты все 32 ячейки
        ]
        for values in cases:
            with self.subTest(values=values):
                self.assertEqual(self.run_with(values), tasks.expected_max(values))

    def test_random_arrays(self):
        rnd = random.Random(12)
        for _ in range(300):
            n = rnd.randint(6, 15)
            values = [rnd.randint(0, 65535) for _ in range(n)]
            self.assertEqual(self.run_with(values), max(values), values)


class Task2ConvolutionTest(unittest.TestCase):
    def setUp(self):
        self.prog = assemble(load_source(TASK2))
        self.a = self.prog.data_addr("A")
        self.b = self.prog.data_addr("B")

    def run_with(self, a, b):
        data = list(self.prog.data)
        tasks.write_array(data, self.a, a)
        tasks.write_array(data, self.b, b)
        cpu = StackCPU()
        cpu.load(self.prog.code, data)
        cpu.run()
        self.assertTrue(cpu.halted)
        self.assertIsNone(cpu.error)
        self.assertEqual(cpu.stack, [], "стек должен остаться пустым")
        return tasks.convolution_result(self.prog, cpu.dmem)

    def test_example(self):
        prog, cpu = run_source(load_source(TASK2))
        expected = (1000 * 70 + 2000 * 3000 + 300 * 400 + 40000 * 50000
                    + 5 * 6 + 65535 * 65535)
        self.assertEqual(tasks.convolution_result(prog, cpu.dmem), expected)
        self.assertGreater(expected, 2 ** 32)   # пример задействует все 3 слова
        lines = tasks.result_lines(prog, cpu.dmem, True)
        self.assertTrue(any("ВЕРНО" in s for s in lines))

    def test_edge_cases(self):
        m = 65535
        cases = [
            ([0] * 6, [0] * 6),
            ([1, 2, 3, 4, 5, 6], [6, 5, 4, 3, 2, 1]),      # = 56
            ([m] * 6, [m] * 6),                            # максимум: > 2^32
            ([m] * 6, [1] * 6),                            # переносы в S1
            ([256] * 6, [256] * 6),                        # младшие слова = 0
            ([m, 0, m, 0, m, 0], [m, m, 0, 0, m, m]),
        ]
        for a, b in cases:
            with self.subTest(a=a, b=b):
                self.assertEqual(self.run_with(a, b), tasks.expected_convolution(a, b))
        self.assertEqual(self.run_with([1, 2, 3, 4, 5, 6], [6, 5, 4, 3, 2, 1]), 56)
        self.assertEqual(self.run_with([m] * 6, [m] * 6), 6 * m * m)

    def test_random_arrays(self):
        rnd = random.Random(12)
        for _ in range(300):
            a = [rnd.randint(0, 65535) for _ in range(6)]
            b = [rnd.randint(0, 65535) for _ in range(6)]
            self.assertEqual(self.run_with(a, b), tasks.expected_convolution(a, b), (a, b))

    def test_fits_memory(self):
        self.assertLessEqual(len(self.prog.code), 32)
        self.assertEqual(self.a, 0)                   # адрес A[i] = i

    def test_other_length(self):
        rnd = random.Random(5)
        a = [rnd.randint(0, 65535) for _ in range(13)]
        b = [rnd.randint(0, 65535) for _ in range(13)]
        source = with_array(with_array(load_source(TASK2), "A", a), "B", b)
        prog, cpu = run_source(source)
        self.assertEqual(tasks.convolution_result(prog, cpu.dmem),
                         tasks.expected_convolution(a, b))

    def test_uses_long_arithmetic(self):
        mnemonics = {isa.disassemble(w).split()[0] for w in self.prog.code}
        self.assertTrue({"UMUL", "ADD", "ADC"} <= mnemonics)


def _gui_available():
    try:
        import tkinter
        root = tkinter.Tk()
        root.destroy()
        return True
    except Exception:
        return False


@unittest.skipUnless(_gui_available(), "нет tkinter или дисплея")
class GuiSmokeTest(unittest.TestCase):
    def setUp(self):
        import tkinter
        from gui import EmulatorApp
        self.root = tkinter.Tk()
        self.root.withdraw()
        self.app = EmulatorApp(self.root)

    def tearDown(self):
        self.app.stop()
        self.root.destroy()

    def test_task1_by_ticks_steps_and_run(self):
        app = self.app
        self.assertIn("Задача 1", app.result.cget("text"))
        app.do_tick()
        self.assertEqual(app.cpu.ticks, 1)
        self.assertEqual(app.regs["IR"].cget("text").split()[0], "0x8000")
        app.do_step()
        self.assertEqual(app.cpu.instr_count, 1)
        app.do_step()
        self.assertEqual(app.cpu.instr_count, 2)
        self.assertEqual(app.regs["IR_ASM"].cget("text"), "PUSH 11")   # адрес MAX
        app.run_to_end()
        self.assertTrue(app.cpu.halted)
        self.assertIn("MAX = 65000", app.result.cget("text"))
        self.assertIn("ВЕРНО", app.result.cget("text"))
        self.assertEqual(str(app.btn_step.cget("state")), "disabled")
        app.do_reset()
        self.assertEqual((app.cpu.ticks, app.cpu.halted), (0, False))
        self.assertEqual(str(app.btn_step.cget("state")), "normal")

    def test_task2_run(self):
        app = self.app
        app.load_example(1)
        self.root.update()
        self.assertFalse(app.src_dirty)
        self.assertFalse(app.file_dirty)
        self.assertIn("task2_convolution.asm", self.root.title())
        app.run_to_end()
        self.assertIn("6301026255", app.result.cget("text"))
        self.assertIn("ВЕРНО", app.result.cget("text"))
        self.assertEqual(app.data_tree.item(f"d{app.program.data_addr('S2')}", "values")[2], "1")

    def test_animated_run(self):
        app = self.app
        app.delay.set(0)
        app.toggle_run()
        self.assertTrue(app.running)
        deadline = time.time() + 60
        while app.running and time.time() < deadline:
            self.root.update()
            time.sleep(0.001)
        self.assertTrue(app.cpu.halted)
        self.assertIn("ВЕРНО", app.result.cget("text"))

    def test_edit_reassembles(self):
        app = self.app
        app.editor.delete("1.0", "end")
        app.editor.insert("1.0", ".data\nV: .word 0\n.code\nPUSH 7 V. HALT")
        self.root.update()
        self.assertTrue(app.src_dirty)
        app.run_to_end()
        self.assertFalse(app.src_dirty)
        self.assertEqual(app.cpu.dmem[0], 7)

    def test_help_and_listing_windows(self):
        self.app.show_help()
        self.app.show_listing()
        self.root.update()


if __name__ == "__main__":
    unittest.main()
