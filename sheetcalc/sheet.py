"""The sheet: cells, the dependency graph and incremental recalculation."""
import csv
import io
import json
from collections import deque

from .formula import FUNCTIONS, LAZY, CellError, FormulaError, cell_name, criterion, expand, fn_vlookup, number, parse, parse_cell, references, shift_formula, text

CYCLE = CellError("#CYCLE!")
MAX_DEPTH_RESULT = None


class Cell:
    __slots__ = ("raw", "tree", "value", "deps")

    def __init__(self, raw=None, tree=None, value=None, deps=()):
        self.raw, self.tree, self.value, self.deps = raw, tree, value, deps


def literal(raw):
    """Plain input: numbers become numbers, TRUE and FALSE become booleans, the rest stays text."""
    if not isinstance(raw, str):
        return raw
    s = raw.strip()
    if s.upper() in ("TRUE", "FALSE"):
        return s.upper() == "TRUE"
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return raw


class Sheet:
    def __init__(self):
        self.cells = {}
        self.names = {}
        self.dependents = {}     # (row, col) -> the set of formula cells reading it
        self.evaluations = 0     # how many formulas were recalculated, for tests and tuning

    def _key(self, name):
        return parse_cell(name.upper().replace("$", "")) if isinstance(name, str) else name

    def get(self, name):
        cell = self.cells.get(self._key(name))
        return None if cell is None else cell.value

    def raw(self, name):
        cell = self.cells.get(self._key(name))
        return None if cell is None else cell.raw

    def set(self, name, raw):
        """Set a cell and recalculate only the cells that depend on it."""
        key = self._key(name)
        self._unlink(key)
        if raw is None or raw == "":
            self.cells.pop(key, None)
        elif isinstance(raw, str) and raw.startswith("="):
            try:
                tree = parse(raw[1:])
                deps = frozenset(references(tree))
            except FormulaError:
                tree, deps = None, frozenset()
            self.cells[key] = Cell(raw, tree, None if tree else CellError("#ERROR!"), deps)
            for d in deps:
                self.dependents.setdefault(d, set()).add(key)
        else:
            self.cells[key] = Cell(raw, None, literal(raw))
        self._recalculate(key)

    def _unlink(self, key):
        cell = self.cells.get(key)
        if cell:
            for d in cell.deps:
                users = self.dependents.get(d)
                if users:
                    users.discard(key)
                    if not users:
                        del self.dependents[d]

    def set_many(self, items):
        """Set many cells, then recalculate once, which is faster for a large paste."""
        changed = []
        for name, raw in items.items():
            key = self._key(name)
            self._unlink(key)
            if raw is None or raw == "":
                self.cells.pop(key, None)
            elif isinstance(raw, str) and raw.startswith("="):
                try:
                    tree = parse(raw[1:])
                    deps = frozenset(references(tree))
                except FormulaError:
                    tree, deps = None, frozenset()
                self.cells[key] = Cell(raw, tree, None if tree else CellError("#ERROR!"), deps)
                for d in deps:
                    self.dependents.setdefault(d, set()).add(key)
            else:
                self.cells[key] = Cell(raw, None, literal(raw))
            changed.append(key)
        self._recalculate(*changed)

    def _affected(self, starts):
        seen = set(starts)
        todo = deque(starts)
        while todo:
            for user in self.dependents.get(todo.popleft(), ()):
                if user not in seen:
                    seen.add(user)
                    todo.append(user)
        return seen

    def _recalculate(self, *starts):
        """Kahn's algorithm over the affected cells. Whatever cannot be ordered is in or after a cycle."""
        affected = self._affected(starts)
        waiting = {}
        ready = deque()
        for key in affected:
            cell = self.cells.get(key)
            n = len(cell.deps & affected) if cell and cell.tree else 0
            waiting[key] = n
            if n == 0:
                ready.append(key)
        done = 0
        while ready:
            key = ready.popleft()
            done += 1
            cell = self.cells.get(key)
            if cell and cell.tree:
                self.evaluations += 1
                cell.value = self._value(cell.tree)
            for user in self.dependents.get(key, ()):
                if user in waiting:
                    waiting[user] -= 1
                    if waiting[user] == 0:
                        ready.append(user)
        if done < len(affected):
            for key, n in waiting.items():
                if n > 0:
                    self.cells[key].value = CYCLE

    def _value(self, tree):
        try:
            return self._eval(tree)
        except CellError as exc:
            return exc
        except ZeroDivisionError:
            return CellError("#DIV/0!")
        except TypeError:
            return CellError("#VALUE!")

    def _read(self, name):
        return self.get(name.replace("$", ""))

    def _range(self, a, b):
        return [self.get(k) for k in expand(a, b)]

    def _eval(self, t):
        k = t[0]
        if k == "lit":
            return t[1]
        if k == "ref":
            v = self._read(t[1])
            if isinstance(v, CellError):
                raise v
            return v
        if k == "range":
            return self._range(t[1], t[2])
        if k == "name":
            if t[1].lower() in self.names:
                return self._read(self.names[t[1].lower()])
            raise CellError("#NAME?")
        if k == "neg":
            return -number(self._eval(t[1]))
        if k == "pct":
            return number(self._eval(t[1])) / 100
        if k == "bin":
            op = t[1]
            a, b = self._eval(t[2]), self._eval(t[3])
            if op == "&":
                return text(a) + text(b)
            if op in ("=", "<>", "<", ">", "<=", ">="):
                return self._compare(op, a, b)
            x, y = number(a), number(b)
            if op == "+":
                return x + y
            if op == "-":
                return x - y
            if op == "*":
                return x * y
            if op == "/":
                if y == 0:
                    raise CellError("#DIV/0!")
                return x / y
            try:
                return x ** y
            except (OverflowError, ZeroDivisionError):
                raise CellError("#NUM!") from None
        if k == "call":
            return self._call(t[1], t[2])
        raise CellError("#ERROR!")

    @staticmethod
    def _compare(op, a, b):
        if a is None:
            a = "" if isinstance(b, str) else 0
        if b is None:
            b = "" if isinstance(a, str) else 0
        if isinstance(a, str) != isinstance(b, str):      # numbers sort before text
            c = -1 if isinstance(b, str) else 1
        else:
            x, y = (a.lower(), b.lower()) if isinstance(a, str) else (a, b)
            c = (x > y) - (x < y)
        return {"=": c == 0, "<>": c != 0, "<": c < 0, ">": c > 0, "<=": c <= 0, ">=": c >= 0}[op]

    def _call(self, name, args):
        if name in LAZY:
            return self._lazy(name, args)
        fn = FUNCTIONS.get(name)
        if fn is None:
            raise CellError("#NAME?")
        values = [self._eval(a) for a in args]
        try:
            return fn(*values)
        except TypeError:
            raise CellError("#VALUE!") from None

    def _lazy(self, name, args):
        if name == "IF":
            cond = self._eval(args[0])
            if isinstance(cond, str):
                raise CellError("#VALUE!")
            if cond:
                return self._eval(args[1])
            return self._eval(args[2]) if len(args) > 2 else False
        if name == "IFERROR":
            try:
                return self._eval(args[0])
            except (CellError, ZeroDivisionError):
                return self._eval(args[1])
        if name == "ISERROR":
            try:
                self._eval(args[0])
                return False
            except CellError:
                return True
        if name in ("ISBLANK", "ISNUMBER", "ISTEXT"):
            try:
                v = self._eval(args[0])
            except CellError:
                return False
            if name == "ISBLANK":
                return v is None
            if name == "ISTEXT":
                return isinstance(v, str)
            return isinstance(v, (int, float)) and not isinstance(v, bool)
        if name in ("SUMIF", "COUNTIF", "AVERAGEIF"):
            area = self._eval(args[0])
            test = criterion(self._eval(args[1]))
            if name == "COUNTIF":
                return sum(1 for v in area if test(v))
            source = self._eval(args[2]) if len(args) > 2 else area
            hits = [s for v, s in zip(area, source) if test(v) and isinstance(s, (int, float)) and not isinstance(s, bool)]
            if name == "SUMIF":
                return sum(hits)
            if not hits:
                raise CellError("#DIV/0!")
            return sum(hits) / len(hits)
        rng = args[0] if name == "INDEX" else args[1]
        if name == "VLOOKUP":
            if rng[0] != "range":
                raise CellError("#VALUE!")
            _, c1 = parse_cell(rng[1].replace("$", ""))
            _, c2 = parse_cell(rng[2].replace("$", ""))
            approx = bool(self._eval(args[3])) if len(args) > 3 else False
            return fn_vlookup(self._eval(args[0]), self._eval(rng), c2 - c1 + 1, self._eval(args[2]), approx)
        if name == "MATCH":
            key = self._eval(args[0])
            for i, v in enumerate(self._eval(rng), 1):
                if self._compare("=", v, key):
                    return i
            raise CellError("#N/A")
        if rng[0] != "range":                     # INDEX
            raise CellError("#VALUE!")
        r1, c1 = parse_cell(rng[1].replace("$", ""))
        r2, c2 = parse_cell(rng[2].replace("$", ""))
        row = int(number(self._eval(args[1])))
        col = int(number(self._eval(args[2]))) if len(args) > 2 else 1
        if rows_one := (r1 == r2 and len(args) == 2):
            row, col = 1, row
        if not (1 <= row <= r2 - r1 + 1 and 1 <= col <= c2 - c1 + 1):
            raise CellError("#REF!")
        return self.get((r1 + row - 1, c1 + col - 1))

    def copy(self, source, target):
        """Copy a cell, adjusting relative references the way a paste does."""
        r1, c1 = self._key(source)
        r2, c2 = self._key(target)
        raw = self.raw(source)
        if isinstance(raw, str) and raw.startswith("="):
            raw = "=" + shift_formula(raw[1:], r2 - r1, c2 - c1)
        self.set(target, raw)

    def define(self, name, cell):
        """Give a cell a name for use in formulas, then recalculate every formula."""
        self.names[name.lower()] = cell.upper()
        self._recalculate(*[k for k, c in self.cells.items() if c.tree])

    def display(self, name):
        v = self.get(name)
        if v is None:
            return ""
        if isinstance(v, CellError):
            return v.code
        return text(round(v, 10) if isinstance(v, float) else v)

    def used(self):
        if not self.cells:
            return 0, 0
        return max(r for r, _ in self.cells), max(c for _, c in self.cells)

    def to_csv(self, values=True):
        rows, cols = self.used()
        out = io.StringIO()
        w = csv.writer(out, lineterminator="\n")
        for r in range(1, rows + 1):
            row = []
            for c in range(1, cols + 1):
                row.append(self.display((r, c)) if values else (self.raw((r, c)) or ""))
            w.writerow(row)
        return out.getvalue()

    @classmethod
    def from_csv(cls, data):
        s = cls()
        items = {}
        for r, row in enumerate(csv.reader(io.StringIO(data)), 1):
            for c, v in enumerate(row, 1):
                if v != "":
                    items[cell_name(r, c)] = v
        s.set_many(items)
        return s

    def to_json(self):
        return json.dumps({"cells": {cell_name(*k): c.raw for k, c in sorted(self.cells.items())}, "names": self.names}, ensure_ascii=False, indent=1)

    @classmethod
    def from_json(cls, data):
        d = json.loads(data)
        s = cls()
        s.names = d.get("names", {})
        s.set_many(d["cells"])
        return s
