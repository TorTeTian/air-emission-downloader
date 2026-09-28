"""Offline client guard regressions; all HTTP responses are in-memory fixtures.

Run alongside the parser tests with:
    python -B -m unittest discover -s <scripts_directory> -p 'test_*.py' -v

Only TemporaryDirectory is written. These tests never instantiate Net or open a
requests session; a failing guard cannot accidentally make a real HTTP request.
"""

import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import permit_air as client


def search_page(numbers=(), total_pages=1, name="测试企业", include_table=True):
    """Produce the reviewed public table shape without a real server."""
    form = '<form id="mainForm"><input name="tempReportKey" value="fixture"></form>'
    pager = '<script>var totalPages = %d;</script>' % total_pages
    if not include_table:
        return form + pager
    rows = ['<tr>' + ''.join('<th>column</th>' for _ in range(9)) + '</tr>']
    for number in numbers:
        values = ['江苏省', '苏州市', 'P%d' % number, name, '测试行业', '2020-2030', '2020-01-01', '重点管理']
        cells = ''.join('<td>%s</td>' % value for value in values)
        cells += '<td><a href="/getxxgkContent.action?dataid=%032x">详情</a></td>' % number
        rows.append('<tr>' + cells + '</tr>')
    return form + pager + '<table class="tabtd">' + ''.join(rows) + '</table>'


def detail_page(region="江苏省-苏州市-常熟市", permit_code="P1"):
    return (
        '<html><body><div>许可证编号：%s</div><div>所在地区：%s</div><div>发证机关：测试机关</div>'
        '<div>生产经营场所地址：测试工业路1号</div><div>行业类别：测试行业</div></body></html>'
    ) % (permit_code, region)


def arguments(**overrides):
    values = dict(
        mode='region', name=None, permit=None, province='江苏省', city='苏州市', county='常熟市',
        max_pages=0, max_details=0, limit_companies=0, years=[2023, 2024],
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeNetwork:
    """An exhaustive fake: any unexpected route raises instead of networking."""

    def __init__(self, out, pages=None, region="江苏省-苏州市-常熟市", index=None, body_bytes=None):
        self.out = Path(out)
        self.calls = 0
        self.pages = pages or {1: search_page([1])}
        self.region = region
        self.index = [] if index is None else index
        self.body_bytes = b'<html>verification required</html>' if body_bytes is None else body_bytes
        self.routes = []

    def html(self, method, url, data=None, **kwargs):
        self.routes.append((method, url, data))
        self.calls += 1
        if url == client.SEARCH:
            # The initial GET is a valid page; POST responses exercise guards.
            if method == 'GET':
                return search_page([1])
            page = int((data or {}).get('page.pageNo', 1))
            if page not in self.pages:
                raise AssertionError('Unexpected requested search page: %d' % page)
            return self.pages[page]
        if 'getxxgkContent' in url:
            number = int(url.split('dataid=', 1)[1].split('&', 1)[0], 16)
            return detail_page(self.region, 'P%d' % number)
        raise AssertionError('Unexpected HTML route: %s %s' % (method, url))

    def json(self, method, url, data=None, **kwargs):
        self.routes.append((method, url, data))
        self.calls += 1
        if url == client.REGIONS:
            parent = (data or {}).get('parentCode')
            if parent == '000000000000':
                return {'regions': [{'regionname': '江苏省', 'regioncode': '320000000000'}]}
            if parent == '320000000000':
                return {'regions': [{'regionname': '苏州市', 'regioncode': '320500000000'}]}
            raise AssertionError('Unexpected region parent: %r' % parent)
        if url == client.INDEX:
            return self.index
        raise AssertionError('Unexpected JSON route: %s %s' % (method, url))

    def request(self, method, url, **kwargs):
        self.routes.append((method, url, kwargs))
        self.calls += 1
        if url != client.BODY:
            raise AssertionError('Unexpected raw route: %s %s' % (method, url))
        return self.body_bytes, {'access_utc': '2026-01-01T00:00:00+00:00', 'sha256': client.digest(self.body_bytes)}


class SearchGuardTests(unittest.TestCase):
    def test_missing_result_table_is_not_a_valid_zero_result(self):
        with self.assertRaises(client.StopRun):
            client.parse_search(search_page(total_pages=1, include_table=False))

    def test_missing_table_in_filtered_response_stops_discovery(self):
        with TemporaryDirectory() as tmp:
            net = FakeNetwork(tmp, {1: search_page(total_pages=1, include_table=False)})
            with self.assertRaises(client.StopRun):
                client.discover(net, arguments())

    def test_nonfinal_first_page_must_have_ten_rows(self):
        for count in (0, 1, 9, 11):
            with self.subTest(count=count), TemporaryDirectory() as tmp:
                pages = {1: search_page(range(1, count + 1), total_pages=2), 2: search_page([100], total_pages=2)}
                with self.assertRaises(client.StopRun):
                    client.discover(FakeNetwork(tmp, pages), arguments())

    def test_nonfinal_middle_page_must_have_ten_rows(self):
        for count in (0, 9, 11):
            with self.subTest(count=count), TemporaryDirectory() as tmp:
                pages = {
                    1: search_page(range(1, 11), total_pages=3),
                    2: search_page(range(20, 20 + count), total_pages=3),
                    3: search_page([100], total_pages=3),
                }
                with self.assertRaises(client.StopRun):
                    client.discover(FakeNetwork(tmp, pages), arguments())

    def test_final_page_must_have_one_to_ten_rows(self):
        for count in (0, 11):
            with self.subTest(count=count), TemporaryDirectory() as tmp:
                pages = {1: search_page(range(1, 11), total_pages=2), 2: search_page(range(20, 20 + count), total_pages=2)}
                with self.assertRaises(client.StopRun):
                    client.discover(FakeNetwork(tmp, pages), arguments())

    def test_complete_valid_pagination_is_accepted(self):
        for final_count in (1, 10):
            with self.subTest(final_count=final_count), TemporaryDirectory() as tmp:
                pages = {1: search_page(range(1, 11), total_pages=2), 2: search_page(range(20, 20 + final_count), total_pages=2)}
                enterprises, ledger = client.discover(FakeNetwork(tmp, pages), arguments())
                self.assertEqual(len(enterprises), 10 + final_count)
                self.assertEqual(ledger['page_rows'], [10, final_count])
                self.assertTrue(ledger['discovery_complete'])

    def test_incomplete_company_pagination_stops_before_identity_selection(self):
        # Exactly one *observed* exact name is insufficient when page 2 is unread.
        first = search_page(range(1, 11), total_pages=2, name='其他企业')
        first = first.replace('<td>其他企业</td>', '<td>目标企业</td>', 1)
        with TemporaryDirectory() as tmp:
            net = FakeNetwork(tmp, {1: first, 2: search_page([100], total_pages=2, name='目标企业')})
            with patch.object(client, 'detail') as detail_mock:
                with self.assertRaises(client.StopRun):
                    client.discover(net, arguments(mode='company', name='目标企业', max_pages=1))
                detail_mock.assert_not_called()

    def test_region_province_or_city_mismatch_stops(self):
        for region in ('浙江省-苏州市-常熟市', '江苏省-南京市-常熟市'):
            with self.subTest(region=region), TemporaryDirectory() as tmp:
                with self.assertRaises(client.StopRun):
                    client.discover(FakeNetwork(tmp, region=region), arguments())

    def test_same_city_other_county_is_excluded_without_error(self):
        # The endpoint only filters province/city, so other counties are normal.
        with TemporaryDirectory() as tmp:
            enterprises, ledger = client.discover(FakeNetwork(tmp, region='江苏省-苏州市-昆山市'), arguments())
            self.assertEqual(enterprises, [])
            self.assertEqual(ledger['details_inspected'], 1)
            self.assertEqual(ledger['county_matches'], 0)
            self.assertTrue(ledger['discovery_complete'])


class CollectionGuardTests(unittest.TestCase):
    def test_body_html_stops_instead_of_becoming_unsupported_format(self):
        def indexed_report(month, letter):
            return {
                'reportTime': '2023年%d月月报表' % month, 'type': '月报',
                'docUrl': client.VIEW + '#/pubView?reportId=' + letter * 32 + '&provinceSharding=' + 'b' * 32 + '&yearSharding=' + 'c' * 32 + '&reportType=month',
            }
        with TemporaryDirectory() as tmp:
            net = FakeNetwork(tmp, index=[indexed_report(6, 'a'), indexed_report(7, 'd')])
            enterprises = [{'enterprise_id': 'e' * 32, 'permit_code': 'P1', 'name': '测试企业'}]
            with patch.object(client, 'viewer_script', return_value='public-fixture-display-constant'):
                with self.assertRaises(client.StopRun), redirect_stdout(StringIO()):
                    client.collect(net, arguments(mode='company', years=[2023]), enterprises, {'discovery_complete': True})
            attempts = [route for route in net.routes if route[1] == client.BODY]
            self.assertEqual(len(attempts), 1, 'A verification/invalid-JSON response must stop before the next report.')

    def test_zero_enterprises_clears_old_manifests_and_is_not_acquisition_success(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            client.save(root / 'report_manifest.json', [{'status': 'downloaded', 'report_id': 'previous-run'}])
            client.save(root / 'index_coverage.json', [{'status': 'ok', 'year': 2023, 'enterprise_id': 'previous-run'}])
            net = FakeNetwork(tmp)
            with redirect_stdout(StringIO()):
                coverage = client.collect(net, arguments(), [], {'discovery_complete': True, 'report_sample_limited': False})
            self.assertEqual(json.loads((root / 'report_manifest.json').read_text(encoding='utf-8')), [])
            self.assertEqual(json.loads((root / 'index_coverage.json').read_text(encoding='utf-8')), [])
            self.assertEqual(coverage['reports_requested'], 0)
            self.assertEqual(coverage['enterprises'], 0)
            self.assertFalse(coverage['report_acquisition_complete_for_selected'])
            self.assertEqual(net.routes, [])

    def test_no_relevant_reports_does_not_keep_previous_report_manifest(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            client.save(root / 'report_manifest.json', [{'status': 'downloaded', 'report_id': 'previous-run'}])
            client.save(root / 'index_coverage.json', [{'status': 'ok', 'year': 1999, 'enterprise_id': 'previous-run'}])
            net = FakeNetwork(tmp, index=[])
            enterprises = [{'enterprise_id': 'e' * 32, 'permit_code': 'P1', 'name': '测试企业'}]
            with redirect_stdout(StringIO()):
                coverage = client.collect(net, arguments(years=[2023]), enterprises, {'discovery_complete': True})
            self.assertEqual(json.loads((root / 'report_manifest.json').read_text(encoding='utf-8')), [])
            indexes = json.loads((root / 'index_coverage.json').read_text(encoding='utf-8'))
            self.assertEqual(len(indexes), 1)
            self.assertEqual(indexes[0]['year'], 2023)
            self.assertEqual(indexes[0]['relevant_reports'], 0)
            self.assertEqual(coverage['reports_requested'], 0)


if __name__ == '__main__':
    unittest.main()
