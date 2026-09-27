"""Offline tests: small source-faithful fixtures plus optional real cache checks.

Run: python -B -m unittest discover -s <this_directory> -p test_air_extract.py -v
Optional: set MEE_AIR_TEST_MANIFEST to a local report_manifest.json. No fixture
contains a private path; complete reports are not copied into this test suite.
"""

from copy import deepcopy
import json
import os
from pathlib import Path
import unittest

from air_extract import extract_air, summarize_jja


def report(rows, period="2024年06月", typ="月报", quarter=2, section="airMainEmission"):
    return {"companyInfo": {"reportType": typ, "reportTime": period, "reportQuarter": quarter, "permitCode": "P1", "enterName": "测试企业"}, "emissionInfo": {section: rows}}


def pollutant(value="1", code="A21002", name="氮氧化物", outlet="DA001", **extra):
    return dict(pollutantCode=code, pollutantName=name, outletCode=outlet, emitValue=value, **extra)


def extract(body, rid="r1", **meta):
    return extract_air(body, dict(report_id=rid, **meta), {"enterprise_id": "E1", "permit_code": "P1"})


class ExtractionTests(unittest.TestCase):
    def test_changle_2024_q3_real_values(self):
        # Source: reviewed public JSON, NOx rows DA001..DA005 and whole total.
        original = report([], "2024年第3季", "季报", 3)
        original["emissionInfo"] = {
            "airMainEmission": [dict(pollutantCode="A21002", pollutantName="氮氧化物", outletCode=outlet, firstMonth=july, secondMonth=august, thirdMonth="999")
                                for outlet, july, august in [("DA001", "70.69", "61.2"), ("DA002", "0", "0"), ("DA003", "70.27", "61.57"), ("DA004", "85.05", "74.3"), ("DA005", "78.37", "67.96")]],
            "airTotalEmission": [dict(pollutantCode="A21002", pollutantName="NOx", firstMonth="0", secondMonth="0", thirdMonth="0", portType="全厂合计")],
        }
        records = extract(original)
        self.assertEqual({row["month"] for row in records}, {7, 8})
        self.assertAlmostEqual(sum(row["value"] for row in records if row["month"] == 7 and row["scope"] == "main_outlet"), 304.38)
        self.assertAlmostEqual(sum(row["value"] for row in records if row["month"] == 8 and row["scope"] == "main_outlet"), 265.03)
        self.assertEqual([row["value"] for row in records if row["scope"] == "enterprise_total"], [0, 0])
        self.assertEqual(len(summarize_jja(records)), 6)
        self.assertTrue(all(item["jja"] is None for item in summarize_jja(records)))

    def test_nantong_2024_june_real_values(self):
        original = report([pollutant(value, outlet=outlet) for outlet, value in [("DA001", "/"), ("DA002", "/"), ("DA003", "8.778"), ("DA004", "11.812")]])
        original["emissionInfo"]["airTotalEmission"] = [
            pollutant("20.59", name="NOx", outlet="", portType="全厂合计"),
            pollutant("2.391", code="A99911", name="颗粒物", outlet="", portType="全厂合计"),
            pollutant("113157.1", code="/", name="工业废气排放量", outlet="", remark="单位：万Nm³", portType="全厂合计"),
        ]
        records = extract(original)
        self.assertEqual([row["value_status"] for row in records[:2]], ["not_reported"] * 2)
        self.assertEqual(records[4]["value"], 20.59)
        self.assertEqual(records[5]["pollutant_code"], "A99911")
        self.assertEqual(records[5]["value"], 2.391)
        self.assertEqual(records[6]["unit"], "万Nm3")
        self.assertEqual(records[6]["dimension"], "volume")

    def test_fenyi_2023_q3_real_values(self):
        original = report([dict(pollutantCode="A21002", pollutantName="氮氧化物", outletCode="DA002", firstMonth="1.6", secondMonth="0.88", thirdMonth="0", total="2.48")], "2023年第03季", "季报", 3)
        records = extract(original)
        self.assertEqual([(row["month"], row["value"]) for row in records], [(7, 1.6), (8, 0.88)])
        self.assertEqual(summarize_jja(records)[0]["missing_months"], [6])
        self.assertIsNone(summarize_jja(records)[0]["jja"])

    def test_lossless_cross_industry_and_input_unchanged(self):
        body = report([pollutant("1", code="A20057", name="汞及其化合物"), pollutant("2", code="CUSTOM", name="未知指标")])
        body["emissionInfo"].update(airMinorEmission=[pollutant("0", code="A21001", name="氨", outlet="", portType="一般排放口（合计）")], airOtherEmission=[pollutant("5", name="氯化氢", code="CUSTOM2")], waterMainEmission=[pollutant("999")])
        old = deepcopy(body)
        records = extract(body)
        self.assertEqual(len(records), 4)
        self.assertEqual(body, old)
        self.assertEqual(records[1]["unit_status"], "unknown")
        self.assertEqual(records[1]["raw_row"], body["emissionInfo"]["airMainEmission"][1])
        self.assertFalse(records[3]["jja_eligible"])
        json.dumps(records, ensure_ascii=False, allow_nan=False)

    def test_units_and_blackness(self):
        body = report([pollutant("1", remark="单位：kg"), pollutant("2", code="/", name="工业废气排放量"), pollutant("0", code="A01010", name="林格曼黑度"), pollutant("1", code="X", name="未知指标"), pollutant("1", remark="单位：kg", unit="吨"), pollutant("1", name="氮氧化物浓度", code="C1", unit="mg/m³")])
        records = extract(body)
        self.assertEqual([row["unit"] for row in records], ["kg", None, "dimensionless", None, "kg", "mg/m3"])
        self.assertEqual(records[4]["unit_status"], "conflict")
        self.assertFalse(records[2]["jja_eligible"])
        self.assertFalse(records[5]["jja_eligible"])
        strict = extract(report([pollutant()]), allow_template_mass_unit=False)[0]
        self.assertIsNone(strict["unit"])

    def test_aliases_identity_and_public_report_id(self):
        records = extract_air(report([pollutant(name="NO<sub>x</sub>"), pollutant(name="二氧化硫")]), {"url": "https://permit.mee.gov.cn/permitrep/report/#/pubView?reportId=public123"}, {"permit_code": "P2"})
        self.assertEqual(records[0]["pollutant_name"], "NOx")
        self.assertEqual(records[0]["report_id"], "public123")
        self.assertIn("permit_code_mismatch", records[0]["identity_conflicts"])
        self.assertTrue(records[1]["pollutant_identity_conflict"])
        self.assertFalse(records[0]["jja_eligible"])

    def test_quarter_mapping_never_uses_total_or_output(self):
        body = report([dict(pollutantCode="A21002", pollutantName="NOx", outletCode="D", firstMonth="5", secondMonth="6", thirdMonth="/", thirdMonthOutput="100", total="0")], "2024年第2季", "季报", 2)
        record = extract(body)[0]
        self.assertEqual((record["month"], record["field"], record["value"]), (6, "thirdMonth", None))
        body["companyInfo"]["reportQuarter"] = 3
        self.assertIn("report_quarter_mismatch", extract(body)[0]["identity_conflicts"])

    def test_annual_hidden_months_not_used(self):
        body = report([dict(pollutantCode="A21002", pollutantName="NOx", total="12", june="1", july="2", august="3", quarter2="4")], "2024年", "年报")
        records = extract(body)
        self.assertEqual(records[0]["field"], "total")
        self.assertEqual(records[0]["value"], 12)
        self.assertIsNone(records[0]["month"])
        self.assertEqual(summarize_jja(records), [])

    def test_collector_metadata_and_period_identity(self):
        metadata = {"report_id": "r1", "report_type": "month", "report_period": "2024年6月月报表", "permit_code": "P1", "official_public_url": "https://example.invalid/public", "public_template_unit": "public air table in tonnes"}
        body = report([pollutant()])
        row = extract_air(body, metadata, {"enterprise_id": "E1", "name": "测试企业", "permit_code": "P1"})[0]
        self.assertEqual(row["source_url"], metadata["official_public_url"])
        self.assertEqual(row["unit_evidence"][0]["template_evidence"], metadata["public_template_unit"])
        self.assertEqual(row["identity_conflicts"], [])
        body["companyInfo"]["reportTime"] = "2024年07月"
        row = extract_air(body, metadata, {})[0]
        self.assertIn("report_period_mismatch", row["identity_conflicts"])
        self.assertFalse(row["jja_eligible"])
        body.pop("companyInfo")
        row = extract_air(body, metadata, {})[0]
        self.assertEqual((row["year"], row["month"]), (2024, 6))

    def test_values_and_json_pointer(self):
        values = ["0", "/", "", None, "-1", "NaN", "Infinity", "1,234.5", "1,2", "1e-3", True]
        records = extract(report([pollutant(value) for value in values], section="airOdd/Section~"))
        self.assertEqual([row["value"] for row in records], [0, None, None, None, None, None, None, 1234.5, None, 0.001, None])
        self.assertEqual(records[0]["json_pointer"], "/emissionInfo/airOdd~1Section~0/0/emitValue")
        self.assertEqual(records[0]["scope"], "unknown:airOdd/Section~")

    def test_absent_field_and_schema_variation_retained(self):
        original = report([{"pollutantName": "氮氧化物", "pollutantCode": "A21002"}, "bad-row"])
        original["emissionInfo"]["airOther"] = {"pollutantName": "未知"}
        records = extract(original)
        self.assertEqual(len(records), 3)
        self.assertFalse(records[0]["field_present"])
        self.assertEqual(records[1]["raw_row"], "bad-row")
        self.assertEqual(records[2]["schema_warnings"], ["section_not_list"])


class SummaryTests(unittest.TestCase):
    def monthly(self, month, value, rid=None, **row_args):
        return extract(report([pollutant(value, **row_args)], f"2024年{month:02d}月"), rid or f"month-{month}")

    def test_complete_decimal_and_month_priority(self):
        rows = self.monthly(6, "0.1") + self.monthly(7, "0.2") + self.monthly(8, "0.3")
        rows += extract(report([dict(pollutantCode="A21002", pollutantName="NOx", outletCode="DA001", firstMonth="9", secondMonth="9")], "2024年第3季", "季报", 3), "quarter")
        summary = summarize_jja(rows)[0]
        self.assertEqual(summary["jja_decimal"], "0.6")
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["months"][1]["selected_priority"], "month")
        self.assertTrue(summary["months"][1]["lower_priority_disagreement"])
        self.assertEqual(len(summary["months"][1]["candidates"]), 2)

    def test_revision_conflict_blocks_without_quarter_fallback(self):
        rows = self.monthly(6, "1") + self.monthly(7, "2", "old") + self.monthly(7, "3", "new") + self.monthly(8, "4")
        rows += extract(report([dict(pollutantCode="A21002", pollutantName="NOx", outletCode="DA001", firstMonth="3", secondMonth="4")], "2024年第3季", "季报", 3), "quarter")
        summary = summarize_jja(rows)[0]
        self.assertEqual(summary["conflict_months"], [7])
        self.assertIsNone(summary["jja"])

    def test_equal_revision_sources_retained(self):
        rows = self.monthly(6, "1") + self.monthly(7, "2", "old") + self.monthly(7, "2.0", "new") + self.monthly(8, "3")
        summary = summarize_jja(rows)[0]
        self.assertEqual(summary["jja"], 6)
        self.assertEqual(len(summary["months"][1]["selected_sources"]), 2)

    def test_duplicate_scope_rows_even_equal_are_ambiguous(self):
        rows = self.monthly(6, "1") + self.monthly(8, "3")
        rows += extract(report([pollutant("2"), pollutant("2")], "2024年07月"), "duplicate")
        summary = summarize_jja(rows)[0]
        self.assertIn("duplicate_scope_rows", summary["months"][1]["conflict_reasons"])
        self.assertFalse(summary["complete"])

    def test_unknown_units_and_mixed_units_not_added(self):
        rows = self.monthly(6, "1", unit="吨") + self.monthly(7, "1000", unit="kg") + self.monthly(8, "1", unit="吨")
        summary = summarize_jja(rows)
        self.assertEqual({item["unit"] for item in summary}, {"t", "kg"})
        self.assertTrue(all(item["jja"] is None for item in summary))
        unknown = sum((self.monthly(m, "1", name="未知指标", code="X") for m in (6, 7, 8)), [])
        self.assertIsNone(summarize_jja(unknown)[0]["jja"])

    def test_scope_permit_outlet_and_pollutant_code_separation(self):
        rows = []
        for month in (6, 7, 8):
            body = report([pollutant("1"), pollutant("2", outlet="DA002")], f"2024年{month}月")
            body["emissionInfo"]["airTotalEmission"] = [pollutant("3", outlet="", portType="全厂合计")]
            rows += extract(body, f"p1-{month}")
            body["companyInfo"]["permitCode"] = "P2"
            rows += extract_air(body, {"report_id": f"p2-{month}"}, {"enterprise_id": "E1", "permit_code": "P2"})
        summaries = summarize_jja(rows)
        self.assertEqual(len(summaries), 6)
        self.assertEqual(sorted(item["jja"] for item in summaries), [3, 3, 6, 6, 9, 9])


@unittest.skipUnless(os.environ.get("MEE_AIR_TEST_MANIFEST"), "optional real public-report cache manifest not supplied")
class RealCacheTests(unittest.TestCase):
    def test_three_reviewed_cached_reports(self):
        manifest = json.loads(Path(os.environ["MEE_AIR_TEST_MANIFEST"]).read_text(encoding="utf-8"))
        targets = {("changle", "2024年第3季度季报表"), ("fenyi", "2023年第03季度季报表"), ("nantong", "2024年6月月报表")}
        found = set()
        for item in manifest:
            identity = (item.get("case"), item.get("report"))
            if identity not in targets:
                continue
            with self.subTest(report=identity):
                body = json.loads(Path(item["path"]).read_text(encoding="utf-8"))
                rows = extract_air(body, item, {"enterprise_id": item["case"], "permit_code": item["permit"]})
                factor = 2 if body["companyInfo"]["reportQuarter"] == 3 else 1
                expected = sum(len(value) for key, value in body["emissionInfo"].items() if key.startswith("air") and isinstance(value, list)) * factor
                self.assertEqual(len(rows), expected)
                self.assertTrue(all(row["raw_row"] is not None for row in rows))
                if item["case"] == "changle":
                    nox = [row for row in rows if row["pollutant_code"] == "A21002"]
                    self.assertAlmostEqual(sum(row["value"] for row in nox if row["month"] == 7 and row["scope"] == "main_outlet"), 304.38)
                    self.assertTrue(all(row["value"] == 0 for row in nox if row["scope"] == "enterprise_total"))
                elif item["case"] == "nantong":
                    pm = next(row for row in rows if row["pollutant_code"] == "A99911" and row["scope"] == "enterprise_total")
                    self.assertEqual(pm["value"], 2.391)
                    gas = next(row for row in rows if row["pollutant_name"] == "工业废气排放量" and row["scope"] == "enterprise_total")
                    self.assertEqual(gas["unit"], "万Nm3")
                else:
                    nox = [row for row in rows if row["pollutant_code"] == "A21002" and row["outlet_code"] == "DA002"]
                    self.assertEqual([row["value"] for row in nox], [1.6, 0.88])
                found.add(identity)
        self.assertEqual(found, targets)


if __name__ == "__main__":
    unittest.main()
