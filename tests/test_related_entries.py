import base64
import csv
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import related_csv_report
from lqa.agent import validate_report
from lqa.store import Store
from lqa.strings import StringCatalog
from tests.test_workflow import ISSUE, png


class RelatedEntries(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "references").mkdir()
        self.catalog = StringCatalog(self.root / "cache")

    def table(self, rows, filename="pack.xlsx.tsv"):
        with (self.root / "references" / filename).open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, delimiter="," if filename.endswith(".csv") else "\t")
            if filename.endswith(".xlsx.tsv"):
                w.writerow(["工作表", "Excel 行号", "单元格"])
            w.writerows(rows)
        ref = dict(name=filename, file=filename)
        if filename.endswith(".xlsx.tsv"):
            ref.update(textFile=filename, file=filename[:-4])
        return dict(folder=str(self.root), reference=ref)

    def test_legacy_and_new_reports_keep_many_to_many_links(self):
        rows = [["MergedData", 1, "SourceFile", "Key", "MsgStr", "fr"],
                ["MergedData", 2, "界面.xlsx", "UI.Wait,UI", "请稍候", "Patientez"],
                ["MergedData", 3, "界面.xlsx", "shop_1", "海岛", "Île"],
                ["MergedData", 4, "界面.xlsx", "shop_10", "商店", "Boutique"],
                ["MergedData", 5, "界面.xlsx", "UI.Wait", "另一项", ""],
                ["MergedData", 6, "界面.xlsx", "UI", "另一条", ""],
                ["MergedData", 7, "界面.xlsx", "3", "行号也可能是 Key", ""]]
        project = self.table(rows)
        issues = [
            ("a", 3, 0, {"reference": "MergedData 第 2 行｜UI.Wait,UI｜请稍候；第 3 行｜shop_1｜海岛"}),
            ("b", 1, 0, {"reference": "shop_10 用作对比", "referenceKeys": ["shop_1", "shop_1", "missing"]}),
            ("b", 1, 1, {"reference": "", "sourceText": "Boutique"}),
            ("b", 1, 2, {"reference": "shop_10", "referenceKeys": []}),
        ]
        entries = self.catalog.related(project, issues)["entries"]
        self.assertEqual([e["key"] for e in entries], ["UI.Wait,UI", "shop_1"])
        shop = entries[1]
        self.assertEqual(shop["chinese"], "海岛")
        self.assertEqual(shop["locations"], [{"sheet": "MergedData", "row": 3, "source": "界面.xlsx"}])
        self.assertEqual(shop["issues"], [{"shot": "a", "revision": 3, "index": 0},
                                          {"shot": "b", "revision": 1, "index": 0}])
        with patch("lqa.strings.table_rows", side_effect=AssertionError("existing index should be reused")):
            other = StringCatalog(self.root / "cache").related(project, [("a", 3, 0, {"reference": "shop_1,shop_10"})])
        self.assertEqual({e["key"] for e in other["entries"]}, {"shop_1", "shop_10"})

    def test_ambiguous_keys_need_a_verified_meaning_or_sheet_location(self):
        project = self.table([["主界面", 4, "Key", "中文"], ["主界面", 5, "confirm", "确定"],
                              ["战斗", 2, "中文", "Key"], ["战斗", 5, "确定出战", "confirm"]])
        issues = [("x", 1, 0, {"reference": "", "referenceKeys": ["confirm"]}),
                  ("x", 1, 1, {"reference": "战斗 第 5 行", "referenceKeys": ["confirm"]}),
                  ("x", 1, 2, {"reference": "主界面 第 5 行｜确定"})]
        entries = self.catalog.related(project, issues)["entries"]
        self.assertEqual({e["chinese"]: [i["index"] for i in e["issues"]] for e in entries},
                         {"确定出战": [1], "确定": [2]})

    def test_csv_authoritative_chinese_and_missing_columns(self):
        project = self.table([["fr", "zh-CN", "String ID"], ["Bouton", " 按钮,\n下一行 ", "00042"]], "a.csv")
        issue = [("s", 1, 0, {"reference": "", "referenceKeys": ["00042"]})]
        item, = self.catalog.related(project, issue)["entries"]
        self.assertEqual((item["key"], item["chinese"], item["locations"][0]["row"]), ("00042", " 按钮,\n下一行 ", 2))
        other = self.table([["Key", "en", "fr"], ["00042", "English", "Français"]], "b.tsv")
        self.assertEqual(self.catalog.related(other, issue)["status"], "no_columns")
        generic = self.table([["id", "source", "target"], ["00042", "English", "中文译文"]], "c.tsv")
        self.assertEqual(self.catalog.related(generic, issue)["entries"][0]["chinese"], "中文译文")
        self.assertEqual(self.catalog.related(dict(reference=None), issue)["status"], "no_reference")

    def test_existing_project_lifecycle_and_schema(self):
        store = Store(self.root / "workspace")
        project = store.save_project({"name": "菜单"}, reference_upload=("游戏.xlsx",
            [(Path(__file__).parent / "fixtures/glossary.xlsx").read_bytes()]))
        key = store.add(png([1, 2, 3]), "fixture.png", project)
        report = {"summary": "核对", "issues": [dict(ISSUE, reference="菜单词条 第 5 行",
                                                  referenceKeys=["menu.new_game"])]}
        with patch("lqa.store.time.time", return_value=store.shots[key]["reviewAfter"] + 1):
            shot, _ = store.claim()
        store.complete(key, shot["revision"], validate_report(report))
        restored = Store(store.root)
        item, = restored.related_entries(project)["entries"]
        self.assertEqual((item["key"], item["chinese"]), ("menu.new_game", "开始新的游戏"))
        exported, = csv.DictReader(io.StringIO(related_csv_report(restored, project).decode("utf-8-sig")))
        self.assertEqual((exported["语言包"], exported["来源行"]), ("游戏.xlsx", "菜单词条 · 第 5 行"))
        restored.resolve(key, 0, shot["revision"], True)
        self.assertEqual(restored.related_entries(project)["entries"], [item])
        other = restored.save_project({"name": "其他"})
        self.assertFalse(restored.related_entries(other)["entries"])
        restored.save_project({"id": project, "name": "菜单"}, reference_upload=("new.csv", ["Key,中文\nanother,其他".encode()]))
        self.assertFalse(restored.related_entries(project)["entries"])
        restored.retry(key)
        self.assertFalse(restored.related_entries(project)["entries"])
        with self.assertRaisesRegex(ValueError, "Key"):
            validate_report({"summary": "", "issues": [dict(ISSUE, referenceKeys="menu.start")]})

    def test_csv_groups_visible_issues_and_preserves_text_and_evidence(self):
        store = Store(self.root / "workspace")
        chinese = ' 点"确定",\n继续 '
        reference = io.StringIO(newline="")
        csv.writer(reference).writerows([
            ["Key", "中文", "SourceFile"], ["00042", chinese, "菜单.xlsx"], ["=ui.bonus", "+5 体力", "奖励.xlsx"],
            ["00042", chinese, "手柄.xlsx"], ["unused", "未涉及", "菜单.xlsx"],
        ])
        pid = store.save_project({"name": "菜单"}, reference_upload=("总表.csv", [reference.getvalue().encode()]))
        original = png([1, 2, 3], 2, 1)
        first = store.add(original, "A.png", pid)
        cut = png([1, 2, 3])
        store.edit(first, {"revision": 1, "crop": {"x": 1, "y": 0, "width": 1, "height": 1},
                           "rendered": base64.b64encode(cut).decode()})

        def finish(key, issues):
            with patch("lqa.store.time.time", return_value=store.shots[key]["reviewAfter"] + 1):
                shot, _ = store.claim()
            self.assertEqual(shot["id"], key)
            store.complete(key, shot["revision"], {"summary": "核对", "issues": issues})

        finish(first, [dict(ISSUE, description="两处漏译", referenceKeys=["00042", "=ui.bonus"]),
                       dict(ISSUE, description="按钮超框", referenceKeys=["00042"]),
                       dict(ISSUE, description="进度条错位", referenceKeys=[])])
        second = store.add(original, "B.png", pid)
        finish(second, [dict(ISSUE, description="提示截断", referenceKeys=["00042"])])
        store.resolve(first, 0, 2, True)

        def exported(project=pid, include_resolved=False):
            raw = related_csv_report(store, project, include_resolved=include_resolved)
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
            return list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline="")))

        row, = exported()
        self.assertEqual((row["Key"], row["中文原文"], row["关联问题数"], row["待处理数"]), ("00042", chinese, "2", "2"))
        self.assertIn("截图 1 · 待处理 · 按钮超框", row["关联问题"])
        self.assertIn("截图 2 · 待处理 · 提示截断", row["关联问题"])
        self.assertIn(ISSUE["suggestion"], row["关联问题"])
        self.assertNotIn("两处漏译", row["关联问题"])
        self.assertNotIn("进度条错位", row["关联问题"])
        self.assertEqual(row["语言包"], "总表.csv")
        self.assertEqual(row["来源行"].splitlines(), ["菜单.xlsx · 第 2 行", "手柄.xlsx · 第 4 行"])
        self.assertEqual([Path(p).read_bytes() for p in row["截图路径"].splitlines()], [cut, original])
        same, bonus = exported(include_resolved=True)
        self.assertEqual((same["关联问题数"], same["待处理数"], same["截图路径"]), ("3", "2", row["截图路径"]))
        self.assertIn("截图 1 · 已处理 · 两处漏译", same["关联问题"])
        self.assertEqual((bonus["Key"], bonus["中文原文"], bonus["待处理数"]), ("'=ui.bonus", "'+5 体力", "0"))
        self.assertIn("两处漏译", bonus["关联问题"])
        other = store.save_project({"name": "其他"})
        self.assertEqual(exported(other, include_resolved=True), [])
        store.resolve(first, 1, 2, True)
        store.resolve(second, 0, 1, True)
        self.assertEqual(exported(), [])
