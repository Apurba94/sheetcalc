"""Formula parsing and the built-in functions."""
import math
import re
import statistics
from datetime import date, timedelta

TOKEN = re.compile(r"""\s*(?:(?P<num>\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+)|(?P<str>"(?:[^"]|"")*")
  |(?P<ref>\$?[A-Za-z]{1,3}\$?\d+(?::\$?[A-Za-z]{1,3}\$?\d+)?)(?![A-Za-z0-9_(])
  |(?P<name>[A-Za-z_][A-Za-z0-9_.]*)|(?P<op><=|>=|<>|[-+*/^&=<>(),%]))""", re.X)
CELL = re.compile(r"(\$?)([A-Za-z]{1,3})(\$?)(\d+)$")


class CellError(Exception):
    """A spreadsheet error value such as #DIV/0!, carried through calculation."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code

    def __eq__(self, other):
        return isinstance(other, CellError) and other.code == self.code

    def __hash__(self):
        return hash(self.code)

    def __repr__(self):
        return self.code


class FormulaError(Exception):
    pass


def col_number(letters):
    n = 0
    for ch in letters.upper():
        n = n * 26 + ord(ch) - 64
    return n


def col_letters(n):
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def parse_cell(text):
    m = CELL.match(text)
    if not m:
        raise FormulaError(f"bad cell name: {text}")
    return int(m.group(4)), col_number(m.group(2))


def cell_name(row, col):
    return f"{col_letters(col)}{row}"


def tokenize(src):
    out, pos = [], 0
    src = src.rstrip()
    while pos < len(src):
        m = TOKEN.match(src, pos)
        if not m:
            raise FormulaError(f"cannot read formula at: {src[pos:]}")
        pos = m.end()
        k = m.lastgroup
        v = m.group(k)
        if k == "num":
            out.append(("num", float(v) if any(c in v for c in ".eE") else int(v)))
        elif k == "str":
            out.append(("str", v[1:-1].replace('""', '"')))
        else:
            out.append((k, v))
    out.append(("end", None))
    return out


class Parser:
    """Precedence, lowest first: comparison, &, + -, * /, ^, unary, percent."""

    def __init__(self, src):
        self.t = tokenize(src)
        self.i = 0

    def peek(self):
        return self.t[self.i]

    def take(self):
        self.i += 1
        return self.t[self.i - 1]

    def at(self, *ops):
        return self.peek()[0] == "op" and self.peek()[1] in ops

    def parse(self):
        e = self.compare()
        if self.peek()[0] != "end":
            raise FormulaError(f"unexpected {self.peek()[1]!r}")
        return e

    def binary(self, next_level, *ops):
        e = next_level()
        while self.at(*ops):
            op = self.take()[1]
            e = ("bin", op, e, next_level())
        return e

    def compare(self):
        return self.binary(self.concat, "=", "<>", "<", ">", "<=", ">=")

    def concat(self):
        return self.binary(self.add, "&")

    def add(self):
        return self.binary(self.mul, "+", "-")

    def mul(self):
        return self.binary(self.pow, "*", "/")

    def pow(self):
        return self.binary(self.unary, "^")

    def unary(self):
        if self.at("-"):
            self.take()
            return ("neg", self.unary())
        if self.at("+"):
            self.take()
            return self.unary()
        return self.postfix()

    def postfix(self):
        e = self.atom()
        while self.at("%"):
            self.take()
            e = ("pct", e)
        return e

    def atom(self):
        k, v = self.take()
        if k == "num" or k == "str":
            return ("lit", v)
        if k == "ref":
            if ":" in v:
                a, b = v.split(":")
                return ("range", a.upper(), b.upper())
            return ("ref", v.upper())
        if k == "name":
            if self.at("("):
                self.take()
                args = []
                if not self.at(")"):
                    args.append(self.compare())
                    while self.at(","):
                        self.take()
                        args.append(self.compare())
                if not self.at(")"):
                    raise FormulaError("missing )")
                self.take()
                return ("call", v.upper(), args)
            up = v.upper()
            if up in ("TRUE", "FALSE"):
                return ("lit", up == "TRUE")
            return ("name", v)
        if k == "op" and v == "(":
            e = self.compare()
            if not self.at(")"):
                raise FormulaError("missing )")
            self.take()
            return e
        raise FormulaError(f"unexpected {v!r}")


def parse(src):
    return Parser(src).parse()


def references(tree, out=None):
    """Every cell a formula reads, as (row, col)."""
    out = set() if out is None else out
    if tree[0] == "ref":
        out.add(parse_cell(tree[1].replace("$", "")))
    elif tree[0] == "range":
        out.update(expand(tree[1], tree[2]))
    elif tree[0] == "bin":
        references(tree[2], out)
        references(tree[3], out)
    elif tree[0] in ("neg", "pct"):
        references(tree[1], out)
    elif tree[0] == "call":
        for a in tree[2]:
            references(a, out)
    return out


def expand(a, b):
    r1, c1 = parse_cell(a.replace("$", ""))
    r2, c2 = parse_cell(b.replace("$", ""))
    if r1 > r2:
        r1, r2 = r2, r1
    if c1 > c2:
        c1, c2 = c2, c1
    return [(r, c) for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)]


def shift_formula(src, rows, cols):
    """Rewrite a formula as if copied: relative references move, absolute ones stay."""
    def move(m):
        text = m.group()
        if m.group(1):
            return text
        parts = []
        for part in text.split(":"):
            c = CELL.match(part)
            ca, letters, ra, num = c.groups()
            col = col_number(letters) + (0 if ca else cols)
            row = int(num) + (0 if ra else rows)
            if col < 1 or row < 1:
                return "#REF!"
            parts.append(f"{ca}{col_letters(col)}{ra}{row}")
        return ":".join(parts)
    return re.sub(r'("(?:[^"]|"")*")|\$?\b[A-Za-z]{1,3}\$?\d+\b(?::\$?[A-Za-z]{1,3}\$?\d+)?(?![(A-Za-z0-9_])', move, src)


def number(v):
    if isinstance(v, CellError):
        raise v
    if v is None or v == "":
        return 0
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return v
    try:
        return float(v)
    except ValueError:
        raise CellError("#VALUE!") from None


def text(v):
    if isinstance(v, CellError):
        raise v
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def boolean(v):
    if isinstance(v, CellError):
        raise v
    if isinstance(v, str):
        if v.upper() in ("TRUE", "FALSE"):
            return v.upper() == "TRUE"
        raise CellError("#VALUE!")
    return bool(v)


def numbers(args):
    """Numbers from ranges and scalars, skipping blanks and text inside ranges, as spreadsheets do."""
    out = []
    for a in args:
        if isinstance(a, list):
            for v in a:
                if isinstance(v, CellError):
                    raise v
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    out.append(v)
        else:
            out.append(number(a))
    return out


def flat(args):
    out = []
    for a in args:
        out.extend(a if isinstance(a, list) else [a])
    return out


def criterion(rule):
    """Turn a rule such as ">5" or "apple" into a test on a value."""
    if isinstance(rule, str):
        m = re.match(r"(<=|>=|<>|<|>|=)(.*)", rule)
        if m:
            op, right = m.groups()
            try:
                right = float(right)
            except ValueError:
                pass
            return lambda v: _compare(op, v, right)
        return lambda v: isinstance(v, str) and v.lower() == rule.lower()
    return lambda v: v == rule


def _compare(op, a, b):
    if isinstance(a, CellError):
        return False
    if isinstance(b, (int, float)) and not isinstance(a, (int, float)):
        return op == "<>"
    if isinstance(b, str) and not isinstance(a, str):
        return op == "<>"
    return {"=": a == b, "<>": a != b, "<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]


EPOCH = date(1899, 12, 30)


def to_serial(d):
    return (d - EPOCH).days


def from_serial(n):
    return EPOCH + timedelta(days=int(number(n)))


def fn_round(x, d=0):
    f = 10 ** int(number(d))
    x = number(x)
    return math.floor(abs(x) * f + 0.5) / f * (1 if x >= 0 else -1)


def fn_vlookup(key, table, width, col, approximate=False):
    rows = [table[i:i + width] for i in range(0, len(table), width)]
    col = int(number(col))
    if not 1 <= col <= width:
        raise CellError("#REF!")
    if approximate:
        best = None
        for r in rows:
            if isinstance(r[0], (int, float)) and r[0] <= key:
                best = r
        if best is None:
            raise CellError("#N/A")
        return best[col - 1]
    for r in rows:
        if (isinstance(r[0], str) and isinstance(key, str) and r[0].lower() == key.lower()) or r[0] == key:
            return r[col - 1]
    raise CellError("#N/A")


def safe(fn, *a):
    try:
        return fn(*a)
    except statistics.StatisticsError:
        raise CellError("#DIV/0!") from None
    except (ValueError, OverflowError):
        raise CellError("#NUM!") from None


FUNCTIONS = {
    "SUM": lambda *a: sum(numbers(a)),
    "AVERAGE": lambda *a: safe(statistics.fmean, numbers(a)),
    "MIN": lambda *a: min(numbers(a), default=0),
    "MAX": lambda *a: max(numbers(a), default=0),
    "COUNT": lambda *a: len(numbers(a)),
    "COUNTA": lambda *a: sum(1 for v in flat(a) if v is not None and v != ""),
    "COUNTBLANK": lambda *a: sum(1 for v in flat(a) if v is None or v == ""),
    "PRODUCT": lambda *a: math.prod(numbers(a)),
    "MEDIAN": lambda *a: safe(statistics.median, numbers(a)),
    "STDEV": lambda *a: safe(statistics.stdev, numbers(a)),
    "VAR": lambda *a: safe(statistics.variance, numbers(a)),
    "ABS": lambda x: abs(number(x)),
    "SQRT": lambda x: safe(math.sqrt, number(x)),
    "POWER": lambda x, y: safe(math.pow, number(x), number(y)),
    "MOD": lambda x, y: number(x) % number(y) if number(y) else (_ for _ in ()).throw(CellError("#DIV/0!")),
    "INT": lambda x: math.floor(number(x)),
    "ROUND": fn_round,
    "ROUNDUP": lambda x, d=0: math.ceil(number(x) * 10 ** int(number(d))) / 10 ** int(number(d)),
    "ROUNDDOWN": lambda x, d=0: math.floor(number(x) * 10 ** int(number(d))) / 10 ** int(number(d)),
    "EXP": lambda x: safe(math.exp, number(x)),
    "LN": lambda x: safe(math.log, number(x)),
    "LOG10": lambda x: safe(math.log10, number(x)),
    "PI": lambda: math.pi,
    "LEN": lambda s: len(text(s)),
    "LEFT": lambda s, n=1: text(s)[:int(number(n))],
    "RIGHT": lambda s, n=1: text(s)[-int(number(n)):] if int(number(n)) else "",
    "MID": lambda s, a, n: text(s)[int(number(a)) - 1:int(number(a)) - 1 + int(number(n))],
    "UPPER": lambda s: text(s).upper(),
    "LOWER": lambda s: text(s).lower(),
    "TRIM": lambda s: " ".join(text(s).split()),
    "CONCAT": lambda *a: "".join(text(v) for v in flat(a)),
    "SUBSTITUTE": lambda s, a, b: text(s).replace(text(a), text(b)),
    "FIND": lambda a, s: text(s).index(text(a)) + 1 if text(a) in text(s) else (_ for _ in ()).throw(CellError("#VALUE!")),
    "VALUE": lambda s: number(s),
    "AND": lambda *a: all(boolean(v) for v in flat(a) if v is not None),
    "OR": lambda *a: any(boolean(v) for v in flat(a) if v is not None),
    "NOT": lambda x: not boolean(x),
    "DATE": lambda y, m, d: to_serial(date(int(number(y)), int(number(m)), int(number(d)))),
    "YEAR": lambda n: from_serial(n).year,
    "MONTH": lambda n: from_serial(n).month,
    "DAY": lambda n: from_serial(n).day,
}
LAZY = {"IF", "IFERROR", "ISERROR", "ISBLANK", "ISNUMBER", "ISTEXT", "SUMIF", "COUNTIF", "AVERAGEIF", "VLOOKUP", "INDEX", "MATCH"}
