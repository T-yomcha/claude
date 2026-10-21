"""
Графический интерфейс эмулятора (tkinter — входит в стандартный Python).
"""
from __future__ import annotations

import os
import tkinter as tk
import tkinter.font as tkfont
from collections import deque
from tkinter import filedialog, messagebox, simpledialog, ttk

import isa
import tasks
from assembler import AsmError, assemble, format_listing, parse_number
from cpu import CPUError, StackCPU

HERE = os.path.dirname(os.path.abspath(__file__))
PROGRAMS_DIR = os.path.join(HERE, "programs")
EXAMPLES = [
    ("Задача 1: максимум", "task1_max.asm"),
    ("Задача 2: свёртка", "task2_convolution.asm"),
]
RUN_LIMIT = 100_000          # защита от бесконечного цикла
LOG_LIMIT = 2000             # строк в журнале

C_CUR = "#fff3b0"            # команда в IR
C_PC = "#cfe8ff"             # следующая команда (PC) / ячейка MAR
C_WRITE = "#ffd8a8"          # последняя записанная ячейка
C_ERR = "#ffc9c9"
C_FLAG_ON = "#7bd88f"
C_FLAG_OFF = "#e2e2e2"
C_CHANGED = "#c92a2a"

HELP_FORMAT = """\
ФОРМАТ КОМАНДЫ (16 бит, фиксированная длина — принцип RISC)

  Литерал (PUSH n):
     бит 15 | биты 14 … 0
        1   | значение n (0 … 32767)

  Остальные команды:
     бит 15 | биты 14..12 | биты 11..8 | биты 7..0
        0   |   группа    |  операция  | не используются (0)

  Группы: 0 — управление, 1 — пересылка и стек, 2 — арифметика,
          3 — логика и сдвиги, 4 — переходы.

  Примеры:  PUSH 5 = 1 000 0000 0000 0101 = 0x8005
            ADD    = 0 010 0000 0000 0000 = 0x2000
            JNZ    = 0 100 0010 0000 0000 = 0x4200

БЕЗАДРЕСНАЯ (СТЕКОВАЯ) МАШИНА
  Операнды берутся из регистрового стека (T0 — вершина, T1 — под ней …),
  результат кладётся на вершину. Адрес для READ / WRITE и адрес перехода
  тоже лежат на вершине стека, поэтому адресных полей в командах нет.
  Для бинарных операций: a = T1 (первый операнд), b = T0 (второй).

ГАРВАРДСКАЯ АРХИТЕКТУРА — две независимые памяти:
  ОЗУ команд  256 слов × 16 бит — адрес задаёт PC, слово попадает в IR;
  ОЗУ данных  256 слов × 16 бит — доступ только через MAR / MDR
              (команды READ и WRITE).

ТАКТЫ ВЫПОЛНЕНИЯ КОМАНДЫ
  1. Выборка:     IR ← ОЗУ_команд[PC];  PC ← PC + 1
  2. Дешифрация:  разбор полей IR, проверка глубины стека
  3. Исполнение:  1 такт; у READ и WRITE — 3 такта (MAR, MDR, память)

ФЛАГИ (регистр FLAGS: бит 2 — N, бит 1 — Z, бит 0 — C)
  Z — результат равен нулю;
  C — перенос (ADD, ADC, INC, SHL, SHR) или заём (SUB, SBB, DEC, CMP);
      у UMUL C = 1, если старшее слово произведения не равно 0;
  N — 15-й (старший) бит результата.
  Флаги меняют только арифметические и логические команды (группы 2 и 3).
  PUSH, READ, WRITE, DUP, SWAP и т. п., а также переходы флаги НЕ меняют —
  это позволяет между ADD и ADC сохранять/загружать переменные.
"""

HELP_ASM = """\
СИНТАКСИС АССЕМБЛЕРА

  ; комментарий        // тоже комментарий
  .data                — начало секции данных (ОЗУ данных)
  .code                — начало секции команд (ОЗУ команд)
  ИМЯ:                 — метка (адрес в текущей секции)
  ИМЯ: .word 6, 1, 2   — слова данных 0 … 65535
                         (первый элемент массива — его длина)
  .space 4             — 4 нулевых слова данных

  PUSH n  (LIT n, n)   — положить в стек число 0 … 32767 или адрес:
                         PUSH 10   PUSH ARR   PUSH ARR+1   PUSH 0x1F
  ИМЯ@                 — прочитать переменную:  PUSH ИМЯ  READ
  ИМЯ.                 — записать вершину стека в переменную: PUSH ИМЯ  WRITE
  JZ МЕТКА             — то же, что PUSH МЕТКА  JZ
                         (так же для JMP, JNZ, JC, JNC, JN, JNN)
  JZ                   — переход по адресу, который уже лежит на вершине стека

  В одной строке можно писать несколько команд:  CNT@ DEC CNT.
  Регистр букв не важен. В выражениях не должно быть пробелов: ARR+1.

ПРИМЕР:  C = A - B
    A@ B@ SUB C.
  транслируется в  PUSH A  READ  PUSH B  READ  SUB  PUSH C  WRITE

РАБОТА С ЭМУЛЯТОРОМ
  F7  — ассемблировать (перевести текст в машинный код и загрузить в ОЗУ)
  F8  — один такт (одна микрооперация)
  F10 — один шаг (одна команда целиком)
  F5  — пуск / стоп (пошагово с задержкой, задаётся ползунком)
  F6  — выполнить до конца (до HALT)
  F2  — сброс (регистры обнуляются, ОЗУ заново загружается из программы)
  F1  — эта справка
  Двойной щелчок по ячейке ОЗУ данных — изменить её значение.
"""

HELP_REGS = """\
РЕГИСТРЫ И ПАМЯТЬ, ОТОБРАЖАЕМЫЕ ЭМУЛЯТОРОМ

  PC     — счётчик команд: адрес следующей команды в ОЗУ команд (16 бит)
  IR     — регистр команд: текущая команда — машинный код, двоичный вид,
           ассемблерная запись и отдельные поля (16 бит)
  MAR    — регистр адреса ОЗУ данных (16 бит)
  MDR    — регистр данных ОЗУ данных (16 бит)
  SP     — указатель стека = количество чисел в стеке (0 … 16)
  T0…T15 — регистровый стек 16 × 16 бит, T0 — вершина
  FLAGS  — регистр флагов: Z (ноль), C (перенос/заём), N (знак, 15-й бит)
  Такт   — счётчик тактов процессора
  Команд — счётчик выполненных команд

  ОЗУ команд — 256 слов × 16 бит (программа)
  ОЗУ данных — 256 слов × 16 бит (массивы и переменные)

ЦВЕТА
  жёлтый   — команда, находящаяся в IR (выполняется / только что выполнена);
  голубой  — следующая команда (PC); в ОЗУ данных — ячейка по адресу MAR;
  оранжевый — ячейка ОЗУ данных, в которую была последняя запись;
  красный текст — значение регистра изменилось на последнем такте.
"""


class LineNumbers(tk.Canvas):
    """Номера строк слева от редактора."""

    def __init__(self, master, text: tk.Text, font, **kw):
        super().__init__(master, width=34, highlightthickness=0, bg="#f2f2f2", **kw)
        self.text = text
        self.font = font

    def redraw(self, *_):
        self.delete("all")
        index = self.text.index("@0,0")
        while True:
            info = self.text.dlineinfo(index)
            if info is None:
                break
            line = index.split(".")[0]
            self.create_text(30, info[1], anchor="ne", text=line,
                             font=self.font, fill="#888888")
            nxt = self.text.index(f"{index}+1line")
            if nxt == index:
                break
            index = nxt


class EmulatorApp:
    def __init__(self, root: tk.Tk, initial_file: str | None = None):
        self.root = root
        self.cpu = StackCPU()
        self.program = None
        self.current_file: str | None = None
        self.src_dirty = True        # текст изменён после ассемблирования
        self.file_dirty = False      # текст не сохранён в файл
        self.running = False
        self._run_job = None
        self._run_count = 0
        self._reg_prev: dict[str, str] = {}
        self._dmem_shown: list = []
        self._hl_code: list[str] = []
        self._hl_data: list[str] = []

        self.fixed = tkfont.nametofont("TkFixedFont").copy()
        self.fixed.configure(size=10)
        self.bold = tkfont.nametofont("TkDefaultFont").copy()
        self.bold.configure(weight="bold")
        self.small = tkfont.nametofont("TkDefaultFont").copy()
        self.small.configure(size=8)

        style = ttk.Style(root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Treeview", font=self.fixed, rowheight=18)
        style.configure("TButton", padding=(6, 2))

        root.title("Эмулятор стекового процессора — вариант 12")
        width = min(1440, root.winfo_screenwidth() - 40)
        height = min(880, root.winfo_screenheight() - 80)
        root.geometry(f"{width}x{height}+10+10")
        root.minsize(1000, 600)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_menu()
        self._build_toolbar()
        self._build_body()
        self._build_statusbar()
        self._bind_keys()

        if initial_file:
            self.open_file(initial_file)
        else:
            self.load_example(0)

    # ================================================================ UI
    def _build_menu(self):
        menu = tk.Menu(self.root)
        fm = tk.Menu(menu, tearoff=False)
        fm.add_command(label="Открыть…", command=self.open_file, accelerator="Ctrl+O")
        fm.add_command(label="Сохранить", command=self.save_file, accelerator="Ctrl+S")
        fm.add_command(label="Сохранить как…", command=self.save_file_as)
        fm.add_separator()
        for i, (title, _) in enumerate(EXAMPLES):
            fm.add_command(label=f"Пример — {title}", command=lambda i=i: self.load_example(i))
        fm.add_separator()
        fm.add_command(label="Листинг (машинный код)…", command=self.show_listing)
        fm.add_separator()
        fm.add_command(label="Выход", command=self.on_close)
        menu.add_cascade(label="Файл", menu=fm)

        rm = tk.Menu(menu, tearoff=False)
        rm.add_command(label="Ассемблировать", command=self.assemble_source, accelerator="F7")
        rm.add_command(label="Такт", command=self.do_tick, accelerator="F8")
        rm.add_command(label="Шаг (команда)", command=self.do_step, accelerator="F10")
        rm.add_command(label="Пуск / Стоп", command=self.toggle_run, accelerator="F5")
        rm.add_command(label="Выполнить до конца", command=self.run_to_end, accelerator="F6")
        rm.add_command(label="Сброс", command=self.do_reset, accelerator="F2")
        menu.add_cascade(label="Выполнение", menu=rm)

        hm = tk.Menu(menu, tearoff=False)
        hm.add_command(label="Описание команд", command=self.show_help, accelerator="F1")
        menu.add_cascade(label="Справка", menu=hm)
        self.root.config(menu=menu)

    def _build_toolbar(self):
        bar = ttk.Frame(self.root, padding=(6, 4))
        bar.grid(row=0, column=0, sticky="ew")

        def btn(text, cmd):
            b = ttk.Button(bar, text=text, command=cmd)
            b.pack(side="left", padx=2)
            return b

        btn("Ассемблировать (F7)", self.assemble_source)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        self.btn_tick = btn("Такт (F8)", self.do_tick)
        self.btn_step = btn("Шаг (F10)", self.do_step)
        self.btn_run = btn("Пуск (F5)", self.toggle_run)
        self.btn_end = btn("До конца (F6)", self.run_to_end)
        btn("Сброс (F2)", self.do_reset)
        ttk.Label(bar, text="  Задержка, мс:").pack(side="left")
        self.delay = tk.IntVar(value=200)
        ttk.Scale(bar, from_=0, to=1000, variable=self.delay, length=110,
                  command=lambda v: self.delay_lbl.config(text=f"{int(float(v)):4d}")
                  ).pack(side="left")
        self.delay_lbl = ttk.Label(bar, text=" 200", width=5, font=self.fixed)
        self.delay_lbl.pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        btn("Задача 1", lambda: self.load_example(0))
        btn("Задача 2", lambda: self.load_example(1))
        btn("Листинг", self.show_listing)
        btn("Справка", self.show_help)

    def _build_body(self):
        self.root.rowconfigure(1, weight=1)
        self.root.columnconfigure(0, weight=1)
        vpaned = ttk.PanedWindow(self.root, orient="vertical")
        vpaned.grid(row=1, column=0, sticky="nsew", padx=4)

        hpaned = ttk.PanedWindow(vpaned, orient="horizontal")
        vpaned.add(hpaned, weight=4)
        hpaned.add(self._build_editor(hpaned), weight=3)
        hpaned.add(self._build_cpu_panel(hpaned), weight=2)
        hpaned.add(self._build_memory(hpaned), weight=3)

        bottom = ttk.Frame(vpaned)
        vpaned.add(bottom, weight=1)
        bottom.columnconfigure(0, weight=3)
        bottom.columnconfigure(1, weight=2)
        bottom.rowconfigure(0, weight=1)

        lf = ttk.LabelFrame(bottom, text="Журнал тактов (микрооперации)")
        lf.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        lf.rowconfigure(0, weight=1)
        lf.columnconfigure(0, weight=1)
        self.log = tk.Text(lf, height=8, wrap="none", font=self.fixed,
                           state="disabled", bg="#fbfbfb")
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")

        rf = ttk.LabelFrame(bottom, text="Результат")
        rf.grid(row=0, column=1, sticky="nsew")
        self.result = ttk.Label(rf, text="", font=self.fixed, justify="left",
                                anchor="nw", wraplength=520)
        self.result.pack(fill="both", expand=True, padx=6, pady=4)

    def _build_editor(self, parent):
        frame = ttk.LabelFrame(parent, text="Программа на ассемблере")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        self.editor = tk.Text(frame, wrap="none", undo=True, font=self.fixed,
                              width=50, height=20)
        self.gutter = LineNumbers(frame, self.editor, self.fixed)
        vs = ttk.Scrollbar(frame, orient="vertical", command=self.editor.yview)
        hs = ttk.Scrollbar(frame, orient="horizontal", command=self.editor.xview)

        def on_yscroll(first, last):
            vs.set(first, last)
            self.gutter.redraw()

        self.editor.configure(yscrollcommand=on_yscroll, xscrollcommand=hs.set)
        self.gutter.grid(row=0, column=0, sticky="ns")
        self.editor.grid(row=0, column=1, sticky="nsew")
        vs.grid(row=0, column=2, sticky="ns")
        hs.grid(row=1, column=1, sticky="ew")
        self.editor.tag_configure("curline", background=C_CUR)
        self.editor.tag_configure("errline", background=C_ERR)
        self.editor.bind("<<Modified>>", self._on_modified)
        self.editor.bind("<Configure>", self.gutter.redraw)
        self.editor.bind("<KeyRelease>", self.gutter.redraw)
        return frame

    def _build_cpu_panel(self, parent):
        frame = ttk.Frame(parent)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        rf = ttk.LabelFrame(frame, text="Регистры процессора")
        rf.grid(row=0, column=0, sticky="ew")
        rf.columnconfigure(1, weight=1)
        self.regs: dict[str, ttk.Label] = {}
        rows = [
            ("PC", "PC", "счётчик команд"),
            ("IR", "IR", "регистр команд"),
            ("IR_BIN", "", "двоичный код"),
            ("IR_ASM", "", "ассемблер"),
            ("IR_FIELDS", "", "поля команды"),
            ("MAR", "MAR", "регистр адреса ОЗУ данных"),
            ("MDR", "MDR", "регистр данных ОЗУ данных"),
            ("SP", "SP", "указатель стека (чисел в стеке)"),
            ("FLAGS", "FLAGS", "регистр флагов"),
            ("TICK", "Такт", "счётчик тактов"),
            ("COUNT", "Команд", "выполнено команд"),
            ("PHASE", "Фаза", "этап выполнения команды"),
            ("MICRO", "Микрооп.", "последний такт"),
            ("STATE", "Состояние", ""),
        ]
        for r, (key, name, hint) in enumerate(rows):
            ttk.Label(rf, text=name, font=self.bold).grid(row=r, column=0, sticky="nw",
                                                          padx=(6, 6), pady=1)
            cell = ttk.Frame(rf)
            cell.grid(row=r, column=1, sticky="ew", pady=1)
            if key == "FLAGS":
                self.flag_lbls = {}
                for f in ("Z", "C", "N"):
                    lb = tk.Label(cell, text=f"{f}=0", width=4, font=self.fixed,
                                  bg=C_FLAG_OFF, relief="groove")
                    lb.pack(side="left", padx=(0, 3))
                    self.flag_lbls[f] = lb
            val = ttk.Label(cell, text="", font=self.fixed, wraplength=330, justify="left")
            val.pack(side="left", fill="x")
            self.regs[key] = val
            if hint:
                ttk.Label(cell, text=f"  {hint}", foreground="#888888",
                          font=self.small).pack(side="left")

        sf = ttk.LabelFrame(frame, text="Регистровый стек (T0 — вершина)")
        sf.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
        sf.rowconfigure(0, weight=1)
        sf.columnconfigure(0, weight=1)
        self.stack_tree = ttk.Treeview(sf, columns=("reg", "dec", "hex", "bin"),
                                       show="headings", height=8, selectmode="none")
        for col, title, w in (("reg", "Регистр", 70), ("dec", "Dec", 70),
                              ("hex", "Hex", 70), ("bin", "Bin", 170)):
            self.stack_tree.heading(col, text=title)
            self.stack_tree.column(col, width=w, anchor="center", stretch=(col == "bin"))
        for i in range(self.cpu.stack_depth):
            self.stack_tree.insert("", "end", iid=f"s{i}", values=(f"T{i}", "", "", ""))
        self.stack_tree.tag_configure("top", background=C_CUR)
        ssb = ttk.Scrollbar(sf, orient="vertical", command=self.stack_tree.yview)
        self.stack_tree.configure(yscrollcommand=ssb.set)
        self.stack_tree.grid(row=0, column=0, sticky="nsew")
        ssb.grid(row=0, column=1, sticky="ns")
        return frame

    def _build_memory(self, parent):
        frame = ttk.Frame(parent)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        cf = ttk.LabelFrame(frame, text=f"ОЗУ команд ({self.cpu.code_size} × 16 бит)")
        cf.grid(row=0, column=0, sticky="nsew")
        self.code_tree = self._make_tree(
            cf, (("addr", "Адрес", 60), ("label", "Метка", 70),
                 ("code", "Код", 55), ("asm", "Команда", 190)))
        ttk.Label(cf, text="жёлтый — команда в IR,  голубой — следующая (PC)",
                  font=self.small, foreground="#666666").grid(row=1, column=0, sticky="w")

        df = ttk.LabelFrame(frame, text=f"ОЗУ данных ({self.cpu.data_size} × 16 бит)")
        df.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
        self.data_tree = self._make_tree(
            df, (("addr", "Адрес", 60), ("name", "Имя", 80),
                 ("dec", "Dec", 70), ("hex", "Hex", 60)))
        ttk.Label(df, text="голубой — ячейка MAR,  оранжевый — последняя запись;  "
                           "двойной щелчок — изменить",
                  font=self.small, foreground="#666666").grid(row=1, column=0, sticky="w")
        self.data_tree.bind("<Double-1>", self._edit_data_cell)

        for tree in (self.code_tree, self.data_tree):
            tree.tag_configure("cur", background=C_CUR)
            tree.tag_configure("pc", background=C_PC)
            tree.tag_configure("mar", background=C_PC)
            tree.tag_configure("write", background=C_WRITE)
            tree.tag_configure("unused", foreground="#9a9a9a")
        for a in range(self.cpu.code_size):
            self.code_tree.insert("", "end", iid=f"c{a}", values=(a, "", "", ""))
        for a in range(self.cpu.data_size):
            self.data_tree.insert("", "end", iid=f"d{a}", values=(a, "", "", ""))
        return frame

    def _make_tree(self, parent, columns):
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        tree = ttk.Treeview(parent, columns=[c[0] for c in columns], show="headings",
                            height=10, selectmode="browse")
        for col, title, w in columns:
            tree.heading(col, text=title)
            tree.column(col, width=w, anchor="w" if col in ("asm", "label", "name")
                        else "center", stretch=col in ("asm", "name"))
        sb = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        return tree

    def _build_statusbar(self):
        self.status = ttk.Label(self.root, text="", relief="sunken", anchor="w", padding=(6, 2))
        self.status.grid(row=2, column=0, sticky="ew")

    def _bind_keys(self):
        keys = {
            "<F1>": self.show_help, "<F2>": self.do_reset, "<F5>": self.toggle_run,
            "<F6>": self.run_to_end, "<F7>": self.assemble_source,
            "<F8>": self.do_tick, "<F10>": self.do_step,
            "<Control-o>": self.open_file, "<Control-s>": self.save_file,
        }
        for seq, fn in keys.items():
            handler = lambda e, fn=fn: (fn(), "break")[1]
            self.root.bind_all(seq, handler)
            # у Text есть свои привязки (Ctrl+O вставляет строку) — перекрываем их
            self.editor.bind(seq, handler)

    # ================================================================ файлы
    def _set_source(self, text: str):
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", text)
        self.editor.edit_reset()
        self.editor.edit_modified(False)
        self.file_dirty = False
        self.src_dirty = True
        self.gutter.redraw()

    def _confirm_discard(self) -> bool:
        if not self.file_dirty:
            return True
        return messagebox.askyesno("Несохранённые изменения",
                                   "Текст программы изменён и не сохранён. Продолжить?")

    def load_example(self, index: int):
        title, name = EXAMPLES[index]
        self.open_file(os.path.join(PROGRAMS_DIR, name))

    def open_file(self, path: str | None = None):
        if not self._confirm_discard():
            return
        if not path:
            path = filedialog.askopenfilename(
                initialdir=PROGRAMS_DIR, title="Открыть программу",
                filetypes=[("Ассемблер", "*.asm"), ("Все файлы", "*.*")])
            if not path:
                return
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except OSError as exc:
            messagebox.showerror("Ошибка", f"Не удалось открыть файл:\n{exc}")
            return
        self.stop()
        self._set_source(text)
        self.current_file = path
        self._update_title()
        self.assemble_source()

    def save_file(self):
        if not self.current_file:
            return self.save_file_as()
        try:
            with open(self.current_file, "w", encoding="utf-8") as f:
                f.write(self.editor.get("1.0", "end-1c"))
        except OSError as exc:
            messagebox.showerror("Ошибка", f"Не удалось сохранить файл:\n{exc}")
            return
        self.file_dirty = False
        self._update_title()
        self._set_status(f"Сохранено: {self.current_file}")

    def save_file_as(self):
        path = filedialog.asksaveasfilename(
            initialdir=PROGRAMS_DIR, defaultextension=".asm", title="Сохранить программу",
            filetypes=[("Ассемблер", "*.asm"), ("Все файлы", "*.*")])
        if path:
            self.current_file = path
            self.save_file()

    def _update_title(self):
        name = os.path.basename(self.current_file) if self.current_file else "без имени"
        mark = " *" if self.file_dirty else ""
        self.root.title(f"{name}{mark} — Эмулятор стекового процессора "
                        f"(вариант 12: безадресная, гарвардская)")

    def _on_modified(self, _event=None):
        if self.editor.edit_modified():
            self.file_dirty = True
            self.src_dirty = True
            self.editor.edit_modified(False)
            self._update_title()

    def on_close(self):
        if self._confirm_discard():
            self.stop()
            self.root.destroy()

    # ================================================================ ассемблирование
    def assemble_source(self) -> bool:
        self.stop()
        self.editor.tag_remove("errline", "1.0", "end")
        try:
            prog = assemble(self.editor.get("1.0", "end-1c"),
                            self.cpu.code_size, self.cpu.data_size)
        except AsmError as exc:
            self.editor.tag_add("errline", f"{exc.line}.0", f"{exc.line}.0 lineend+1c")
            self.editor.see(f"{exc.line}.0")
            self._set_status(f"Ошибка ассемблирования — {exc}")
            messagebox.showerror("Ошибка ассемблирования", str(exc))
            return False
        self.program = prog
        self.cpu.load(prog.code, prog.data)
        self.src_dirty = False
        self._fill_memory()
        self._clear_log()
        msg = (f"Ассемблировано: {len(prog.code)} команд в ОЗУ команд, "
               f"{len(prog.data)} слов в ОЗУ данных")
        self._log(msg)
        self._set_status(msg)
        self._reg_prev.clear()
        self.refresh()
        return True

    def _ensure_program(self) -> bool:
        if self.program is None or self.src_dirty:
            if not self.assemble_source():
                return False
            self._set_status("Текст программы изменён — выполнено повторное ассемблирование")
        return True

    def _fill_memory(self):
        prog = self.program
        for a in range(self.cpu.code_size):
            word = self.cpu.cmem[a]
            if a < len(prog.listing):
                item = prog.listing[a]
                vals = (a, prog.code_labels.get(a, ""), f"{word:04X}", item.text)
                tags = ()
            else:
                vals = (a, "", f"{word:04X}", isa.disassemble(word))
                tags = ("unused",)
            self.code_tree.item(f"c{a}", values=vals, tags=tags)
        self._dmem_shown = [None] * self.cpu.data_size
        self._hl_code, self._hl_data = [], []

    # ================================================================ выполнение
    def _can_execute(self) -> bool:
        if not self._ensure_program():
            return False
        if self.cpu.halted:
            self._set_status("Процессор остановлен. Нажмите «Сброс» (F2) для повторного запуска.")
            return False
        return True

    def do_tick(self):
        self.stop()
        if not self._can_execute():
            return
        try:
            info = self.cpu.tick()
            self._log(self._fmt(info))
        except CPUError:
            self._on_cpu_error()
        self.refresh()
        self._after_halt()

    def do_step(self):
        self.stop()
        if not self._can_execute():
            return
        self._step_once()
        self.refresh()
        self._after_halt()

    def _step_once(self) -> bool:
        try:
            infos = self.cpu.step()
        except CPUError:
            self._on_cpu_error()
            return False
        self._log("\n".join(self._fmt(i) for i in infos))
        return True

    def toggle_run(self):
        if self.running:
            self.stop()
            self._set_status("Выполнение приостановлено")
            return
        if not self._can_execute():
            return
        self.running = True
        self._run_count = 0
        self.btn_run.config(text="Стоп (F5)")
        self._set_status("Выполнение…")
        self._run_next()

    def _run_next(self):
        self._run_job = None
        if not self.running:
            return
        ok = self._step_once()
        self._run_count += 1
        self.refresh()
        if not ok or self.cpu.halted:
            self.stop()
            self._after_halt()
            return
        if self._run_count >= RUN_LIMIT:
            self.stop()
            self._limit_warning()
            return
        self._run_job = self.root.after(max(1, int(self.delay.get())), self._run_next)

    def stop(self):
        self.running = False
        if self._run_job is not None:
            self.root.after_cancel(self._run_job)
            self._run_job = None
        if hasattr(self, "btn_run"):
            self.btn_run.config(text="Пуск (F5)")

    def run_to_end(self):
        self.stop()
        if not self._can_execute():
            return
        lines = deque(maxlen=300)
        count = 0
        try:
            while not self.cpu.halted and count < RUN_LIMIT:
                for info in self.cpu.step():
                    lines.append(self._fmt(info))
                count += 1
        except CPUError:
            self._on_cpu_error()
        if lines:
            self._log("\n".join(lines))
        self.refresh()
        if not self.cpu.halted and count >= RUN_LIMIT:
            self._limit_warning()
        self._after_halt()

    def do_reset(self):
        self.stop()
        if self.program is None or self.src_dirty:
            self.assemble_source()
            return
        self.cpu.reset()
        self._clear_log()
        self._log("Сброс: регистры обнулены, ОЗУ команд и ОЗУ данных загружены заново")
        self._set_status("Сброс выполнен")
        self._reg_prev.clear()
        self.refresh()

    def _after_halt(self):
        cpu = self.cpu
        if cpu.halted and not cpu.error:
            self._set_status(f"Программа завершена (HALT): {cpu.instr_count} команд, "
                             f"{cpu.ticks} тактов")

    def _on_cpu_error(self):
        self.stop()
        self._log(f"ОШИБКА: {self.cpu.error}")
        self._set_status(f"Ошибка выполнения: {self.cpu.error}")
        messagebox.showerror("Ошибка выполнения", self.cpu.error)

    def _limit_warning(self):
        msg = (f"Выполнено {RUN_LIMIT} команд без HALT — возможно, бесконечный цикл. "
               "Выполнение приостановлено.")
        self._set_status(msg)
        messagebox.showwarning("Ограничение", msg)

    @staticmethod
    def _fmt(info) -> str:
        return f"Такт {info.tick:>5} | {info.phase:<11} | {info.text}"

    # ================================================================ отображение
    def _set_reg(self, key: str, text: str):
        lbl = self.regs[key]
        changed = key in self._reg_prev and self._reg_prev[key] != text
        lbl.config(text=text, foreground=C_CHANGED if changed else "")
        self._reg_prev[key] = text

    def refresh(self):
        cpu = self.cpu
        self._set_reg("PC", f"{cpu.pc:<6} 0x{cpu.pc:04X}")
        ir_info = f"  (с адреса {cpu.cur_addr})" if cpu.cur_addr is not None else ""
        self._set_reg("IR", f"0x{cpu.ir:04X}{ir_info}")
        self._set_reg("IR_BIN", isa.bin16(cpu.ir))
        self._set_reg("IR_ASM", isa.disassemble(cpu.ir) if cpu.cur_addr is not None else "—")
        self._set_reg("IR_FIELDS", isa.fields_text(cpu.ir))
        self._set_reg("MAR", f"{cpu.mar:<6} 0x{cpu.mar:04X}")
        self._set_reg("MDR", f"{cpu.mdr:<6} 0x{cpu.mdr:04X}")
        self._set_reg("SP", f"{cpu.sp} из {cpu.stack_depth}")
        self._set_reg("FLAGS", f" 0b{cpu.flags:03b}")
        for f in ("Z", "C", "N"):
            v = getattr(cpu, f.lower())
            self.flag_lbls[f].config(text=f"{f}={v}", bg=C_FLAG_ON if v else C_FLAG_OFF)
        self._set_reg("TICK", str(cpu.ticks))
        self._set_reg("COUNT", str(cpu.instr_count))
        self._set_reg("PHASE", cpu.phase)
        self._set_reg("MICRO", cpu.micro)
        if cpu.error:
            state = "ОШИБКА"
        elif cpu.halted:
            state = "остановлен (HALT)"
        elif cpu.mid_instruction:
            state = "выполняет команду"
        else:
            state = "готов к выборке команды"
        self._set_reg("STATE", state)

        # стек
        for i in range(cpu.stack_depth):
            if i < cpu.sp:
                v = cpu.top(i)
                vals = (f"T{i}", v, f"0x{v:04X}", isa.bin16(v))
            else:
                vals = (f"T{i}", "—", "", "")
            self.stack_tree.item(f"s{i}", values=vals, tags=("top",) if i == 0 and cpu.sp else ())

        self._refresh_memory()
        self._refresh_editor_line()
        self._refresh_result()

        state = "disabled" if cpu.halted else "normal"
        for b in (self.btn_tick, self.btn_step, self.btn_end):
            b.config(state=state)
        if not self.running:
            self.btn_run.config(state=state)

    def _refresh_memory(self):
        cpu = self.cpu
        for iid in self._hl_code:
            tags = () if int(iid[1:]) < len(self.program.listing) else ("unused",)
            self.code_tree.item(iid, tags=tags)
        self._hl_code = []
        marks = {}
        if 0 <= cpu.pc < cpu.code_size:
            marks[cpu.pc] = "pc"
        if cpu.cur_addr is not None:
            marks[cpu.cur_addr] = "cur"
        for addr, tag in marks.items():
            iid = f"c{addr}"
            self.code_tree.item(iid, tags=(tag,))
            self._hl_code.append(iid)
        # один вызов see(): несколько подряд без обновления окна сбивают прокрутку
        target = cpu.cur_addr if cpu.cur_addr is not None else cpu.pc
        if 0 <= target < cpu.code_size:
            self.code_tree.see(f"c{target}")

        names = self.program.data_names
        for a, v in enumerate(cpu.dmem):
            if self._dmem_shown[a] != v:
                self.data_tree.item(f"d{a}", values=(a, names.get(a, ""), v, f"{v:04X}"))
                self._dmem_shown[a] = v
        for iid in self._hl_data:
            self.data_tree.item(iid, tags=())
        self._hl_data = []
        if cpu.last_write is not None:
            self.data_tree.item(f"d{cpu.last_write}", tags=("write",))
            self._hl_data.append(f"d{cpu.last_write}")
        if cpu.ticks and 0 <= cpu.mar < cpu.data_size:
            self.data_tree.item(f"d{cpu.mar}", tags=("mar",))
            self._hl_data.append(f"d{cpu.mar}")
            self.data_tree.see(f"d{cpu.mar}")

    def _refresh_editor_line(self):
        self.editor.tag_remove("curline", "1.0", "end")
        if self.src_dirty:
            return
        line = self.program.line_of(self.cpu.cur_addr)
        if line is None and not self.cpu.halted:
            line = self.program.line_of(self.cpu.pc)
        if line:
            self.editor.tag_add("curline", f"{line}.0", f"{line}.0 lineend+1c")
            self.editor.see(f"{line}.0")
        self.gutter.redraw()

    def _refresh_result(self):
        lines = tasks.result_lines(self.program, self.cpu.dmem,
                                   self.cpu.halted and not self.cpu.error)
        self.result.config(text="\n".join(lines))

    # ================================================================ журнал
    def _log(self, text: str):
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        extra = int(self.log.index("end-1c").split(".")[0]) - LOG_LIMIT
        if extra > 0:
            self.log.delete("1.0", f"{extra + 1}.0")
        self.log.see("end")
        self.log.config(state="disabled")

    def _clear_log(self):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")

    def _set_status(self, text: str):
        self.status.config(text=text)

    # ================================================================ ОЗУ данных
    def _edit_data_cell(self, event):
        iid = self.data_tree.identify_row(event.y)
        if not iid or self.program is None:
            return
        addr = int(iid[1:])
        name = self.program.data_names.get(addr, "")
        answer = simpledialog.askstring(
            "Изменить ячейку ОЗУ данных",
            f"Адрес {addr} {name}\nНовое значение (0 … 65535, можно 0x…):",
            initialvalue=str(self.cpu.dmem[addr]), parent=self.root)
        if answer is None:
            return
        value = parse_number(answer)
        if value is None or not 0 <= value <= isa.WORD_MASK:
            messagebox.showerror("Ошибка", "Нужно целое число от 0 до 65535")
            return
        self.cpu.dmem[addr] = value
        self._log(f"Пользователь записал в ОЗУ данных[{addr}] значение {value}")
        self.refresh()

    # ================================================================ окна
    def show_help(self):
        win = tk.Toplevel(self.root)
        win.title("Справка: система команд процессора")
        win.geometry("1240x640")
        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True)

        tab = ttk.Frame(nb)
        nb.add(tab, text="Команды")
        tab.rowconfigure(0, weight=1)
        tab.columnconfigure(0, weight=1)
        cols = (("mn", "Команда", 75), ("code", "Код", 60), ("grp", "Группа", 175),
                ("stack", "Стек: до -- после", 165), ("flags", "Флаги", 60),
                ("ticks", "Тактов", 60), ("desc", "Описание", 640))
        tree = ttk.Treeview(tab, columns=[c[0] for c in cols], show="headings")
        for col, title, w in cols:
            tree.heading(col, text=title)
            tree.column(col, width=w, anchor="w" if col in ("desc", "stack", "grp") else "center",
                        stretch=col == "desc")
        info = isa.LIT_INFO
        tree.insert("", "end", values=("PUSH n", "8000+n", "литерал (T=1)", info.stack,
                                       info.flags, isa.FETCH_DECODE_TICKS + info.ticks,
                                       info.description))
        for ins in isa.INSTRUCTIONS:
            tree.insert("", "end", values=(
                ins.mnemonic, f"{isa.opcode(ins):04X}", f"{ins.group} — {isa.GROUPS[ins.group]}",
                ins.stack, ins.flags, isa.FETCH_DECODE_TICKS + ins.ticks, ins.description))
        sb = ttk.Scrollbar(tab, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        detail = ttk.Label(tab, text="Выберите команду, чтобы увидеть полное описание.",
                           wraplength=1180, justify="left")
        detail.grid(row=1, column=0, sticky="w", padx=4, pady=2)
        tree.bind("<<TreeviewSelect>>", lambda e: detail.config(
            text="  ".join(str(v) for v in tree.item(tree.selection()[0], "values")[::6])
            if tree.selection() else ""))
        ttk.Label(tab, text="Стековая нотация: справа — вершина стека (T0). "
                            "«Тактов» — всего тактов на команду: выборка + дешифрация + исполнение.",
                  foreground="#555555").grid(row=2, column=0, sticky="w", padx=4, pady=2)

        for title, text in (("Формат и флаги", HELP_FORMAT), ("Ассемблер", HELP_ASM),
                            ("Регистры", HELP_REGS)):
            f = ttk.Frame(nb)
            nb.add(f, text=title)
            t = tk.Text(f, wrap="word", font=self.fixed, padx=10, pady=8)
            t.insert("1.0", text)
            t.config(state="disabled")
            t.pack(fill="both", expand=True)

    def show_listing(self):
        if not self._ensure_program():
            return
        text = format_listing(self.program)
        win = tk.Toplevel(self.root)
        win.title("Листинг: машинный код программы")
        win.geometry("1000x600")
        bar = ttk.Frame(win, padding=4)
        bar.pack(fill="x")

        def save():
            base = os.path.splitext(os.path.basename(self.current_file or "program"))[0]
            path = filedialog.asksaveasfilename(
                parent=win, initialdir=PROGRAMS_DIR, initialfile=base + ".lst",
                defaultextension=".lst", filetypes=[("Листинг", "*.lst *.txt")])
            if path:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                self._set_status(f"Листинг сохранён: {path}")

        ttk.Button(bar, text="Сохранить в файл…", command=save).pack(side="left")
        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True)
        t = tk.Text(frame, wrap="none", font=self.fixed)
        vs = ttk.Scrollbar(frame, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        t.pack(fill="both", expand=True)
        t.insert("1.0", text)
        t.config(state="disabled")


def run_gui(initial_file: str | None = None):
    root = tk.Tk()
    EmulatorApp(root, initial_file)
    root.mainloop()
