# Vendored from mtamsyed-gc/ete-dashboard side_tools/interim_pipeline/alteryx/xmlkit.py @ 412a36c (2026-10-14).
# Local changes are marked 'XOL:'.
"""Minimal writer for Alteryx Designer workflows (.yxmd, yxmdVer 2025.2, AMP). Tool XML follows the patterns of
the team's own 2025.2 workflows (configuration syntax only)."""
from xml.sax.saxutils import escape, quoteattr

STR, DATE, DBL, INT, INT64, BOOL = "V_WString", "Date", "Double", "Int32", "Int64", "Bool"
SIZE = {STR: 1073741823, DATE: 10, DBL: 8, INT: 4, INT64: 8, BOOL: 1}
BASE = "AlteryxBasePluginsGui"


def q(x):
    return quoteattr(str(x))


def e(x):
    return escape(str(x))


# ---- expression helpers (Alteryx formula language)
def F(name):
    return f"[{name}]"


def coal(*xs):
    """First non-null of the expressions."""
    out = xs[-1]
    for x in reversed(xs[:-1]):
        out = f"IIF(IsNull({x}), {out}, {x})"
    return out


def dmin(a, b):
    return f"IIF({a} <= {b}, {a}, {b})"


def dmax(a, b):
    return f"IIF({a} >= {b}, {a}, {b})"


def dadd(d, n):
    return f'ToDate(DateTimeAdd({d}, {n}, "days"))'


def ddiff(a, b):
    return f'DateTimeDiff({a}, {b}, "days")'


def mstart(d):
    return f'ToDate(DateTimeTrim({d}, "firstofmonth"))'


def lit(s):
    if '"' in str(s):
        raise ValueError(f"double quote in literal: {s!r}")
    return '"' + str(s) + '"'


def isin(field, values):
    return f"{F(field)} IN (" + ", ".join(lit(v) for v in values) + ")"


class WF:
    def __init__(self, name, description):
        self.name, self.description = name, description
        self.nodes, self.conns = [], []
        self.containers = []
        self.cur = None
        self.next_id = 1
        self.constants = []

    def constant(self, name, value):
        self.constants.append((name, value))

    def container(self, caption):
        cid = self.next_id
        self.next_id += 1
        c = {"id": cid, "caption": caption, "children": [], "col": len(self.containers)}
        self.containers.append(c)
        self.cur = c
        return cid

    def _add(self, plugin, entry, config, label, dll="AlteryxBasePluginsEngine.dll"):
        tid = self.next_id
        self.next_id += 1
        self.cur["children"].append({"id": tid, "plugin": plugin, "entry": entry, "config": config, "label": label,
                                     "dll": dll})
        return tid

    def connect(self, src, dst, dst_anchor="Input", name=None):
        """src: (tool_id, anchor) or tool_id (anchor Output)."""
        if not isinstance(src, tuple):
            src = (src, "Output")
        self.conns.append((src[0], src[1], dst, dst_anchor, name))

    # ---------------------------------------------------------------- tools
    def text_input(self, fields, rows, label=""):
        f = "".join(f'<Field name={q(x)} />' for x in fields)
        d = "".join("<r>" + "".join(f"<c>{e('' if v is None else v)}</c>" for v in r) + "</r>" for r in rows)
        cfg = f'<NumRows value="{len(rows)}" /><Fields>{f}</Fields><Data>{d}</Data>'
        return self._add(f"{BASE}.TextInput.TextInput", "AlteryxTextInput", cfg, label)

    def input_xlsx(self, path, sheet, header_row=1, label=""):
        cfg = (f'<Passwords /><File OutputFileName="" RecordLimit="" SearchSubDirs="False" FileFormat="25">'
               f'{e(path)}|||`{e(sheet)}$`</File><FormatSpecificOptions><FirstRowData>False</FirstRowData>'
               f'<ImportLine>{int(header_row)}</ImportLine></FormatSpecificOptions>')
        return self._add(f"{BASE}.DbFileInput.DbFileInput", "AlteryxDbFileInput", cfg, label)

    def directory(self, folder, spec="*.xlsx", subdirs=True, label=""):
        """Directory tool (v5.11): every file under folder matching spec. Fields FullPath, Directory, FileName,
        ShortFileName, CreationTime, LastAccessTime, LastWriteTime, Size, Attributes (Alteryx sample Dynamic_Input)."""
        cfg = (f'<Directory>{e(folder)}</Directory><FileSpec>{e(spec)}</FileSpec>'
               f'<IncludeSubDirs value="{subdirs}" />')
        return self._add(f"{BASE}.Directory.Directory", "AlteryxDirectory", cfg, label)

    def field_info(self, src, label=""):
        """Field Info (v5.11): one record per field of the input (Name, Type, Size, Scale, Source, Description)."""
        t = self._add(f"{BASE}.FieldInfo.FieldInfo", "AlteryxFieldInfo", "", label)
        self.connect(src, t)
        return t

    def dynamic_input_xlsx(self, src, field, template, sheet=None, header_row=1, label="", record_limit=""):
        """Dynamic Input (v5.11), mode ReadList / Path: each record's [field] is a full Excel spec, "<path>|||`Sheet$`"
        or "<path>|||<List of Sheet Names>" (sheet=None: the template lists sheet names, field 'Sheet Names'). The
        template only carries the format options (header row); XML as Alteryx's sample Dynamic_Input.yxmd."""
        spec = f"{e(template)}|||" + ("&lt;List of Sheet Names&gt;" if sheet is None else f"`{e(sheet)}$`")
        opts = ("<FormatSpecificOptions />" if sheet is None else
                f"<FormatSpecificOptions><FirstRowData>False</FirstRowData><ImportLine>{int(header_row)}</ImportLine>"
                "</FormatSpecificOptions>")
        cfg = (f'<InputConfiguration><Configuration><Passwords /><File OutputFileName="" RecordLimit="{record_limit}" '
               f'SearchSubDirs="False" FileFormat="25">{spec}</File>{opts}</Configuration></InputConfiguration>'
               f'<Mode>ReadList</Mode><ReadList_Field>{e(field)}</ReadList_Field><ReadList_Type>Path</ReadList_Type>')
        t = self._add(f"{BASE}.DynamicInput.DynamicInput", "AlteryxDynamicInput", cfg, label)
        self.connect(src, t)
        return t

    def message(self, src, expr, kind="Error", label=""):
        """Message tool (v5.11): one message per record (Type Error shows red in the Results window)."""
        cfg = (f'<When>All</When><Filter /><Type>{kind}</Type><Priority>High</Priority><Transient>False</Transient>'
               f'<MessageExpression>{e(expr)}</MessageExpression>')
        t = self._add(f"{BASE}.Message.Message", "AlteryxMessage", cfg, label)
        self.connect(src, t)
        return t

    def input_csv(self, path, label=""):
        """Comma-separated file with a header row, every field read as text (V_String, as the team's CSV inputs);
        UTF-8 (code page 65001, the BOM the workflows write is skipped). Empty values: use IsEmpty, never IsNull."""
        cfg = (f'<Passwords /><File OutputFileName="" RecordLimit="" SearchSubDirs="False" FileFormat="0">{e(path)}'
               '</File><FormatSpecificOptions><CodePage>65001</CodePage><Delimeter>,</Delimeter>'
               '<IgnoreErrors>False</IgnoreErrors><FieldLen>1024</FieldLen><AllowShareWrite>False</AllowShareWrite>'
               '<HeaderRow>True</HeaderRow><IgnoreQuotes>DoubleQuotes</IgnoreQuotes><ImportLine>1</ImportLine>'
               '</FormatSpecificOptions>')
        return self._add(f"{BASE}.DbFileInput.DbFileInput", "AlteryxDbFileInput", cfg, label)

    def output_csv(self, src, path, label=""):
        cfg = (f'<File MaxRecords="" FileFormat="0">{e(path)}</File><Passwords /><Disable>False</Disable>'
               '<FormatSpecificOptions><LineEndStyle>CRLF</LineEndStyle><Delimeter>,</Delimeter>'
               '<ForceQuotes>False</ForceQuotes><HeaderRow>True</HeaderRow><CodePage>65001</CodePage>'
               '<WriteBOM>True</WriteBOM><SuppressBlankFile>False</SuppressBlankFile></FormatSpecificOptions>'
               '<MultiFile value="False" />')
        t = self._add(f"{BASE}.DbFileOutput.DbFileOutput", "AlteryxDbFileOutput", cfg, label)
        self.connect(src, t)
        return t

    def output_parquet(self, src, path, label=""):
        """Parquet output (v5.14): FileFormat 79, no format options - as Designer 2026.2 saves it (probe workflow
        saved on the owner's laptop, 2026-10-12). Designer writes Parquet with the AMP engine only (RunWithE2)."""
        cfg = (f'<File MaxRecords="" FileFormat="79">{e(path)}</File><Passwords /><Disable>False</Disable>'
               '<FormatSpecificOptions /><MultiFile value="False" />')
        t = self._add(f"{BASE}.DbFileOutput.DbFileOutput", "AlteryxDbFileOutput", cfg, label)
        self.connect(src, t)
        return t

    def output_xlsx(self, src, path, sheet, option="Overwrite", label=""):
        """Excel (.xlsx) output, one sheet. option: "RemoveFile" = Overwrite File (Remove) (the first sheet a workflow
        writes), "Overwrite" = Overwrite Sheet (Drop) (later sheets; other sheets of the file are kept). Several
        sheets of one file are written one after another through Block Until Done (one writer at a time)."""
        cfg = (f'<File MaxRecords="" FileFormat="25">{e(path)}|||{e(sheet)}</File><Passwords /><Disable>False</Disable>'
               f'<FormatSpecificOptions><PreserveFormat>False</PreserveFormat><SkipFieldNames>False</SkipFieldNames>'
               f'<OutputOption>{option}</OutputOption></FormatSpecificOptions><MultiFile value="False" />')
        t = self._add(f"{BASE}.DbFileOutput.DbFileOutput", "AlteryxDbFileOutput", cfg, label)
        self.connect(src, t)
        return t

    def block_until_done(self, src, label=""):
        """Block Until Done: Output1 is processed completely (its downstream tools finish) before Output2 starts,
        then Output3. Anchors as Designer names them: (tool, "Output"), (tool, "Output2"), (tool, "Output3") (checked in Designer 2026.2, 2026-10-09)"""
        t = self._add(f"{BASE}.BlockUntilDone.BlockUntilDone", "AlteryxBlockUntilDone", "", label)
        self.connect(src, t)
        return t

    def input_yxdb(self, path, label=""):
        """Alteryx database (.yxdb): lossless types, as Designer writes the Input Data tool for it."""
        cfg = (f'<Passwords /><File OutputFileName="" FileFormat="19" SearchSubDirs="False" RecordLimit="">'
               f'{e(path)}</File><FormatSpecificOptions />')
        return self._add(f"{BASE}.DbFileInput.DbFileInput", "AlteryxDbFileInput", cfg, label)

    def output_yxdb(self, src, path, label=""):
        """Alteryx database (.yxdb) output; format options left at Designer's defaults."""
        cfg = (f'<File MaxRecords="" FileFormat="19">{e(path)}</File><Passwords /><Disable>False</Disable>'
               '<FormatSpecificOptions /><MultiFile value="False" />')
        t = self._add(f"{BASE}.DbFileOutput.DbFileOutput", "AlteryxDbFileOutput", cfg, label)
        self.connect(src, t)
        return t

    def formula(self, src, fields, label=""):
        """fields: list of (name, type, expression)."""
        ff = "".join(f'<FormulaField expression={q(x)} field={q(n)} size="{SIZE[t]}" type="{t}" />'
                     for n, t, x in fields)
        t = self._add(f"{BASE}.Formula.Formula", "AlteryxFormula", f"<FormulaFields>{ff}</FormulaFields>", label)
        self.connect(src, t)
        return t

    def filter(self, src, expr, label=""):
        t = self._add(f"{BASE}.Filter.Filter", "AlteryxFilter",
                      f"<Expression>{e(expr)}</Expression><Mode>Custom</Mode>", label)
        self.connect(src, t)
        return (t, "True"), (t, "False")

    def select(self, src, keep=None, rename=None, drop=None, keep_unknown=True, types=None, label=""):
        rename, drop, types = rename or {}, list(dict.fromkeys(drop or [])), types or {}   # keep order: same build, same bytes
        sf = []
        for k in (keep or []):
            attrs = f' rename={q(rename[k])}' if k in rename else ""
            if k in types:
                attrs += f' type="{types[k]}" size="{SIZE[types[k]]}"'
            sf.append(f'<SelectField field={q(k)} selected="True"{attrs} />')
        for k, v in rename.items():
            if k not in (keep or []):
                sf.append(f'<SelectField field={q(k)} selected="True" rename={q(v)} />')
        for k in drop:
            sf.append(f'<SelectField field={q(k)} selected="False" />')
        sf.append(f'<SelectField field="*Unknown" selected="{"True" if keep_unknown else "False"}" />')
        cfg = f'<OrderChanged value="False" /><CommaDecimal value="False" /><SelectFields>{"".join(sf)}</SelectFields>'
        t = self._add(f"{BASE}.AlteryxSelect.AlteryxSelect", "AlteryxSelect", cfg, label)
        self.connect(src, t)
        return t

    def join(self, left, right, lkeys, rkeys=None, label=""):
        """Returns dict with 'J', 'L', 'R' anchors. Right-side field names must not clash with the left."""
        rkeys = rkeys or lkeys
        jl = "".join(f'<Field field={q(k)} />' for k in lkeys)
        jr = "".join(f'<Field field={q(k)} />' for k in rkeys)
        cfg = (f'<JoinInfo connection="Left">{jl}</JoinInfo><JoinInfo connection="Right">{jr}</JoinInfo>'
               '<SelectConfiguration><Configuration outputConnection="Join"><OrderChanged value="False" />'
               '<CommaDecimal value="False" /><SelectFields><SelectField field="*Unknown" selected="True" />'
               '</SelectFields></Configuration></SelectConfiguration>')
        t = self._add(f"{BASE}.Join.Join", "AlteryxJoin", cfg, label)
        t_cfg = self.cur["children"][-1]
        t_cfg["join_attr"] = True
        self.connect(left, t, "Left")
        self.connect(right, t, "Right")
        return {"J": (t, "Join"), "L": (t, "Left"), "R": (t, "Right")}

    def union(self, srcs, label="", differ="Warning"):
        """differ: Designer's 'when fields differ' mode. "Ignore" only where the inputs are expected to differ
        (the unmatched-left half of a left join lacks the right-hand fields): no per-field warning, same output
        (checked in Designer 2026.2, 08 Oct 2026). More than 32 inputs (Designer's limit, error found on the laptop
        2026-10-11): unions of 32, then a union of those (same rows, same order)."""
        srcs = list(srcs)
        if len(srcs) > 32:
            parts = [self.union(srcs[i:i + 32], label=f"{label} (inputs {i + 1}-{min(i + 32, len(srcs))})",
                                differ=differ) for i in range(0, len(srcs), 32)]
            return self.union(parts, label=label, differ=differ)
        cfg = (f"<ByName_ErrorMode>{differ}</ByName_ErrorMode><ByName_OutputMode>All</ByName_OutputMode>"
               "<Mode>ByName</Mode><SetOutputOrder value=\"False\" />")
        t = self._add(f"{BASE}.Union.Union", "AlteryxUnion", cfg, label)
        for i, s in enumerate(srcs, 1):
            self.connect(s, t, "Input", name=f"#{i}")
        return t

    def left_join(self, left, right, lkeys, rkeys=None, label=""):
        j = self.join(left, right, lkeys, rkeys, label)
        return self.union([j["J"], j["L"]], label=f"{label} (left join)", differ="Ignore")

    def summarize(self, src, fields, label=""):
        """fields: list of (field, action, rename)."""
        sf = "".join(f'<SummarizeField field={q(f)} action="{a}" rename={q(r)} />' for f, a, r in fields)
        t = self._add("AlteryxSpatialPluginsGui.Summarize.Summarize", "AlteryxSummarize",
                      f"<SummarizeFields>{sf}</SummarizeFields>", label, dll="AlteryxSpatialPluginsEngine.dll")
        self.connect(src, t)
        return t

    def sort(self, src, fields, label=""):
        """fields: list of (field, 'Ascending'|'Descending')."""
        sf = "".join(f'<Field field={q(f)} order="{o}" />' for f, o in fields)
        t = self._add(f"{BASE}.Sort.Sort", "AlteryxSort", f'<SortInfo locale="1033">{sf}</SortInfo>', label)
        self.connect(src, t)
        return t

    def unique(self, src, fields, label=""):
        uf = "".join(f'<Field field={q(f)} />' for f in fields)
        t = self._add(f"{BASE}.Unique.Unique", "AlteryxUnique", f"<UniqueFields>{uf}</UniqueFields>", label)
        self.connect(src, t)
        return (t, "Unique"), (t, "Duplicates")   # Designer anchor name (reference workflows)

    def multirow(self, src, field, typ, expr, groupby=(), other="Empty", label=""):
        gb = "".join(f'<Field field={q(g)} />' for g in groupby)
        cfg = (f'<UpdateField value="False" /><UpdateField_Name /><CreateField_Name>{e(field)}</CreateField_Name>'
               f'<CreateField_Type>{typ}</CreateField_Type><CreateField_Size>{SIZE[typ]}</CreateField_Size>'
               f'<OtherRows>{other}</OtherRows><NumRows value="1" /><Expression>{e(expr)}</Expression>'
               f'<GroupByFields>{gb}</GroupByFields>')
        t = self._add(f"{BASE}.MultiRowFormula.MultiRowFormula", "AlteryxMultiRowFormula", cfg, label)
        self.connect(src, t)
        return t

    def generate_rows(self, src, field, typ, init, cond, loop, label=""):
        cfg = (f'<UpdateField value="False" /><UpdateField_Name /><CreateField_Name>{e(field)}</CreateField_Name>'
               f'<CreateField_Type>{typ}</CreateField_Type><CreateField_Size>{SIZE[typ]}</CreateField_Size>'
               f'<Expression_Init>{e(init)}</Expression_Init><Expression_Cond>{e(cond)}</Expression_Cond>'
               f'<Expression_Loop>{e(loop)}</Expression_Loop>')
        t = self._add(f"{BASE}.GenerateRows.GenerateRows", "AlteryxGenerateRows", cfg, label)
        self.connect(src, t)
        return t

    def record_id(self, src, field, label=""):
        cfg = (f'<FieldName>{e(field)}</FieldName><StartValue>1</StartValue><FieldType>Int64</FieldType>'
               '<FieldSize>8</FieldSize><Position>0</Position>')
        t = self._add(f"{BASE}.RecordID.RecordID", "AlteryxRecordID", cfg, label)
        self.connect(src, t)
        return t

    # ---------------------------------------------------------------- serialise
    def dag_grid(self, c):
        """Tool positions inside container c as a left-to-right flow (self.dag, owner 2026-10-10: one step per row,
        no congested branches): column = longest chain of connections from the container's inputs, row = the lane of
        the first predecessor in the container when free (a chain stays on one line), else the next free lane.
        Returns ({tool id: (column, row)}, columns, rows)."""
        ids = [t["id"] for t in c["children"]]
        inside = set(ids)
        preds = {i: [] for i in ids}
        for s_, _, d, _, _ in self.conns:
            if s_ in inside and d in inside:
                preds[d].append(s_)
        depth = {}

        def dep(i, seen=()):
            if i not in depth:
                ps = [p for p in preds[i] if p not in seen]
                depth[i] = 1 + max((dep(p, seen + (i,)) for p in ps), default=-1)
            return depth[i]
        pos, used = {}, set()
        for i in ids:                               # creation order follows the flow
            col = dep(i)
            want = next((pos[p][1] for p in preds[i] if p in pos), 0)
            row = want
            while (col, row) in used:
                row += 1
            used.add((col, row))
            pos[i] = (col, row)
        ncols = 1 + max((x for x, _ in pos.values()), default=0)
        nrows = 1 + max((y for _, y in pos.values()), default=0)
        return pos, ncols, nrows

    def compact(self):
        """Fewer tools for the same result (owner 2026-10-10: the workflows open slowly): a Formula whose only output
        feeds one Formula in the same container takes over that Formula's fields (in order, so later fields still see
        earlier ones; not when both set the same field), and a Filter whose True output alone feeds one Filter whose
        False output is unused takes over its condition with AND. Repeated until nothing merges."""
        import html as H
        import re as R
        from collections import defaultdict
        where = {t["id"]: (c, t) for c in self.containers for t in c["children"]}
        while True:
            outs, ins = defaultdict(list), defaultdict(list)
            for k in self.conns:
                outs[k[0]].append(k)
                ins[k[2]].append(k)
            done = False
            for tid, (c, t) in list(where.items()):
                kind = t["plugin"].split(".")[-1]
                o = outs.get(tid, [])
                if kind not in ("Formula", "Filter") or len(o) != 1 or o[0][3] != "Input":
                    continue
                if kind == "Filter" and o[0][1] != "True":
                    continue
                d = o[0][2]
                if d not in where or len(ins.get(d, [])) != 1:
                    continue
                c2, u = where[d]
                if c2 is not c or u["plugin"] != t["plugin"]:
                    continue
                if kind == "Formula":
                    tf = set(R.findall(r'field="([^"]*)" size', t["config"]))
                    if tf & set(R.findall(r'field="([^"]*)" size', u["config"])):
                        continue
                    cfg = t["config"].replace("</FormulaFields>", "") + u["config"].replace("<FormulaFields>", "")
                else:
                    if any(k[1] == "False" for k in outs.get(d, [])):
                        continue
                    ex = [H.unescape(R.search(r"<Expression>(.*?)</Expression>", x["config"], R.S).group(1))
                          for x in (t, u)]
                    cfg = f"<Expression>{e('(' + ex[0] + ') AND (' + ex[1] + ')')}</Expression><Mode>Custom</Mode>"
                lab = [x["label"] or self.auto_label(x["plugin"], x["config"]) for x in (t, u)]
                t["label"] = (lab[0] + "; " + lab[1])[:120]
                t["config"] = cfg
                self.conns = [(tid,) + k[1:] if k[0] == d else k for k in self.conns if k is not o[0]]
                c["children"].remove(u)
                del where[d]
                done = True
                break
            if not done:
                return

    def note_text(self, c):
        """Comment text at the top of a container (owner 2026-10-10: each container says what it does)."""
        f = getattr(self, "note_for", None)
        if f is None:
            return None
        n = [x["id"] for x in self.containers].index(c["id"]) + 1
        return f(c["caption"], n, len(self.containers))

    def note_height(self, c, width):
        import math
        t = self.note_text(c)
        if not t:
            return 0
        per = max(30, int((width - 20) / 6.2))
        return math.ceil(len(t) / per) * 14 + 16

    def layout(self):
        """Container geometry. With self.band_of (caption -> (band, align_key)): one row per band, read left to right
        then down (row 1 = raw sheets, row 2 = per-TPA mapping under its sheets, then the Stage B steps); else stacked."""
        import math
        geo, band_of = {}, getattr(self, "band_of", None)

        def size(c):
            if getattr(self, "dag", False):
                _, cols, rows = self.dag_grid(c)
                wdt = max(max(cols, 2) * 120 + 60, 420 if self.note_text(c) else 0)
                return cols, wdt, rows * 90 + 70 + self.note_height(c, wdt - 40)
            n = len(c["children"])
            cols = max(4, min(12, math.ceil(math.sqrt(max(n, 1)) * 1.4)))
            rows = (n + cols - 1) // cols
            wdt = max(cols * 120 + 60, 420 if self.note_text(c) else 0)
            return cols, wdt, rows * 90 + 70 + self.note_height(c, wdt - 40)
        if not band_of:
            y = 60
            for c in self.containers:
                cols, wdt, hgt = size(c)
                geo[c["id"]] = (60, y, cols, wdt, hgt)
                y += hgt + 40
            return geo
        bands, keys = {}, {}
        for c in self.containers:
            b, key = band_of(c["caption"])
            bands.setdefault(b, []).append((c, key))
        y = 60
        for b in sorted(bands):
            x, tallest = 60, 0
            for c, key in bands[b]:
                cols, wdt, hgt = size(c)
                if key and key in keys and keys[key][0] < b:
                    x = max(x, keys[key][1])
                geo[c["id"]] = (x, y, cols, wdt, hgt)
                if key and key not in keys:
                    keys[key] = (b, x)
                x += wdt + 60
                tallest = max(tallest, hgt)
            y += tallest + 120
        return geo

    @staticmethod
    def auto_label(plugin, config):
        """A short annotation from the configuration when the builder gave none (owner 2026-10-07: readable canvas)."""
        import html as H
        import re as R
        k = plugin.split(".")[-1]
        cut = (lambda x, n=60: x if len(x) <= n else x[:n - 3] + "...")
        f = [H.unescape(x) for x in R.findall(r'field="([^"]*)"', config)]
        if k == "Filter":
            return cut(H.unescape(R.search(r"<Expression>(.*?)</Expression>", config, R.S).group(1)))
        if k == "Formula":
            return cut("set " + ", ".join(H.unescape(x) for x in R.findall(r'field="([^"]*)" size', config)))
        if k == "AlteryxSelect":
            ren = R.findall(r'field="([^"]*)" selected="True" rename="([^"]*)"', config)
            keep = [x for x in f if x != "*Unknown"]
            if ren and len(ren) == len(keep):
                return cut("rename " + ", ".join(f"{H.unescape(a)}->{H.unescape(b)}" for a, b in ren))
            return cut("keep " + ", ".join(keep))
        if k == "Join":
            lk = R.search(r'connection="Left">(.*?)</JoinInfo>', config, R.S).group(1)
            rk = R.search(r'connection="Right">(.*?)</JoinInfo>', config, R.S).group(1)
            return cut("join " + "+".join(R.findall(r'field="([^"]*)"', lk)) + " = " +
                       "+".join(R.findall(r'field="([^"]*)"', rk)))
        if k == "Sort":
            return cut("sort by " + ", ".join(f))
        if k == "Summarize":
            g = R.findall(r'field="([^"]*)" action="GroupBy"', config)
            a = R.findall(r'action="(?!GroupBy)([^"]*)" rename="([^"]*)"', config)
            return cut(("by " + ", ".join(g) + ": " if g else "") + ", ".join(f"{x} {y}" for x, y in a))
        if k == "Unique":
            return cut("unique by " + ", ".join(f))
        if k == "Union":
            return "union by name"
        return ""

    def xml(self):
        out = ['<?xml version="1.0"?>', '<AlteryxDocument yxmdVer="2025.2" RunE2="T">', "<Nodes>"]
        self.compact()
        geo = self.layout()
        for c in self.containers:
            x0, y0, cols, wdt, hgt = geo[c["id"]]
            out.append(f'<Node ToolID="{c["id"]}"><GuiSettings Plugin="AlteryxGuiToolkit.ToolContainer.ToolContainer">'
                       f'<Position x="{x0}" y="{y0}" width="{wdt}" height="{hgt}" /></GuiSettings>'
                       f'<Properties><Configuration><Caption>{e(c["caption"])}</Caption>'
                       '<Style TextColor="#314c4a" FillColor="#ecf2f2" BorderColor="#314c4a" Transparency="25" Margin="25" />'
                       '<Disabled value="False" /><Folded value="False" /></Configuration>'
                       f'<Annotation DisplayMode="0"><Name /><AnnotationText>{e(c["caption"])}</AnnotationText>'
                       '<DefaultAnnotationText /><Left value="False" /></Annotation></Properties><ChildNodes>')
            note, nh = self.note_text(c), self.note_height(c, wdt - 40)
            if note:      # Comment tool (pattern of the team's workflows): what this container does
                out.append(f'<Node ToolID="{900000 + c["id"]}"><GuiSettings Plugin="AlteryxGuiToolkit.TextBox.TextBox">'
                           f'<Position x="{x0 + 20}" y="{y0 + 40}" width="{wdt - 40}" height="{nh - 6}" /></GuiSettings>'
                           f'<Properties><Configuration><Text>{e(note)}</Text><Font name="Arial" size="8.25" style="0" />'
                           '<TextColor name="Black" /><FillColor name="White" /><Shape shape="0" />'
                           '<Justification Justification="4" /></Configuration><Annotation DisplayMode="0"><Name />'
                           '<DefaultAnnotationText /><Left value="False" /></Annotation></Properties></Node>')
            dpos = self.dag_grid(c)[0] if getattr(self, "dag", False) else None
            for i, t in enumerate(c["children"]):
                if dpos is not None:
                    x, y = x0 + 30 + dpos[t["id"]][0] * 120, y0 + 50 + nh + dpos[t["id"]][1] * 90
                else:
                    x, y = x0 + 30 + (i % cols) * 120, y0 + 50 + nh + (i // cols) * 90
                cfg_open = '<Configuration joinByRecordPos="False">' if t.get("join_attr") else "<Configuration>"
                out.append(f'<Node ToolID="{t["id"]}"><GuiSettings Plugin="{t["plugin"]}"><Position x="{x}" y="{y}" />'
                           f'</GuiSettings><Properties>{cfg_open}{t["config"]}</Configuration>'
                           f'<Annotation DisplayMode="0"><Name>{e(t["label"] or self.auto_label(t["plugin"], t["config"]))}</Name><DefaultAnnotationText />'
                           '<Left value="False" /></Annotation></Properties>'
                           f'<EngineSettings EngineDll="{t["dll"]}" EngineDllEntryPoint="{t["entry"]}" /></Node>')
            out.append("</ChildNodes></Node>")
        out.append("</Nodes><Connections>")
        for s, sa, d, da, name in self.conns:
            nm = f" name={q(name)}" if name else ""
            out.append(f'<Connection{nm}><Origin ToolID="{s}" Connection="{sa}" />'
                       f'<Destination ToolID="{d}" Connection="{da}" /></Connection>')
        out.append("</Connections><Properties>")
        out.append('<Memory default="True" /><GlobalRecordLimit value="0" /><TempFiles default="True" />'
                   '<Annotation on="True" includeToolName="False" /><ConvErrorLimit value="10" />'
                   '<ConvErrorLimit_Stop value="False" /><CancelOnError value="True" /><DisableBrowse value="False" />'
                   '<EnablePerformanceProfiling value="False" /><RunWithE2 value="True" /><DefaultTZ value="Local" />'
                   '<PredictiveToolsCodePage value="1252" /><DisableAllOutput value="False" />'
                   '<ShowAllMacroMessages value="False" /><ShowConnectionStatusIsOn value="True" />'
                   '<ShowConnectionStatusOnlyWhenRunning value="True" /><ZoomLevel value="0" />'
                   '<LayoutType>Horizontal</LayoutType>')
        out.append("<Constants>" + "".join(
            f"<Constant><Namespace>User</Namespace><Name>{e(n)}</Name><Value>{e(v)}</Value>"
            f'<IsNumeric value="False" /></Constant>' for n, v in self.constants) + "</Constants>")
        out.append(f'<MetaInfo><NameIsFileName value="True" /><Name>{e(self.name)}</Name>'
                   f'<Description>{e(self.description)}</Description><RootToolName /><ToolVersion />'
                   '<ToolInDb value="False" /><CategoryName /><SearchTags /><Author /><Company /><Copyright />'
                   '<DescriptionLink actual="" displayed="" /><Example><Description /><File /></Example>'
                   '</MetaInfo><Events><Enabled value="True" /></Events></Properties></AlteryxDocument>')
        return "\n".join(out)
