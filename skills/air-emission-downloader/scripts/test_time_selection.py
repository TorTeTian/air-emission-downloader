"""Offline requested-time tests; no public requests and no persistent data writes."""

from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import permit_air as client
from air_extract import extract_air, requested_rows, summarize_period, summarize_native_periods
from test_air_extract import pollutant, report
from time_selection import select_time, report_period, relevant_report
from verify_air import verify


def monthly(year, month, value="1"):
    return extract_air(report([pollutant(value)], f"{year}年{month:02d}月"), {"report_id": f"m-{year}-{month}"})


class TimeSelectionTests(unittest.TestCase):
    def test_no_implicit_period(self):
        with self.assertRaises(ValueError):
            select_time()

    def test_years_alone_means_full_year(self):
        s = select_time(years=[2024])
        self.assertEqual(s["periods"], [f"2024-{m:02d}" for m in range(1, 13)])

    def test_explicit_months_for_each_year(self):
        self.assertEqual(select_time(years=[2023, 2024], months=[1, 10])["periods"],
                         ["2023-01", "2023-10", "2024-01", "2024-10"])

    def test_cross_year_inclusive_month_range(self):
        s = select_time(start="2023-12", end="2024-02")
        self.assertEqual(s["periods"], ["2023-12", "2024-01", "2024-02"])
        self.assertEqual(s["requested_end"], "2024-02-29")
        self.assertTrue(s["whole_months"])

    def test_noncontiguous_months_do_not_fill_between(self):
        self.assertEqual(select_time(periods=["2024-03", "2023-11", "2023-11"])["periods"], ["2023-11", "2024-03"])

    def test_exact_day_range_is_preserved(self):
        s = select_time(start="2024-01-15", end="2024-02-20")
        self.assertFalse(s["whole_months"])
        self.assertEqual(s["requested_start"], "2024-01-15")
        self.assertEqual(s["requested_end"], "2024-02-20")

    def test_invalid_and_conflicting_time_inputs_rejected(self):
        for kwargs in [dict(years=[2024], start="2024-01", end="2024-02"), dict(months=[1]),
                       dict(years=[2024], months=[0]), dict(years=[2024], months=[13]),
                       dict(years=[2024], months=[]), dict(start="2024-02"),
                       dict(start="2024-03", end="2024-02"), dict(start="2023-02-29", end="2023-03-01"),
                       dict(periods=["2024-13"]), dict(periods=["2024-01-10"])]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                select_time(**kwargs)

    def test_report_selection_is_not_submission_date(self):
        r = {"reportTime": "2023年第04季度季报表", "type": "季报", "reportSubmitTime": "2024-01-20"}
        self.assertTrue(relevant_report(r, select_time(periods=["2023-12"])))
        self.assertFalse(relevant_report(r, select_time(periods=["2024-01"])))
        self.assertEqual(report_period(r)["periods"], ["2023-10", "2023-11", "2023-12"])

    def test_annual_reports_are_retrievable_not_excluded(self):
        r = {"reportTime": "2024年年报表", "type": "年报"}
        self.assertTrue(relevant_report(r, select_time(years=[2024])))
        self.assertTrue(relevant_report(r, select_time(years=[2024], months=[2])))
        self.assertFalse(relevant_report(r, select_time(years=[2023])))

    def test_unknown_period_or_index_mismatch_stops(self):
        with self.assertRaises(ValueError):
            report_period({"reportTime": "2024年未知周期"})
        with self.assertRaises(ValueError):
            relevant_report({"reportTime": "2023年01月"}, select_time(years=[2024]), index_year=2024)


class GeneralAggregationTests(unittest.TestCase):
    def test_all_four_quarters_map_three_calendar_months(self):
        for q in range(1, 5):
            with self.subTest(q=q):
                body = report([pollutant(firstMonth="1", secondMonth="2", thirdMonth="3", total="6")], f"2024年第{q:02d}季度", "季报", q)
                rows = extract_air(body)
                month_rows = [r for r in rows if r["period_kind"] == "month"]
                self.assertEqual([r["month"] for r in month_rows], list(range(3*q-2, 3*q+1)))
                self.assertEqual([r["value"] for r in month_rows], [1, 2, 3])
                native = [r for r in rows if r["period_kind"] == "quarter"]
                self.assertEqual([r["value"] for r in native], [6])

    def test_single_non_summer_month_not_whole_quarter(self):
        rows = extract_air(report([pollutant(firstMonth="10", secondMonth="20", thirdMonth="30", total="60")], "2024年第1季度", "季报", 1))
        s = select_time(periods=["2024-02"])
        filtered = requested_rows(rows, s)
        self.assertEqual([r["month"] for r in filtered if r["period_kind"] == "month"], [2])
        self.assertEqual(summarize_period(filtered, s)[0]["period_total"], 20)
        native = summarize_native_periods(filtered, s)[0]
        self.assertEqual(native["value"], 60)
        self.assertFalse(native["fully_within_requested_months"])

    def test_complete_year_and_native_total_never_double_counted(self):
        rows = sum((monthly(2024, m) for m in range(1, 13)), [])
        rows += extract_air(report([pollutant(total="99", firstMonth="777")], "2024年", "年报"))
        s = select_time(years=[2024])
        summary = summarize_period(rows, s)[0]
        self.assertEqual(summary["period_total"], 12)
        native = summarize_native_periods(rows, s)[0]
        self.assertEqual(native["value"], 99)
        self.assertTrue(native["exactly_matches_request"])

    def test_cross_year_sum_and_month_labels(self):
        rows = monthly(2023, 12, "0.1") + monthly(2024, 1, "0.2") + monthly(2024, 2, "0.3")
        s = select_time(start="2023-12", end="2024-02")
        summary = summarize_period(rows, s)[0]
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["period_total_decimal"], "0.6")
        self.assertEqual([m["period"] for m in summary["months"]], s["periods"])

    def test_noncontiguous_only_sums_requested_months(self):
        rows = monthly(2024, 1, "1") + monthly(2024, 2, "100") + monthly(2024, 3, "3")
        self.assertEqual(summarize_period(rows, select_time(periods=["2024-01", "2024-03"]))[0]["period_total"], 4)

    def test_partial_month_never_returns_exact_daily_total(self):
        s = select_time(start="2024-01-15", end="2024-02-20")
        g = summarize_period(monthly(2024, 1, "1") + monthly(2024, 2, "2"), s)[0]
        self.assertTrue(g["month_coverage_complete"])
        self.assertFalse(g["complete"])
        self.assertEqual(g["status"], "granularity_mismatch")
        self.assertIsNone(g["period_total"])
        self.assertEqual(g["covered_full_months_total"], 3)

    def test_annual_only_retains_actual_total_not_fake_months(self):
        rows = extract_air(report([pollutant(total="120", january="10")], "2024年", "年报"))
        s = select_time(years=[2024])
        self.assertTrue(all(r["month"] is None for r in rows))
        self.assertFalse(summarize_period(rows, s)[0]["complete"])
        self.assertEqual(len(summarize_period(rows, s)[0]["missing_periods"]), 12)
        self.assertEqual(summarize_native_periods(rows, s)[0]["value"], 120)

    def test_zero_is_valid_missing_is_not(self):
        s = select_time(years=[2024], months=[1, 2])
        g = summarize_period(monthly(2024, 1, "0") + monthly(2024, 2, "/"), s)[0]
        self.assertIsNone(g["period_total"])
        self.assertEqual(g["missing_periods"], ["2024-02"])
        self.assertEqual(g["months"][0]["value"], 0)


class CliTimeGuards(unittest.TestCase):
    def test_no_time_fails_before_network(self):
        with TemporaryDirectory() as tmp, patch.object(client, "Net") as net:
            with patch("sys.argv", ["permit_air.py", "run", "--mode", "company", "--name", "测试", "--out", tmp]), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as error:
                    client.main()
            self.assertEqual(error.exception.code, 2)
            net.assert_not_called()

    def test_output_time_change_and_legacy_contract_rejected_before_network(self):
        for contract in [{"version": "1.0.0", "arguments": {}},
                         {"version": client.VERSION, "arguments": {}, "time_selection": select_time(years=[2024], months=[1])}]:
            with self.subTest(contract=contract), TemporaryDirectory() as tmp, patch.object(client, "Net") as net:
                client.save(Path(tmp)/"contract.json", contract)
                with patch("sys.argv", ["permit_air.py", "run", "--mode", "company", "--name", "测试", "--years", "2024", "--months", "2", "--out", tmp]), redirect_stderr(StringIO()):
                    with self.assertRaises(SystemExit):
                        client.main()
                net.assert_not_called()

    def test_collector_non_summer_cross_year_and_readback(self):
        # A complete synthetic archive exercises selection, export and source QA.
        ent = {"enterprise_id": "e1", "permit_code": "P1", "name": "测试企业"}
        class Archive:
            calls = 0
            def __init__(self, out):
                self.out, self.queried_years = Path(out), []
            def json(self, method, url, data):
                self.queried_years.append(int(data["reportYear"]))
                y = data["reportYear"]
                return [{"reportTime": f"{y}年第{q:02d}季度季报表", "type": "季报", "docUrl": f"https://permit.mee.gov.cn/example/{y}/{q}"} for q in range(1, 5)]
        def body_loader(net, r, enterprise, secret):
            coverage = report_period(r)
            year, q = coverage["year"], (int(coverage["periods"][0][-2:])-1)//3+1
            rid = f"{year}-Q{q}"
            body = report([pollutant(firstMonth="1", secondMonth="2", thirdMonth="3", total="6")], r["reportTime"], "季报", q)
            path = net.out/"reports"/rid/"air_report.json"
            client.save(path, body)
            meta = dict(enterprise_id="e1", permit_code="P1", report_id=rid, report_period=r["reportTime"], report_type="季报", source_url=r["docUrl"], air_sha256=client.digest(path.read_bytes()))
            client.save(path.parent/"provenance.json", meta)
            return body, meta
        with TemporaryDirectory() as tmp:
            args = SimpleNamespace(mode="company", start="2023-12", end="2024-01")
            selection = select_time(start=args.start, end=args.end)
            client.save(Path(tmp)/"contract.json", {"version": client.VERSION, "time_selection": selection})
            client.save(Path(tmp)/"enterprises.json", [ent])
            net = Archive(tmp)
            with patch.object(client, "viewer_script", return_value="unused-fixture"), patch.object(client, "get_body", side_effect=body_loader), redirect_stdout(StringIO()):
                coverage = client.collect(net, args, [ent], {"discovery_complete": True})
                self.assertEqual(verify(tmp), 0)
            self.assertEqual(net.queried_years, [2023, 2024])
            self.assertEqual(coverage["reports_downloaded"], 2)
            summary = client.load(Path(tmp)/"period_summary.json")[0]
            self.assertEqual(summary["period_total"], 4)
            self.assertEqual(summary["requested_periods"], ["2023-12", "2024-01"])
            # Deliberately corrupt only the derived total; QA must catch it.
            summary["period_total"] = 123
            client.save(Path(tmp)/"period_summary.json", [summary])
            with redirect_stdout(StringIO()):
                self.assertEqual(verify(tmp), 2)


@unittest.skipUnless(os.environ.get("MEE_AIR_TEST_PERIOD_ARCHIVE"), "optional all-season public-report archive not supplied")
class RealNonSummerCacheTests(unittest.TestCase):
    def test_cached_first_fourth_quarter_and_annual(self):
        archive = Path(os.environ["MEE_AIR_TEST_PERIOD_ARCHIVE"])
        found = set()
        for file in sorted(archive.glob("*/public_report_data.json")):
            title = file.parent.name
            kind = "q1" if "1季" in title else "q4" if "4季" in title else "annual" if "年报" in title else None
            if kind is None or kind in found:
                continue
            body = json.loads(file.read_text(encoding="utf-8"))
            rows = extract_air(body, {"report_period": title})
            if not rows:
                continue
            monthly_rows = [r for r in rows if r["period_kind"] == "month"]
            if kind == "annual":
                self.assertEqual(monthly_rows, [])
                self.assertTrue(any(r["field"] == "total" for r in rows))
            else:
                expected = {1, 2, 3} if kind == "q1" else {10, 11, 12}
                self.assertEqual({r["month"] for r in monthly_rows}, expected)
                for r in monthly_rows:
                    self.assertEqual(r["value_raw"], r["raw_row"].get(r["field"]))
            found.add(kind)
        self.assertEqual(found, {"q1", "q4", "annual"})


if __name__ == "__main__":
    unittest.main()
