# Vendored from mtamsyed-gc/ete-dashboard side_tools/interim_pipeline/alteryx/sim.py @ 412a36c (2026-10-14).
# Local changes are marked 'XOL:'.
"""Strict executor for the .yxmd files this folder generates (test harness only; not used by the owner).

    python alteryx/sim.py <workflow.yxmd>

It runs the subset of Designer tools the generator emits (Text Input, Input xlsx / yxdb, Output csv / yxdb
(a .yxdb is stood in for by a pickle of the typed table), Formula, Filter,
Select, Join, Union, Summarize, Sort, Unique, Multi-Row Formula, Generate Rows, Record ID) and the subset of the
formula language it writes. It is deliberately STRICTER than Designer, so the generated workflow cannot depend on
behaviour that is uncertain in Designer:
  - a comparison, AND/OR/NOT or string concatenation that meets a Null raises (Designer would return something);
  - comparing a Date with a DateTime or a string raises (Designer compares them as text);
  - a Join key that is Null, a Join that would create a Right_ prefixed field, and a Union of a Date with a
    non-Date field all raise;
  - ToString of a non-integer raises; a literal containing a double quote raises.
Arithmetic and functions on Null return Null, as in Designer.
"""
import csv
import math
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path


class SimError(Exception):
    pass


INTS = {"Byte", "Int16", "Int32", "Int64"}
NUMS = INTS | {"Double", "Float", "FixedDecimal"}
STRS = {"String", "WString", "V_String", "V_WString"}


# ============================================================================================== expressions
TOK = re.compile(r"""\s*(?:
    (?P<field>\[[^\]]+\])
  | "(?P<dq>[^"]*)"
  | '(?P<sq>[^']*)'
  | (?P<num>\d+(?:\.\d+)?)
  | (?P<op><=|>=|!=|<>|==|&&|\|\||[=<>+\-*/!(),])
  | (?P<id>[A-Za-z_][A-Za-z0-9_.]*)
)""", re.X)


def tokenize(s):
    pos, out = 0, []
    s = s.strip()
    while pos < len(s):
        m = TOK.match(s, pos)
        if not m or m.end() == pos:
            raise SimError(f"cannot tokenise at {pos}: {s[pos:pos + 30]!r}")
        pos = m.end()
        k = m.lastgroup
        v = m.group(k)
        if k == "id" and v.upper() in ("AND", "OR", "NOT", "IN", "IF", "THEN", "ELSE", "ELSEIF", "ENDIF"):
            out.append(("kw", v.upper()))
        elif k in ("dq", "sq"):
            out.append(("str", v))
        else:
            out.append((k, v))
        while pos < len(s) and s[pos].isspace():
            pos += 1
    return out


def _isnull(v):
    return v is None


def _typecheck_cmp(a, b):
    if a is None or b is None:
        raise SimError("comparison with Null (guard with IsNull)")
    ta, tb = type(a), type(b)
    if isinstance(a, bool) or isinstance(b, bool):
        raise SimError("comparison of a boolean")
    num = (int, float)
    if isinstance(a, num) and isinstance(b, num):
        return
    if ta is tb:
        return
    raise SimError(f"comparison of {ta.__name__} with {tb.__name__}")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


class Ctx:
    """Row context: fields of the current row, plus neighbours for Multi-Row."""
    __slots__ = ("row", "prev", "nxt")

    def __init__(self, row, prev=None, nxt=None):
        self.row, self.prev, self.nxt = row, prev, nxt


class Parser:
    def __init__(self, s):
        self.src = s
        self.t = tokenize(s)
        self.i = 0

    def peek(self, k=0):
        return self.t[self.i + k] if self.i + k < len(self.t) else (None, None)

    def take(self, kind=None, val=None):
        tk = self.peek()
        if (kind and tk[0] != kind) or (val and tk[1] != val):
            raise SimError(f"expected {kind} {val} got {tk} in {self.src[:200]!r}")
        self.i += 1
        return tk

    def parse(self):
        e = self.p_or()
        if self.i != len(self.t):
            raise SimError(f"trailing tokens {self.t[self.i:self.i + 3]} in {self.src[:200]!r}")
        return e

    def p_or(self):
        a = self.p_and()
        while self.peek() in (("kw", "OR"), ("op", "||")):
            self.i += 1
            b = self.p_and()
            a = (lambda x, y: lambda c: _bool(x(c)) or _bool(y(c)))(a, b)
        return a

    def p_and(self):
        a = self.p_not()
        while self.peek() in (("kw", "AND"), ("op", "&&")):
            self.i += 1
            b = self.p_not()
            a = (lambda x, y: lambda c: _bool(x(c)) and _bool(y(c)))(a, b)
        return a

    def p_not(self):
        if self.peek() in (("kw", "NOT"), ("op", "!")):
            self.i += 1
            a = self.p_not()
            return lambda c: not _bool(a(c))
        return self.p_cmp()

    def p_cmp(self):
        a = self.p_add()
        tk = self.peek()
        if tk[0] == "op" and tk[1] in ("=", "==", "!=", "<>", "<", "<=", ">", ">="):
            self.i += 1
            b = self.p_add()
            op = tk[1]

            def f(c, a=a, b=b, op=op):
                x, y = a(c), b(c)
                _typecheck_cmp(x, y)
                if op in ("=", "=="):
                    return x == y
                if op in ("!=", "<>"):
                    return x != y
                return {"<": x < y, "<=": x <= y, ">": x > y, ">=": x >= y}[op]
            return f
        if tk == ("kw", "IN"):
            self.i += 1
            self.take("op", "(")
            vals = [self.p_add()]
            while self.peek() == ("op", ","):
                self.i += 1
                vals.append(self.p_add())
            self.take("op", ")")

            def g(c, a=a, vals=vals):
                x = a(c)
                vs = [v(c) for v in vals]
                for v in vs:
                    _typecheck_cmp(x, v)
                return x in vs
            return g
        return a

    def p_add(self):
        a = self.p_mul()
        while self.peek() in (("op", "+"), ("op", "-")):
            op = self.take()[1]
            b = self.p_mul()

            def f(c, a=a, b=b, op=op):
                x, y = a(c), b(c)
                if isinstance(x, str) or isinstance(y, str):
                    if op == "-":
                        raise SimError("string minus")
                    if x is None or y is None:
                        raise SimError("string concatenation with Null")
                    if not (isinstance(x, str) and isinstance(y, str)):
                        raise SimError("string + number")
                    return x + y
                if x is None or y is None:
                    return None
                if not (_num(x) and _num(y)):
                    raise SimError(f"arithmetic on {type(x).__name__}/{type(y).__name__}")
                return x + y if op == "+" else x - y
            a = f
        return a

    def p_mul(self):
        a = self.p_unary()
        while self.peek() in (("op", "*"), ("op", "/")):
            op = self.take()[1]
            b = self.p_unary()

            def f(c, a=a, b=b, op=op):
                x, y = a(c), b(c)
                if x is None or y is None:
                    return None
                if not (_num(x) and _num(y)):
                    raise SimError("arithmetic on non-number")
                if op == "*":
                    return x * y
                if y == 0:
                    raise SimError("division by zero")
                return x / y
            a = f
        return a

    def p_unary(self):
        if self.peek() == ("op", "-"):
            self.i += 1
            a = self.p_unary()
            return lambda c: None if a(c) is None else -a(c)
        return self.p_primary()

    def p_primary(self):
        k, v = self.peek()
        if k == "num":
            self.i += 1
            n = float(v) if "." in v else int(v)
            return lambda c: n
        if k == "str":
            self.i += 1
            return lambda c: v
        if k == "field":
            self.i += 1
            name = v[1:-1]
            m = re.match(r"Row([+-]\d+):(.*)$", name)
            if m:
                off, fld = int(m.group(1)), m.group(2)
                if off == -1:
                    return lambda c: c.prev[fld] if fld in c.prev else _missing(fld)
                if off == 1:
                    return lambda c: c.nxt[fld] if fld in c.nxt else _missing(fld)
                raise SimError("only Row-1 / Row+1")
            return lambda c: c.row[name] if name in c.row else _missing(name)
        if (k, v) == ("op", "("):
            self.i += 1
            e = self.p_or()
            self.take("op", ")")
            return e
        if (k, v) == ("kw", "IF"):
            self.i += 1
            arms = []
            cond = self.p_or()
            self.take("kw", "THEN")
            arms.append((cond, self.p_or()))
            while self.peek() == ("kw", "ELSEIF"):
                self.i += 1
                cnd = self.p_or()
                self.take("kw", "THEN")
                arms.append((cnd, self.p_or()))
            self.take("kw", "ELSE")
            other = self.p_or()
            self.take("kw", "ENDIF")

            def f(c, arms=arms, other=other):
                for cnd, val in arms:
                    if _bool(cnd(c)):
                        return val(c)
                return other(c)
            return f
        if k == "id" and v.lower() in ("true", "false") and self.peek(1) != ("op", "("):
            # XOL: Designer 26.1 (laptop, 2026-10-14): "Parse Error ... Unknown variable "True"". Use 1=1 / 1=0.
            raise SimError(f"{v} is not a Designer constant (use 1=1 / 1=0) in {self.src[:200]!r}")
        if k == "id":
            self.i += 1
            if self.peek() != ("op", "("):
                # bare identifier: Generate Rows created field
                return lambda c: c.row[v] if v in c.row else _missing(v)
            self.take("op", "(")
            args = []
            if self.peek() != ("op", ")"):
                args.append(self.p_or())
                while self.peek() == ("op", ","):
                    self.i += 1
                    args.append(self.p_or())
            self.take("op", ")")
            return make_func(v, args)
        raise SimError(f"unexpected {k} {v} in {self.src[:200]!r}")


def _missing(name):
    raise SimError(f"unknown field [{name}]")


def _bool(v):
    if v is None:
        raise SimError("boolean of Null (guard with IsNull)")
    if isinstance(v, bool):
        return v
    if _num(v):
        return v != 0
    raise SimError(f"boolean of {type(v).__name__}")


def _todate(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        s = v.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}( \d{2}:\d{2}:\d{2})?", s):
            return date(int(s[:4]), int(s[5:7]), int(s[8:10]))
        return None   # Designer: conversion error -> Null
    raise SimError(f"ToDate of {type(v).__name__}")


def _dt(v):
    """Date or DateTime argument of a DateTime function."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    if isinstance(v, str):
        s = v.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            return datetime(int(s[:4]), int(s[5:7]), int(s[8:10]))
        if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", s):
            return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    raise SimError(f"date argument {v!r}")


def _add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    last = (date(y + (m == 12), 1 if m == 12 else m + 1, 1) - timedelta(days=1)).day
    return d.replace(year=y, month=m, day=min(d.day, last))


def make_func(name, args):
    n = name.lower()
    A = args

    def a(i, c):
        return A[i](c)
    if n == "iif":
        return lambda c: A[1](c) if _bool(A[0](c)) else A[2](c)
    if n == "isnull":
        return lambda c: a(0, c) is None
    if n == "isempty":
        return lambda c: a(0, c) is None or a(0, c) == ""
    if n == "null":
        return lambda c: None
    if n == "trim":
        def f(c):
            x = a(0, c)
            if x is None:
                return None
            if not isinstance(x, str):
                raise SimError("Trim of non-string")
            return x.strip()
        return f
    if n in ("lowercase",):
        def f(c):
            x = a(0, c)
            if x is None:
                return None
            if not isinstance(x, str):
                raise SimError(f"{name} of non-string")
            return x.lower()
        return f
    if n == "regex_replace":
        def f(c):
            x = a(0, c)
            if x is None:
                return None
            if not isinstance(x, str):
                raise SimError(f"{name} of non-string")
            # Alteryx (Boost) replacement text: $1 / \1 refer to groups (v5.10: ICD code split)
            return re.sub(a(1, c), re.sub(r"\$(\d)", r"\\g<\1>", a(2, c)), x)
        return f
    if n == "findstring":
        def f(c):
            x, y = a(0, c), a(1, c)
            if x is None or y is None:
                raise SimError("FindString on Null")
            return float(x.find(y))
        return f
    if n in ("uppercase", "titlecase"):
        def f(c):
            x = a(0, c)
            if x is None:
                return None
            if not isinstance(x, str):
                raise SimError(f"{name} of non-string")
            return x.upper() if n == "uppercase" else x.title()
        return f
    if n == "charfromint":
        return lambda c: None if a(0, c) is None else chr(int(a(0, c)))
    if n == "replace":
        return lambda c: None if a(0, c) is None else a(0, c).replace(a(1, c), a(2, c))
    if n == "left":
        return lambda c: None if a(0, c) is None else a(0, c)[:int(a(1, c))]
    if n == "right":
        return lambda c: None if a(0, c) is None else a(0, c)[-int(a(1, c)):] if int(a(1, c)) else ""
    if n == "length":
        return lambda c: 0 if a(0, c) is None else len(a(0, c))
    if n == "regex_match":
        return lambda c: None if a(0, c) is None else re.fullmatch(a(1, c), a(0, c)) is not None
    if n == "tonumber":
        def f(c):
            x = a(0, c)
            if x is None:
                return None
            if _num(x):
                return float(x)
            try:
                return float(x)
            except ValueError:
                raise SimError(f"ToNumber({x!r}) would be a conversion error")
        return f
    if n == "tostring" and len(A) == 2:          # ToString(x, decimals): fixed decimals (Designer rounds half away)
        def f(c):
            x, k = a(0, c), a(1, c)
            if x is None:
                return None
            if not _num(x):
                raise SimError(f"ToString of {x!r}")
            return f"{x:.{int(k)}f}"
        return f
    if n == "tostring":
        def f(c):
            x = a(0, c)
            if x is None:
                return None
            if isinstance(x, bool) or not (isinstance(x, int) or (isinstance(x, float) and x.is_integer())):
                raise SimError(f"ToString of non-integer {x!r}")
            return str(int(x))
        return f
    if n == "todate":
        return lambda c: _todate(a(0, c))
    if n == "datetimeadd":
        def f(c):
            d, k, u = _dt(a(0, c)), a(1, c), a(2, c).lower()
            if d is None or k is None:
                return None
            if not isinstance(k, int) and not (isinstance(k, float) and k.is_integer()):
                raise SimError("DateTimeAdd by a fraction")
            k = int(k)
            if u == "days":
                return d + timedelta(days=k)
            if u == "months":
                return _add_months(d, k)
            raise SimError(u)
        return f
    if n == "datetimediff":
        def f(c):
            x, y, u = _dt(a(0, c)), _dt(a(1, c)), a(2, c).lower()
            if x is None or y is None:
                return None
            if u == "months":      # whole months; the generator only diffs first-of-month dates
                if x.day != 1 or y.day != 1:
                    raise SimError("DateTimeDiff months on a date that is not a month start")
                return (x.year - y.year) * 12 + x.month - y.month
            if u != "days":
                raise SimError(u)
            return (x.date() - y.date()).days
        return f
    if n == "datetimetrim":
        def f(c):
            d, u = _dt(a(0, c)), a(1, c).lower()
            if d is None:
                return None
            if u == "firstofmonth":
                return datetime(d.year, d.month, 1)
            raise SimError(u)
        return f
    if n == "datetimeformat":
        def f(c):
            d, fm = _dt(a(0, c)), a(1, c)
            return None if d is None else d.strftime(fm)
        return f
    if n == "datetimeparse":
        def f(c):
            s, fm = a(0, c), a(1, c)
            if s is None:
                return None
            try:
                return datetime.strptime(s.strip(), fm)
            except ValueError:
                return None
        return f
    if n in ("datetimeyear", "datetimemonth", "datetimeday"):
        attr = n[8:]
        return lambda c: None if _dt(a(0, c)) is None else getattr(_dt(a(0, c)), attr)
    if n == "sqrt":             # XOL: Designer Sqrt(x)
        def sq(c):
            x = a(0, c)
            if x is None:
                return None
            if x < 0:
                raise SimError("Sqrt of a negative number")
            return math.sqrt(x)
        return sq
    if n == "floor":
        return lambda c: None if a(0, c) is None else int(math.floor(a(0, c)))
    if n == "abs":
        return lambda c: None if a(0, c) is None else abs(a(0, c))
    if n == "round":            # Round(x, mult): nearest multiple of mult, halves away from zero
        def rnd(c):
            x, m = a(0, c), a(1, c)
            if x is None or m is None:
                return None
            return math.copysign(math.floor(abs(x) / m + 0.5) * m, x)
        return rnd
    raise SimError(f"function {name} not supported")


_cache = {}


def compile_expr(s):
    if s not in _cache:
        _cache[s] = Parser(s).parse()
    return _cache[s]


# ============================================================================================== tables
class Table:
    def __init__(self, fields, rows):
        self.fields = dict(fields)   # name -> type
        self.rows = rows
        low = {}
        for n in self.fields:
            if n.lower() in low:
                raise SimError(f"fields {low[n.lower()]!r} and {n!r} differ only by case (Designer treats them as one)")
            low[n.lower()] = n


def coerce(v, typ, where):
    if v is None:
        return None
    if typ == "Date":
        if isinstance(v, datetime):
            return v.date()
        if isinstance(v, date):
            return v
        if isinstance(v, str):
            d = _todate(v)
            if d is None:
                raise SimError(f"{where}: '{v}' is not a date")
            return d
        raise SimError(f"{where}: {type(v).__name__} into Date")
    if typ == "DateTime":
        return _dt(v)
    if typ in STRS:
        if not isinstance(v, str):
            raise SimError(f"{where}: {type(v).__name__} {v!r} into string")
        return v
    if typ == "Double":
        if isinstance(v, bool) or not _num(v):
            raise SimError(f"{where}: {v!r} into Double")
        return float(v)
    if typ in INTS:
        if isinstance(v, bool):
            raise SimError(f"{where}: bool into int")
        if isinstance(v, float):
            if not v.is_integer():
                raise SimError(f"{where}: {v} into int")
            return int(v)
        if isinstance(v, int):
            return v
        raise SimError(f"{where}: {v!r} into int")
    if typ == "Bool":
        if isinstance(v, bool):
            return v
        raise SimError(f"{where}: {v!r} into Bool")
    raise SimError(f"type {typ}")


def merge_type(a, b, where):
    if a == b:
        return a
    if a in STRS and b in STRS:
        return "V_WString"
    if a in NUMS and b in NUMS:
        return "Int64" if (a in INTS and b in INTS) else "Double"
    raise SimError(f"{where}: union of {a} and {b}")


def to_text(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    return str(v)


def union_type(a, b, where):
    """Union only: text meeting a number or date becomes text (verified in Designer 2025.2, 2026-10-08)."""
    if (a in STRS) != (b in STRS):
        return "V_WString"
    return merge_type(a, b, where)


# ============================================================================================== tools
def text_input(cfg):
    names = [f.get("name") for f in cfg.find("Fields")]
    rows = []
    for r in cfg.find("Data"):
        vals = [(c.text or "") for c in r]
        rows.append({n: (v if v != "" else None) for n, v in zip(names, vals)})
    # Designer types each Text Input column from its values (a column of whole numbers becomes a number: found in
    # Designer 2025.2 on 2026-10-08, a join of "1" with a string key failed); the simulator does the same
    types = {}
    for n in names:
        vals = [r[n] for r in rows if r[n] is not None]
        if vals and all(re.fullmatch(r"-?\d+", v) for v in vals):
            types[n] = "Int64"
            for r in rows:
                r[n] = int(r[n]) if r[n] is not None else None
        elif vals and all(re.fullmatch(r"-?\d*\.\d+|-?\d+", v) for v in vals):
            types[n] = "Double"
            for r in rows:
                r[n] = float(r[n]) if r[n] is not None else None
        else:
            types[n] = "V_String"
    return Table(types, rows)


def _path(path, base):
    """Designer resolves a relative path from the workflow's folder; separators are Windows-style."""
    q = Path(path.replace("\\", "/"))
    return q if q.is_absolute() else base / q


def input_csv(cfg, base):
    """Designer CSV input: every field text; an empty value is read as an empty string here (the generator tests
    emptiness with IsEmpty, which holds for an empty string and for Null, so Designer's choice does not matter)."""
    p = _path(cfg.find("File").text, base)
    with open(p, encoding="utf-8-sig", newline="") as f:
        rd = csv.reader(f)
        head = next(rd)
        rows = [dict(zip(head, r)) for r in rd]
    return Table({h: "V_String" for h in head}, rows)


def input_xlsx(cfg, base, spec=None):
    from openpyxl import load_workbook
    spec = spec or cfg.find("File").text
    path, sheet = spec.split("|||")
    sheet = sheet.strip("`").rstrip("$")
    p = _path(path, base)
    if not p.exists():
        raise SimError(f"input file not found: {p}")
    wb = load_workbook(p, read_only=True, data_only=True)
    if sheet not in wb.sheetnames:
        raise SimError(f"sheet {sheet!r} not found in {p.name}")
    ws = wb[sheet]
    it = ws.iter_rows(values_only=True)
    for _ in range(int(cfg.find("FormatSpecificOptions/ImportLine").text) - 1):
        next(it)
    try:
        head = next(it)
    except StopIteration:
        return Table({}, [])
    names = [str(h).strip() if h is not None else f"F{i + 1}" for i, h in enumerate(head)]
    lim = (cfg.find("File").get("RecordLimit") or "").strip()
    raw = [dict(zip(names, r)) for r in it]
    if lim:
        raw = raw[:int(lim)]
    wb.close()
    while raw and all(v is None for v in raw[-1].values()):
        raw.pop()
    types = {}
    for n in names:
        vals = [r.get(n) for r in raw if r.get(n) is not None]
        if vals and all(isinstance(v, datetime) for v in vals):
            types[n] = "DateTime"
        elif vals and all(_num(v) for v in vals):
            types[n] = "Double"
        else:
            types[n] = "V_WString"
    rows = []
    for r in raw:
        o = {}
        for n in names:
            v = r.get(n)
            if types[n] == "V_WString":
                v = to_text(v) if v is not None else None
            elif types[n] == "Double" and v is not None:
                v = float(v)
            o[n] = v
        rows.append(o)
    return Table(types, rows)


def directory(cfg, base):
    """Directory tool: files under Directory matching FileSpec (case-insensitive, as Windows), sorted by path."""
    import fnmatch
    root = _path(cfg.find("Directory").text, base)
    spec = cfg.find("FileSpec").text.lower()
    sub = cfg.find("IncludeSubDirs").get("value") == "True"
    files = sorted(x for x in (root.rglob("*") if sub else root.glob("*"))
                   if x.is_file() and fnmatch.fnmatch(x.name.lower(), spec))
    rows = [{"FullPath": str(x), "Directory": str(x.parent), "FileName": x.name, "ShortFileName": x.stem,
             "Size": x.stat().st_size} for x in files]
    return Table({"FullPath": "V_WString", "Directory": "V_WString", "FileName": "V_WString",
                  "ShortFileName": "V_WString", "Size": "Int64"}, rows)


def dynamic_input(cfg, t, base):
    """Dynamic Input, ReadList / Path: each record's field is a full spec; '<List of Sheet Names>' lists sheets
    (field 'Sheet Names'), else the sheet is read with the template's format options. No records: the template's
    schema for a sheet list, none for data (the raw-column template union downstream supplies the columns)."""
    from openpyxl import load_workbook
    if cfg.find("Mode").text != "ReadList" or cfg.find("ReadList_Type").text != "Path":
        raise SimError("Dynamic Input: only ReadList / Path is simulated")
    fld = cfg.find("ReadList_Field").text
    icfg = cfg.find("InputConfiguration/Configuration")
    listing = "<List of Sheet Names>" in icfg.find("File").text
    out = None
    for r in t.rows:
        spec = r[fld]
        if spec is None:
            raise SimError("Dynamic Input: Null path")
        if listing:
            path = _path(spec.split("|||")[0], base)
            if not path.exists():
                raise SimError(f"Dynamic Input: file not found {path}")
            wb = load_workbook(path, read_only=True)
            res = Table({"Sheet Names": "V_WString"}, [{"Sheet Names": n} for n in wb.sheetnames])
            wb.close()
        else:
            res = input_xlsx(icfg, base, spec)
        if out is None:
            out = res
        else:
            if list(res.fields) != list(out.fields):
                raise SimError("Dynamic Input: files with different fields")
            out = Table(out.fields, out.rows + res.rows)
    if out is None:
        out = Table({"Sheet Names": "V_WString"} if listing else {}, [])
    return out


def field_info(t):
    rows = [{"Name": n, "Type": ty, "Size": None, "Scale": None, "Source": None, "Description": None}
            for n, ty in t.fields.items()]
    return Table({"Name": "V_WString", "Type": "V_WString", "Size": "Int32", "Scale": "Int32", "Source": "V_WString",
                  "Description": "V_WString"}, rows)


MESSAGES = []   # (type, text) of Message tools; reset by run()


def message(cfg, t):
    expr = compile_expr(cfg.find("MessageExpression").text)
    kind = cfg.find("Type").text
    for r in t.rows:
        MESSAGES.append((kind, expr(Ctx(r))))
    return t


def formula(cfg, t):
    fields = dict(t.fields)
    specs = []
    for ff in cfg.find("FormulaFields"):
        name, typ = ff.get("field"), ff.get("type")
        if name in fields:
            typ = fields[name]
        fields[name] = typ
        specs.append((name, typ, compile_expr(ff.get("expression")), ff.get("expression")))
    rows = []
    for r in t.rows:
        o = dict(r)
        c = Ctx(o)
        for name, typ, fn, src in specs:
            try:
                o[name] = coerce(fn(c), typ, f"Formula {name}")
            except SimError as e:
                raise SimError(f"{e}\n  field {name}: {src[:300]}\n  row: { {k: o[k] for k in list(o)[:60]} }")
        rows.append(o)
    return Table(fields, rows)


def filter_(cfg, t):
    fn = compile_expr(cfg.find("Expression").text)
    tr, fa = [], []
    for r in t.rows:
        try:
            (tr if _bool(fn(Ctx(r))) else fa).append(r)
        except SimError as e:
            raise SimError(f"{e}\n  filter: {cfg.find('Expression').text[:300]}\n  row: {r}")
    return {"True": Table(t.fields, tr), "False": Table(t.fields, fa)}


def select(cfg, t, label=""):
    sfs = list(cfg.find("SelectFields"))
    unknown = True
    spec = {}
    for s in sfs:
        if s.get("field") == "*Unknown":
            unknown = s.get("selected") == "True"
        else:
            spec[s.get("field")] = s
    # Designer: a Select that names a field absent from its input warns once per field
    WARNINGS.extend((label, f'The field "{n}" is missing. Compare the tool configuration with the input stream.')
                    for n in spec if n not in t.fields)
    fields, plan = {}, []
    for n, ty in t.fields.items():
        s = spec.get(n)
        if s is None:
            if unknown:
                fields[n] = ty
                plan.append((n, n, ty, ty))
            continue
        if s.get("selected") != "True":
            continue
        new = s.get("rename") or n
        nt = s.get("type") or ty
        if new in fields:
            raise SimError(f"Select: duplicate output field {new}")
        fields[new] = nt
        plan.append((n, new, ty, nt))
    rows = []
    for r in t.rows:
        o = {}
        for n, new, ty, nt in plan:
            v = r.get(n)
            if nt != ty:
                if nt in STRS:
                    v = to_text(v)
                elif nt == "Date":
                    v = _todate(v) if not isinstance(v, str) else _todate(v)
                elif nt in NUMS:
                    v = None if v is None else coerce(float(v) if isinstance(v, str) else v, nt, f"Select {n}")
                else:
                    raise SimError(f"Select cast {ty}->{nt}")
            o[new] = v
        rows.append(o)
    return Table(fields, rows)


def join(cfg, left, right):
    ji = cfg.findall("JoinInfo")
    lk = [f.get("field") for f in ji[0]]
    rk = [f.get("field") for f in ji[1]]
    for a, b in zip(lk, rk):
        ta, tb = left.fields[a], right.fields[b]
        if (ta in STRS) != (tb in STRS):
            raise SimError(f"Join: {a} ({ta}) with {b} ({tb}): string fields can only be joined to string fields")
    lowl = {n.lower() for n in left.fields}
    for n in right.fields:
        if n.lower() in lowl:
            raise SimError(f"Join: right field {n} clashes with the left (would become Right_{n})")
    idx = {}
    for i, r in enumerate(right.rows):
        k = tuple(r[f] for f in rk)
        if any(v is None for v in k):
            raise SimError(f"Join: Null right key {rk}")
        idx.setdefault(k, []).append(i)
    J, L, used = [], [], set()
    for r in left.rows:
        k = tuple(r[f] for f in lk)
        if any(v is None for v in k):
            raise SimError(f"Join: Null left key {lk}")
        hits = idx.get(k)
        if hits:
            for i in hits:
                J.append({**r, **right.rows[i]})
                used.add(i)
        else:
            L.append(r)
    R = [r for i, r in enumerate(right.rows) if i not in used]
    return {"Join": Table({**left.fields, **right.fields}, J), "Left": Table(left.fields, L),
            "Right": Table(right.fields, R)}


WARNINGS = []   # (tool label, message) as Designer reports them; reset by run()


def union(cfg, ins, label=""):
    fields = {}
    for t in ins:
        for n, ty in t.fields.items():
            fields[n] = union_type(fields[n], ty, f"Union field {n}") if n in fields else ty
    mode = cfg.findtext("ByName_ErrorMode") if cfg is not None else "Warning"
    if mode != "Ignore":     # Designer: 'The field "x" is not present in all inputs.' once per field
        WARNINGS.extend((label, f'The field "{n}" is not present in all inputs.')
                        for n in fields if any(n not in t.fields for t in ins))
    rows = []
    for t in ins:
        for r in t.rows:
            o = {n: None for n in fields}
            for n, v in r.items():
                ft = fields[n]
                if v is not None and ft == "Double" and isinstance(v, int):
                    v = float(v)
                elif ft in STRS and t.fields[n] not in STRS:
                    v = to_text(v)
                o[n] = v
            rows.append(o)
    return Table(fields, rows)


def summarize(cfg, t, label=""):
    specs = [(s.get("field"), s.get("action"), s.get("rename")) for s in cfg.find("SummarizeFields")]
    gb = [f for f, a, _ in specs if a == "GroupBy"]
    # Designer 2026.2 (laptop, 2026-10-11): "Group By on Double or Float are not recommended due to rounding error."
    WARNINGS.extend((label, f"Group By on Double or Float ({f})") for f in gb
                    if f in t.fields and t.fields[f] in ("Double", "Float"))
    groups = {}
    for r in t.rows:
        groups.setdefault(tuple(r[f] for f in gb), []).append(r)
    fields = {}
    for f, a, rn in specs:
        if f not in t.fields:
            raise SimError(f"Summarize: unknown field {f}")
        ty = t.fields[f]
        fields[rn] = {"Sum": "Double", "Count": "Int64", "CountDistinct": "Int64", "CountNonNull": "Int64"}.get(a, ty)
    rows = []
    for k in sorted(groups, key=lambda k: tuple((v is not None, v) for v in k)):
        g = groups[k]
        o = {}
        for f, a, rn in specs:
            vals = [r[f] for r in g]
            nn = [v for v in vals if v is not None]
            if a == "GroupBy":
                o[rn] = vals[0]
            elif a == "Sum":
                o[rn] = float(sum(nn)) if nn else None
            elif a == "Min":
                o[rn] = min(nn) if nn else None
            elif a == "Max":
                o[rn] = max(nn) if nn else None
            elif a == "First":
                o[rn] = vals[0]
            elif a == "Last":
                o[rn] = vals[-1]
            elif a == "Count":
                o[rn] = len(vals)
            elif a == "CountNonNull":
                o[rn] = len(nn)
            elif a == "CountDistinct":
                o[rn] = len(set(vals))
            else:
                raise SimError(a)
        rows.append(o)
    return Table(fields, rows)


def _sortkey(v):
    return (0,) if v is None else (1, v)


def sort(cfg, t):
    rows = list(t.rows)
    for f in reversed(list(cfg.find("SortInfo"))):
        n, desc = f.get("field"), f.get("order") == "Descending"
        if n not in t.fields:
            raise SimError(f"Sort: unknown field {n}")
        rows.sort(key=lambda r: _sortkey(r[n]), reverse=desc)
    return Table(t.fields, rows)


def unique(cfg, t):
    fs = [f.get("field") for f in cfg.find("UniqueFields")]
    for f in fs:
        if f not in t.fields:
            raise SimError(f"Unique: unknown field {f}")
    seen, u, d = set(), [], []
    for r in t.rows:
        k = tuple(r[f] for f in fs)
        (d if k in seen else u).append(r)
        seen.add(k)
    return {"Unique": Table(t.fields, u), "Duplicates": Table(t.fields, d)}


def multirow(cfg, t):
    name = cfg.find("CreateField_Name").text
    typ = cfg.find("CreateField_Type").text
    other = cfg.find("OtherRows").text
    gbe = cfg.find("GroupByFields")
    gb = [g.get("field") for g in gbe] if gbe is not None else []
    fn = compile_expr(cfg.find("Expression").text)
    fields = {**t.fields, name: typ}

    def empty_row():
        if other == "NULL":
            return {n: None for n in fields}
        return {n: ("" if ty in STRS else 0 if ty in NUMS else None) for n, ty in fields.items()}
    rows = [dict(r) for r in t.rows]
    for i, r in enumerate(rows):
        r[name] = None
    groups = {}                     # Group By: Row-1 / Row+1 are the neighbours inside the group, record order kept
    for i, r in enumerate(rows):
        groups.setdefault(tuple(r.get(g) for g in gb), []).append(i)
    pos_prev, pos_next = {}, {}
    for idx in groups.values():
        for a, b in zip(idx, idx[1:]):
            pos_next[a], pos_prev[b] = b, a
    for i, r in enumerate(rows):
        prev = rows[pos_prev[i]] if i in pos_prev else empty_row()
        nxt = rows[pos_next[i]] if i in pos_next else empty_row()
        try:
            r[name] = coerce(fn(Ctx(r, prev, nxt)), typ, f"Multi-Row {name}")
        except SimError as e:
            raise SimError(f"{e}\n  multirow {name}: {cfg.find('Expression').text[:300]}\n  row {r}")
    return Table(fields, rows)


def generate_rows(cfg, t):
    name = cfg.find("CreateField_Name").text
    typ = cfg.find("CreateField_Type").text
    init = compile_expr(cfg.find("Expression_Init").text)
    cond = compile_expr(cfg.find("Expression_Cond").text)
    loop = compile_expr(cfg.find("Expression_Loop").text)
    out = []
    for r in t.rows:
        o = dict(r)
        o[name] = coerce(init(Ctx(o)), typ, "GenerateRows init")
        n = 0
        while _bool(cond(Ctx(o))):
            out.append(dict(o))
            o[name] = coerce(loop(Ctx(o)), typ, "GenerateRows loop")
            n += 1
            if n > 10000:
                raise SimError("Generate Rows runaway")
    return Table({**t.fields, name: typ}, out)


def record_id(cfg, t):
    name = cfg.find("FieldName").text
    start = int(cfg.find("StartValue").text)
    rows = [{name: start + i, **r} for i, r in enumerate(t.rows)]
    return Table({name: cfg.find("FieldType").text, **t.fields}, rows)


def _yxdb(cfg):
    return cfg.find("File").get("FileFormat") == "19"


def input_yxdb(cfg, base):
    """.yxdb stand-in: the simulator stores a written table (field types + values) with pickle under the same name;
    Designer's .yxdb is likewise lossless. Never read a Designer-written .yxdb with the simulator."""
    import pickle
    p = _path(cfg.find("File").text, base)
    with open(p, "rb") as f:
        fields, rows = pickle.load(f)
    return Table(fields, rows)


def output_yxdb(cfg, t, base):
    import pickle
    p = _path(cfg.find("File").text, base)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as f:
        pickle.dump((dict(t.fields), [dict(r) for r in t.rows]), f)
    return p


def output_csv(cfg, t, base):
    path = cfg.find("File").text
    p = _path(path, base)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(list(t.fields))
        for r in t.rows:
            w.writerow(["" if r[n] is None else to_text(r[n]) if not isinstance(r[n], float)
                        else repr(r[n]) for n in t.fields])
    return p


def output_parquet(cfg, t, base):
    """Parquet output (Designer FileFormat 79, AMP engine; v5.14). Typed columns: Double -> double, Int -> int64,
    Date -> date32, DateTime -> timestamp, Bool -> bool, text -> string. 0 records still write a file (Designer
    offers no 'suppress if empty' option for Parquet)."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    p = _path(cfg.find("File").text, base)
    p.parent.mkdir(parents=True, exist_ok=True)
    pt = {"Double": pa.float64(), "Float": pa.float64(), "Int16": pa.int64(), "Int32": pa.int64(), "Int64": pa.int64(),
          "Byte": pa.int64(), "Date": pa.date32(), "DateTime": pa.timestamp("s"), "Bool": pa.bool_()}
    cols = {n: pa.array([r[n] for r in t.rows], type=pt.get(ty, pa.string())) for n, ty in t.fields.items()}
    pq.write_table(pa.table(cols) if cols else pa.table({}), p)
    return p


def _xlsx_target(cfg, base):
    text = cfg.find("File").text
    path, _, sheet = text.partition("|||")
    return _path(path, base), sheet.strip("`").rstrip("$")


def output_xlsx(cfg, t, base):
    """Excel output, one sheet (Designer FileFormat 25). OutputOption RemoveFile = Overwrite File (Remove): a new
    workbook; Overwrite = Overwrite Sheet (Drop): the sheet is replaced (re-created after the existing sheets),
    other sheets are kept, a missing file is created. Header row = field names; numbers, dates and text typed."""
    from openpyxl import Workbook, load_workbook
    p, sheet = _xlsx_target(cfg, base)
    opt = cfg.findtext("FormatSpecificOptions/OutputOption")
    if opt not in ("RemoveFile", "Overwrite"):
        raise SimError(f"xlsx OutputOption {opt!r} not used by the generator")
    if len(sheet) > 31 or any(ch in sheet for ch in "[]:*?/\\"):
        raise SimError(f"xlsx sheet name {sheet!r} not valid in Excel")
    if len(t.rows) >= 1048576:
        raise SimError(f"xlsx sheet {sheet}: {len(t.rows)} rows exceed an Excel sheet")
    p.parent.mkdir(parents=True, exist_ok=True)
    if opt == "Overwrite" and p.exists():
        wb = load_workbook(p)
        if sheet in wb.sheetnames:
            del wb[sheet]
    else:
        wb = Workbook()
        wb.remove(wb.active)
    ws = wb.create_sheet(sheet)
    ws.append(list(t.fields))
    for r in t.rows:
        row = []
        for n, ty in t.fields.items():
            v = r[n]
            if isinstance(v, datetime) and ty == "Date":
                v = v.date()
            row.append(v)
        ws.append(row)
    wb.save(p)
    return p


# ============================================================================================== runner
def run(path, verbose=False, trace=None):
    """trace: optional dict filled with {tool_id: {anchor: [input field names]}} (alteryx/audit.py)."""
    path = Path(path)
    base = path.parent
    WARNINGS.clear()
    MESSAGES.clear()
    root = ET.parse(path).getroot()
    nodes = {}
    for n in root.iter("Node"):
        plugin = n.find("GuiSettings").get("Plugin")
        if plugin.endswith("ToolContainer") or plugin.endswith("TextBox"):     # containers, Comment notes: no data
            continue
        cfg = n.find("Properties/Configuration")
        nodes[n.get("ToolID")] = (plugin.split(".")[-1], cfg, n)
    ins = {k: [] for k in nodes}
    for c in root.find("Connections"):
        o, d = c.find("Origin"), c.find("Destination")
        ins[d.get("ToolID")].append((d.get("Connection"), o.get("ToolID"), o.get("Connection"), c.get("name")))
    done, outputs, written = {}, {}, []

    def get(tid, anchor):
        if tid not in done:
            evaluate(tid)
        r = done[tid]
        return r[anchor] if isinstance(r, dict) else r

    def evaluate(tid):
        kind, cfg, node = nodes[tid]
        conns = ins[tid]
        label = node.find("Properties/Annotation/Name")
        label = label.text if label is not None else ""
        try:
            if kind == "TextInput":
                res = text_input(cfg)
            elif kind == "DbFileInput":
                ff = cfg.find("File").get("FileFormat")
                res = input_yxdb(cfg, base) if _yxdb(cfg) else input_csv(cfg, base) if ff == "0" else input_xlsx(cfg, base)
            elif kind == "Directory":
                res = directory(cfg, base)
            else:
                src = {}
                for anchor, o, oa, name in conns:
                    src.setdefault(anchor, []).append((name, get(o, oa)))
                one = (lambda a="Input": src[a][0][1])
                if trace is not None:
                    trace[tid] = {a: list(dict.fromkeys(f for _, t in v for f in t.fields)) for a, v in src.items()}
                if kind == "Formula":
                    res = formula(cfg, one())
                elif kind == "Filter":
                    res = filter_(cfg, one())
                elif kind == "AlteryxSelect":
                    res = select(cfg, one(), label)
                elif kind == "Join":
                    res = join(cfg, one("Left"), one("Right"))
                elif kind == "Union":
                    lst = sorted(src["Input"], key=lambda x: int((x[0] or "#0")[1:]))
                    if len(lst) > 32:       # Designer 2026.2 (laptop, 2026-10-11): "32 or fewer" incoming connections
                        raise SimError(f"Union with {len(lst)} inputs: Designer allows 32 or fewer")
                    res = union(cfg, [t for _, t in lst], label)
                elif kind == "Summarize":
                    res = summarize(cfg, one(), label)
                elif kind == "Sort":
                    res = sort(cfg, one())
                elif kind == "Unique":
                    res = unique(cfg, one())
                elif kind == "MultiRowFormula":
                    res = multirow(cfg, one())
                elif kind == "GenerateRows":
                    res = generate_rows(cfg, one())
                elif kind == "RecordID":
                    res = record_id(cfg, one())
                elif kind == "DbFileOutput":
                    ff = cfg.find("File").get("FileFormat")
                    written.append((output_yxdb if _yxdb(cfg) else output_xlsx if ff == "25" else
                                    output_parquet if ff == "79" else output_csv)(cfg, one(), base))
                    res = one()
                elif kind == "DynamicInput":
                    res = dynamic_input(cfg, one(), base)
                elif kind == "FieldInfo":
                    res = field_info(one())
                elif kind == "Message":
                    res = message(cfg, one())
                elif kind == "BlockUntilDone":      # sheets downstream run in tool order (= Output1, 2, 3 order)
                    res = {a: one() for a in ("Output", "Output2", "Output3")}   # Designer anchor names
                else:
                    raise SimError(f"tool {kind} not supported")
        except SimError as e:
            raise SimError(f"tool {tid} {kind} '{label}': {e}") from None
        done[tid] = res
        if verbose:
            t = res if isinstance(res, Table) else next(iter(res.values()))
            print(f"  {tid:>4} {kind:<16} {label[:40]:<40} {len(t.rows)}")

    sys.setrecursionlimit(20000)
    for tid in nodes:
        if tid not in done:
            evaluate(tid)
    return written


if __name__ == "__main__":
    for p in run(sys.argv[1], verbose="-v" in sys.argv):
        print("wrote", p)
