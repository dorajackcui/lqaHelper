"""Resolve issue citations against the project's language pack."""
import csv
import hashlib
import itertools
import re
import sqlite3
import tempfile
import threading
from collections import defaultdict
from contextlib import closing
from pathlib import Path


KEY_HEADERS = ("key", "stringkey", "stringid", "lockey", "textkey", "textid", "id", "词条键", "词条id", "键名")
ZH_HEADERS = ("zhcn", "zhhans", "简体中文", "简中", "中文", "中文原文", "中文文本", "chinesesimplified",
              "simplifiedchinese", "schinese", "chs", "zh", "cn", "chinese", "繁體中文", "繁体中文", "zhtw")
TEXT_HEADERS = ("msgstr", "原文", "源文本", "source", "sourcetext", "译文", "target", "translation", "targettext")
SOURCE_HEADERS = ("sourcefile", "来源文件", "来源表")
HAN = re.compile(r"[\u3400-\u9fff]")
KANA = re.compile(r"[\u3040-\u30ff]")
ROW_CITATION = re.compile(r"(?:第\s*(\d+)\s*行|(?:\brow|行号)\s*[:：#]?\s*(\d+))", re.I)
KEY_TOKEN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:/@,#-]*")
csv.field_size_limit(max(csv.field_size_limit(), 2**31 - 1))


def column(headers, names):
    return next((headers.index(name) for name in names if name in headers), None)


def cell(cells, index):
    return cells[index] if index is not None and index < len(cells) else ""


def table_rows(path, excel):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = csv.reader(stream, delimiter="\t" if excel or path.suffix.lower() == ".tsv" else ",")
        if excel:
            next(rows, None)  # Header of the existing workbook TSV index.
            for row in rows:
                yield row[0], int(row[1]), row[2:]
        else:
            for number, row in enumerate(rows, 1):
                yield "", number, row


def index_sheet(db, sheet, rows):
    for _, _, cells in rows:
        headers = [re.sub(r"[\W_]", "", value.casefold()) for value in cells]
        key = column(headers, KEY_HEADERS)
        if key is not None and any(h in ZH_HEADERS or h in TEXT_HEADERS for h in headers):
            break
    else:
        return
    zh = column(headers, ZH_HEADERS)
    source = column(headers, SOURCE_HEADERS)
    if zh is None:
        sample = list(itertools.islice(rows, 64))
        candidates = [i for i, h in enumerate(headers) if h in TEXT_HEADERS]
        chinese = [i for i in candidates if any(HAN.search(cell(r[2], i)) for r in sample)
                   and not any(KANA.search(cell(r[2], i)) for r in sample)]
        if len(chinese) != 1:
            return
        zh = chinese[0]
        rows = itertools.chain(sample, rows)
    db.execute("INSERT INTO sheets VALUES (?)", (sheet,))
    def entries():
        for _, number, cells in rows:
            identifier, text = cell(cells, key), cell(cells, zh)
            if identifier.strip() and text.strip():
                yield identifier, text, sheet, number, cell(cells, source)
    db.executemany("INSERT INTO entries VALUES (?,?,?,?,?)", entries())


def legacy_keys(text):
    # Keep punctuation inside exact fields (some real keys contain both dots and a comma).
    text = ROW_CITATION.sub(" ", text)
    fields = {part.strip(" \t\r\n\"'«»[]()（）") for part in re.split(r"[|｜;；\n\t、，“”\x60]", text)}
    tokens = {m.group().rstrip(".:,") for m in KEY_TOKEN.finditer(text)
              if re.search(r"[_.:/@,#-]|[A-Za-z]\d|\d[A-Za-z]", m.group())}
    labeled = re.findall(r"(?:\bkey|\bid|词条键)\s*[:：=]\s*([^\s|｜;；]+)", text, re.I)
    return (fields | tokens | set(labeled)) - {""}


def cited_locations(text, by_number, sheets):
    sheet = sheets[0] if len(sheets) == 1 else None
    previous = 0
    rows = set()
    for mention in ROW_CITATION.finditer(text):
        context = text[previous:mention.start()]
        positions = [(context.rfind(s), len(s), s) for s in sheets if s and s in context]
        if positions:
            sheet = max(positions)[2]
        number = mention[1] or mention[2]
        if len(number) <= 9:
            rows.update(r for r in by_number[int(number)] if r[2] == sheet)
        previous = mention.end()
    return rows


def find_rows(db, field, values):
    found = defaultdict(list)
    values = list(values)
    for start in range(0, len(values), 500):
        batch = values[start:start + 500]
        marks = ",".join("?" for _ in batch)
        sql = f"SELECT key,zh,sheet,num,source FROM entries WHERE {field} IN ({marks}) ORDER BY sheet,num"
        for row in db.execute(sql, batch):
            found[row[0] if field == "key" else row[3]].append(row)
    return found


class StringCatalog:
    def __init__(self, cache):
        self.cache = cache
        self.lock = threading.Lock()

    def index(self, path, excel):
        stat = path.stat()
        signature = f"1/{path}/{stat.st_size}/{stat.st_mtime_ns}".encode("utf-8")
        target = self.cache / (hashlib.sha256(signature).hexdigest() + ".sqlite")
        with self.lock:
            if target.is_file():
                with closing(sqlite3.connect(target)) as db:
                    if db.execute("PRAGMA user_version").fetchone() == (1,):
                        return target
            self.cache.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=self.cache) as temp:
                staged = Path(temp) / "strings.sqlite"
                with closing(sqlite3.connect(staged)) as db:
                    db.execute("CREATE TABLE sheets (name TEXT)")
                    db.execute("CREATE TABLE entries (key TEXT, zh TEXT, sheet TEXT, num INTEGER, source TEXT)")
                    for sheet, rows in itertools.groupby(table_rows(path, excel), key=lambda row: row[0]):
                        index_sheet(db, sheet, rows)
                    db.execute("CREATE INDEX entry_key ON entries(key)")
                    db.execute("CREATE INDEX entry_number ON entries(num)")
                    db.execute("PRAGMA user_version=1")
                    db.commit()
                staged.replace(target)
        return target

    def related(self, project, issues):
        ref = project["reference"]
        result = {"name": ref["name"] if ref else "", "status": "ok", "entries": []}
        if not ref:
            return dict(result, status="no_reference")
        if not issues:
            return result
        excel = bool(ref.get("textFile"))
        path = Path(project["folder"]) / "references" / ref.get("textFile", ref["file"])
        if not excel and path.suffix.lower() not in (".csv", ".tsv"):
            return dict(result, status="no_columns")
        requests = []
        all_keys, all_numbers = set(), set()
        for shot, revision, index, issue in issues:
            text = issue["reference"]
            explicit = issue.get("referenceKeys")
            keys = set(explicit) if explicit is not None else legacy_keys(text)
            numbers = {int(a or b) for a, b in ROW_CITATION.findall(text) if len(a or b) <= 9}
            requests.append((shot, revision, index, text, explicit is not None, keys))
            all_keys.update(keys)
            if explicit is None:
                all_keys.update(part.strip() for k in keys if "," in k for part in k.split(","))
            all_numbers.update(numbers)
        if not all_keys and not all_numbers:
            return result
        with closing(sqlite3.connect(self.index(path, excel))) as db:
            sheets = [name for (name,) in db.execute("SELECT name FROM sheets")]
            if not sheets:
                return dict(result, status="no_columns")
            by_key = find_rows(db, "key", all_keys)
            by_number = find_rows(db, "num", all_numbers)
        groups = {}
        for shot, revision, index, text, structured, keys in requests:
            cited_rows = cited_locations(text, by_number, sheets)
            quotes = {p.strip(" \t\r\n‘’「」") for p in re.split(r"[|｜;；\n\t“”\x60\"：]", ROW_CITATION.sub("", text))}
            if not structured:
                keys = keys | {part.strip() for k in keys if "," in k and not by_key[k] for part in k.split(",")}
            matched = set()
            for key in keys:
                rows = by_key[key]
                anchored = [r for r in rows if r in cited_rows]
                if anchored:
                    rows = anchored
                elif len({r[1] for r in rows}) > 1:
                    rows = [r for r in rows if r[1].strip() in quotes]
                    if len({r[1] for r in rows}) != 1:
                        continue
                matched.update(rows)
            if not structured:
                # A historical row number alone can point elsewhere after a pack update.
                matched.update(r for r in cited_rows if r[1].strip() in quotes)
            for key, zh, sheet, num, source in sorted(matched):
                group = groups.setdefault((key, zh), {"key": key, "chinese": zh, "locations": {}, "issues": {}})
                group["locations"][sheet, num] = {"sheet": sheet, "row": num, "source": source}
                group["issues"][shot, index] = {"shot": shot, "revision": revision, "index": index}
        for group in groups.values():
            group["locations"] = list(group["locations"].values())
            group["issues"] = list(group["issues"].values())
        result["entries"] = sorted(groups.values(), key=lambda g: (g["key"], g["chinese"]))
        return result
