"""Lossless, offline extraction of MEE public air-emission records.

``extract_air(body, report_meta, enterprise)`` emits every row in emissionInfo
sections whose names start with ``air``. All four quarters map their three
monthly fields to calendar months. Native quarter/year totals are retained
separately, never disaggregated. No network, file I/O, or third-party imports.

``summarize_period(rows, selection)`` keeps enterprise/outlet scopes separate.
It requires every requested month, uses monthly reports before quarterly reports,
and refuses conflicting revisions or ambiguous duplicate rows. Different raw
pollutant codes and different physical units are intentionally not merged.
"""

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import html
import json
import re
import unicodedata
from urllib.parse import parse_qs, urlparse


_MISSING = object()
_KNOWN_CODES = {
    "A21002": ("NOx", {"nox", "氮氧化物"}),
    "A21026": ("SO2", {"so2", "二氧化硫"}),
    "A34002": ("颗粒物", {"颗粒物"}),
    "A99911": ("颗粒物", {"颗粒物"}),
    "A34013": ("烟尘", {"烟尘"}),
    "A34012": ("粉尘", {"粉尘"}),
    "A20057": ("汞及其化合物", {"汞及其化合物"}),
    "A21001": ("氨", {"氨", "氨（氨气）", "氨(氨气)", "nh3"}),
    "A99901": ("VOCs", {"vocs", "挥发性有机物", "挥发性有机物(vocs)", "挥发性有机物（vocs）"}),
}
_MASS_NAMES = re.compile(
    r"氮氧化物|二氧化硫|颗粒物|烟尘|粉尘|汞|铅|镉|铬|镍|砷|锰|铜|锌|锡|锑|铊|"
    r"钴|硒|钡|银|铍|氨|氯化氢|氟化氢|氟化物|氯气|硫化氢|硫酸雾|氰化氢|"
    r"一氧化碳|二氧化碳|总烃|非甲烷总烃|挥发性有机物|苯|酚|醛|醇|酮|胺|"
    r"二噁英|二恶英|沥青烟|油烟|nox|so2|vocs|nmhc|nh3|hcl|hf|h2s|co2",
    re.I,
)
_NUMBER = re.compile(r"[+]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_UNIT_NOTE = re.compile(r"单位\s*(?:为|[:：=])?\s*([^,，;；。\n]+)", re.I)
_UNIT_FIELDS = ("unit", "unitName", "emissionUnit", "emitUnit", "pollutantUnit", "measureUnit", "valueUnit")
_ADDITIVE_UNITS = {"t": "mass", "kg": "mass", "g": "mass", "mg": "mass", "m3": "volume", "Nm3": "volume", "万m3": "volume", "万Nm3": "volume"}


def _text(value):
    if value is None or value is _MISSING:
        return ""
    return unicodedata.normalize("NFKC", html.unescape(re.sub(r"<[^>]*>", "", str(value)))).strip()


def _key(value):
    return re.sub(r"\s+", "", _text(value)).casefold()


def _pointer(value):
    return str(value).replace("~", "~0").replace("/", "~1")


def _parse_number(raw):
    if raw is _MISSING:
        return None, "not_reported"
    if raw is None:
        return None, "missing"
    if isinstance(raw, bool):
        return None, "invalid"
    text = _text(raw)
    if text.casefold() in {"", "/", "-", "--", "—", "n/a", "na", "null", "未填", "未填报"}:
        return None, "not_reported"
    if text in {"不适用", "无此项"}:
        return None, "not_applicable"
    if not _NUMBER.fullmatch(text):
        return None, "invalid"
    try:
        value = Decimal(text.replace(",", ""))
        if not value.is_finite() or value < 0:
            return None, "invalid"
        # JSON consumers must not receive Infinity after conversion to float.
        if abs(value) > Decimal("1e308"):
            return None, "invalid"
        return value, "reported_zero" if value == 0 else "reported_numeric"
    except InvalidOperation:
        return None, "invalid"


def _normalize_unit(raw):
    text = re.sub(r"\s+", "", _text(raw)).strip("()（）[]【】.,，;；。")
    key = text.casefold().replace("立方米", "m3").replace("米3", "m3").replace("^3", "3")
    aliases = {
        "t": "t", "吨": "t", "tonne": "t", "tonnes": "t", "kg": "kg", "千克": "kg", "公斤": "kg",
        "g": "g", "克": "g", "mg": "mg", "毫克": "mg", "m3": "m3", "nm3": "Nm3",
        "标m3": "Nm3", "标准m3": "Nm3", "万m3": "万m3", "万nm3": "万Nm3", "万标m3": "万Nm3",
        "万标准m3": "万Nm3", "无量纲": "dimensionless", "级": "grade", "mg/m3": "mg/m3",
        "mg/nm3": "mg/Nm3", "kg/h": "kg/h", "g/s": "g/s", "%": "%",
    }
    return aliases.get(key)


def _pollutant(row):
    raw_code = row.get("pollutantCode")
    raw_name = row.get("pollutantName")
    code, name = _text(raw_code), _text(raw_name)
    known = _KNOWN_CODES.get(code.upper())
    canonical = known[0] if known else name
    conflict = bool(known and name and _key(name) not in {_key(alias) for alias in known[1]})
    if not known:
        for normal, aliases in _KNOWN_CODES.values():
            if _key(name) in {_key(alias) for alias in aliases}:
                canonical = normal
                break
    if code and code != "/":
        identity = "code:" + code.upper()
    elif name:
        identity = "name:" + _key(canonical)
    else:
        identity = ""
    if code.upper() == "A01010" or "林格曼" in name or "黑度" in name:
        kind = "index"
    elif re.search(r"废气(?:排放)?量|烟气(?:排放)?量|气体体积", name):
        kind = "volume"
    elif "浓度" in name:
        kind = "concentration"
    elif "速率" in name or "流量" in name:
        kind = "rate"
    elif known or (name and _MASS_NAMES.search(name)):
        kind = "mass"
    else:
        kind = "unknown"
    return raw_code, raw_name, code, canonical, identity, kind, conflict


def _unit(row, metric_kind, report_meta, report_type):
    evidence = []
    remark = _text(row.get("remark"))
    for match in _UNIT_NOTE.finditer(remark):
        raw = match.group(1).strip()
        # A note may continue with prose; only a known leading unit is accepted.
        normalized = _normalize_unit(raw)
        if normalized is None:
            for prefix in re.split(r"[\s,，;；。]", raw):
                normalized = _normalize_unit(prefix)
                if normalized:
                    raw = prefix
                    break
        evidence.append({"raw": raw, "unit": normalized, "source": "row.remark"})
    for field in _UNIT_FIELDS:
        if _text(row.get(field)) not in {"", "/", "-"}:
            evidence.append({"raw": row[field], "unit": _normalize_unit(row[field]), "source": "row." + field})
    if evidence:
        chosen = evidence[0]  # Explicit remarks precede the row unit fields.
        recognized = {item["unit"] for item in evidence if item["unit"] is not None}
        status = "conflict" if len(recognized) > 1 else "known" if chosen["unit"] else "unknown"
        if any(item["unit"] is None for item in evidence) and len(evidence) > 1:
            status = "conflict"
    elif metric_kind == "index":
        chosen = {"raw": None, "unit": "dimensionless", "source": "indicator_name:blackness_index"}
        evidence.append(chosen)
        status = "known"
    elif metric_kind == "mass" and report_meta.get("allow_template_mass_unit", True):
        # The reviewed viewer's monthly air header says actual emissions (tonnes).
        # Quarter/year air tables share a tonnes permit header, but their actual
        # columns do not repeat the unit. This is recorded as template evidence,
        # never fabricated as an explicit row unit.
        raw = report_meta.get("air_table_unit", report_meta.get("table_unit", "吨"))
        source = report_meta.get("air_table_unit_source", report_meta.get("table_unit_source"))
        source = source or (
            "reviewed_official_viewer:air_month_actual_emission_header_tonnes" if report_type == "month"
            else "reviewed_official_viewer:air_table_mass_unit_convention_from_tonnes_header"
        )
        chosen = {"raw": raw, "unit": _normalize_unit(raw), "source": source}
        if report_meta.get("public_template_unit"):
            chosen["template_evidence"] = report_meta["public_template_unit"]
        evidence.append(chosen)
        status = "template_default" if chosen["unit"] else "unknown"
    else:
        chosen = {"raw": None, "unit": None, "source": "unresolved"}
        status = "unknown"
    normalized = chosen["unit"]
    dimension = _ADDITIVE_UNITS.get(normalized)
    dimension_conflict = bool(
        dimension and metric_kind not in {"unknown", dimension}
        or metric_kind == "mass" and normalized in {"dimensionless", "grade", "mg/m3", "mg/Nm3", "kg/h", "g/s", "%"}
    )
    if dimension_conflict:
        status = "conflict"
    return {
        "unit_raw": chosen["raw"], "unit": normalized, "unit_source": chosen["source"],
        "unit_status": status, "unit_evidence": evidence, "dimension": dimension or metric_kind,
        "unit_additive": dimension in {"mass", "volume"} and status in {"known", "template_default"},
    }


def _report_context(body, report_meta, enterprise):
    info = body.get("companyInfo") or report_meta.get("companyInfo") or {}
    metadata_time = report_meta.get("reportTime") or report_meta.get("report_time") or report_meta.get("report_period") or report_meta.get("report") or ""
    time_raw = info.get("reportTime") or metadata_time
    time_text = _text(time_raw)
    type_raw = info.get("reportType") or report_meta.get("report_type") or report_meta.get("reportType") or report_meta.get("type")
    type_key = _key(type_raw)
    typ = {"月报": "month", "月报表": "month", "month": "month", "monthly": "month", "季报": "quarter", "季报表": "quarter", "quarter": "quarter", "quarterly": "quarter", "年报": "year", "年报表": "year", "year": "year", "annual": "year"}.get(type_key, "unknown")
    year_match = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", time_text)
    year = int(year_match[1]) if year_match else None
    if year is None:
        raw_year = info.get("reportYear") or report_meta.get("year")
        year = int(raw_year) if re.fullmatch(r"(?:19|20)\d{2}", str(raw_year or "")) else None
    month_match = re.search(r"年\s*(\d{1,2})\s*月", time_text)
    month = int(month_match[1]) if month_match and 1 <= int(month_match[1]) <= 12 else None
    quarter_match = re.search(r"第?\s*([1-4])\s*季", time_text.replace("第0", "第"))
    raw_quarter = info.get("reportQuarter") or report_meta.get("report_quarter") or report_meta.get("reportQuarter")
    quarter = int(raw_quarter) if re.fullmatch(r"0?[1-4]", str(raw_quarter or "")) else (int(quarter_match[1]) if quarter_match else None)
    conflicts = []
    if quarter_match and quarter is not None and int(quarter_match[1]) != quarter:
        conflicts.append("report_quarter_mismatch")
    if metadata_time and info.get("reportTime"):
        expected_time = _text(metadata_time)
        expected_year = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", expected_time)
        expected_month = re.search(r"年\s*(\d{1,2})\s*月", expected_time)
        expected_quarter = re.search(r"第?\s*0?([1-4])\s*季", expected_time)
        if ((expected_year and year is not None and int(expected_year[1]) != year)
                or (typ == "month" and expected_month and month is not None and int(expected_month[1]) != month)
                or (typ == "quarter" and expected_quarter and quarter is not None and int(expected_quarter[1]) != quarter)):
            conflicts.append("report_period_mismatch")
    source_url = report_meta.get("source_url") or report_meta.get("url") or report_meta.get("docUrl") or report_meta.get("official_public_url") or ""
    public_id = ""
    if source_url:
        parsed = urlparse(source_url)
        params = parse_qs(parsed.query)
        if "?" in parsed.fragment:
            params.update(parse_qs(parsed.fragment.split("?", 1)[1]))
        public_id = (params.get("reportId") or [""])[0]
    report_id = report_meta.get("report_id") or report_meta.get("reportId") or public_id or info.get("reportId") or ""
    permit = _text(info.get("permitCode") or report_meta.get("permit_code") or report_meta.get("permit") or enterprise.get("permit_code") or enterprise.get("permitCode"))
    expected = _text(report_meta.get("permit_code") or report_meta.get("permit") or enterprise.get("permit_code") or enterprise.get("permitCode"))
    if expected and permit and expected != permit:
        conflicts.append("permit_code_mismatch")
    enterprise_id = str(enterprise.get("enterprise_id") or enterprise.get("dataid") or enterprise.get("id") or permit)
    enterprise_name = info.get("enterName") or info.get("companyName") or enterprise.get("enterprise_name") or enterprise.get("name") or ""
    return {
        "enterprise_id": enterprise_id, "enterprise_name": enterprise_name, "permit_code": permit,
        "expected_permit_code": expected, "report_id": str(report_id), "report_type": typ,
        "report_type_raw": type_raw, "report_time": time_raw, "report_year": year, "year": year,
        "report_quarter": quarter, "report_month": month, "source_url": source_url,
        "report_submit_time": info.get("reportSubmitTime"), "identity_conflicts": conflicts,
    }


def _scope(section, row):
    port_type, name, outlet = _text(row.get("portType")), _text(row.get("outletName")), _text(row.get("outletCode"))
    if section == "airTotalEmission" or port_type == "全厂合计":
        return "enterprise_total"
    if section == "airUnorganizedEmission" or "无组织" in port_type or name == "无组织排放":
        return "fugitive_group_total" if not outlet or "合计" in port_type else "fugitive_outlet"
    if section == "airMinorEmission":
        return "minor_group_total" if not outlet or "合计" in port_type else "minor_outlet"
    if section == "airMainEmission":
        return "main_group_total" if "合计" in port_type else "main_outlet"
    return "unknown:" + section


def extract_air(body, report_meta=None, enterprise=None):
    """Extract auditable records without changing input objects.

    ``report_meta`` accepts report_id, source_url (also url/docUrl), permit_code,
    type/report_type, reportTime/report/report_period, companyInfo, and optional
    air_table_unit/source. public_template_unit is retained as evidence text.
    Set allow_template_mass_unit=False to require explicit row-level units.
    ``enterprise`` accepts enterprise_id (or dataid/id), name, and permit_code.
    All returned values are JSON serializable, including a copy of each raw row.
    """
    report_meta, enterprise = report_meta or {}, enterprise or {}
    context = _report_context(body, report_meta, enterprise)
    typ, quarter, month = context["report_type"], context["report_quarter"], context["report_month"]
    if typ == "month":
        fields = [(month, "emitValue", "month")]
    elif typ == "quarter" and quarter in (1, 2, 3, 4):
        fields = [(3 * quarter - 2 + i, field, "month")
                  for i, field in enumerate(("firstMonth", "secondMonth", "thirdMonth"))]
        fields.append((None, "total", "quarter"))
    elif typ in {"year", "quarter"}:
        fields = [(None, "total", typ)]
    else:
        fields = [(None, None, "unknown")]
    result = []
    emission_info = body.get("emissionInfo") or {}
    if not isinstance(emission_info, dict):
        return result
    for section, raw_rows in emission_info.items():
        if not str(section).casefold().startswith("air") or raw_rows is None:
            continue
        section_warning = []
        if isinstance(raw_rows, list):
            items = list(enumerate(raw_rows))
        else:
            items = [(None, raw_rows)]
            section_warning = ["section_not_list"]
        for row_index, raw_row in items:
            row = raw_row if isinstance(raw_row, dict) else {}
            base_pointer = "/emissionInfo/" + _pointer(section) + ("/" + str(row_index) if row_index is not None else "")
            raw_code, raw_name, code, canonical, pollutant_key, metric, pollutant_conflict = _pollutant(row)
            unit_info = _unit(row, metric, report_meta, typ)
            scope = _scope(section, row)
            for target_month, field, period_kind in fields:
                raw = row.get(field, _MISSING) if field else _MISSING
                value, value_status = _parse_number(raw)
                warnings = section_warning + ([] if isinstance(raw_row, dict) else ["row_not_object"])
                eligible = bool(value is not None and unit_info["unit_additive"] and pollutant_key
                                and not pollutant_conflict and not context["identity_conflicts"]
                                and (period_kind == "month" and target_month in range(1, 13)
                                     or period_kind == "quarter" and quarter in (1, 2, 3, 4)
                                     or period_kind == "year")
                                and context["year"] is not None and not warnings and not scope.startswith("unknown:"))
                record = dict(context, **unit_info)
                if not record["report_id"]:
                    record["report_id"] = _text(row.get("reportId"))
                record.update({
                    "month": target_month, "period_kind": period_kind, "section": section,
                    "scope": scope, "outlet_code": _text(row.get("outletCode")), "outlet_name": _text(row.get("outletName")),
                    "port_type_raw": row.get("portType"), "pollutant_code_raw": raw_code,
                    "pollutant_name_raw": raw_name, "pollutant_code": code, "pollutant_name": canonical,
                    "pollutant_key": pollutant_key or "unknown:" + base_pointer, "metric_kind": metric,
                    "pollutant_identity_conflict": pollutant_conflict, "field": field,
                    "field_present": raw is not _MISSING, "value_raw": None if raw is _MISSING else raw,
                    "value": float(value) if value is not None else None,
                    "value_decimal": str(value) if value is not None else None, "value_status": value_status,
                    "json_pointer": base_pointer + ("/" + _pointer(field) if field else ""),
                    "row_json_pointer": base_pointer, "row_id": _text(row.get("id")),
                    "source_row_report_id": _text(row.get("reportId")), "remark": row.get("remark"),
                    "raw_row": deepcopy(raw_row), "schema_warnings": warnings, "aggregation_eligible": eligible,
                })
                result.append(record)
    return result


def _candidate(row):
    names = ("report_id", "report_type", "report_submit_time", "source_url", "json_pointer", "row_json_pointer", "row_id", "field",
             "value_raw", "value", "value_decimal", "value_status", "unit", "unit_raw", "unit_source", "unit_status", "aggregation_eligible",
             "identity_conflicts", "pollutant_identity_conflict", "schema_warnings")
    return {name: row.get(name) for name in names}


def _choose(candidates, monthly=True):
    valid = [r for r in candidates if r.get("aggregation_eligible") and r.get("value_decimal") is not None]
    preferred = [r for r in valid if r.get("report_type") == "month"] if monthly else valid
    pool = preferred or ([r for r in valid if r.get("report_type") == "quarter"] if monthly else [])
    unique = {}
    for r in pool:
        signature = (r.get("report_id"), r.get("source_url"), r.get("json_pointer"), r.get("value_decimal"))
        unique.setdefault(signature, r)
    pool = list(unique.values())
    documents = {}
    for r in pool:
        document = r.get("report_id") or r.get("source_url") or r.get("source_row_report_id") or "unidentified_report"
        documents.setdefault(document, set()).add(r.get("row_json_pointer"))
    values = {Decimal(r["value_decimal"]) for r in pool}
    duplicate = any(len(pointers) > 1 for pointers in documents.values())
    conflict = len(values) > 1 or duplicate
    selected = pool if len(values) == 1 and not conflict else []
    value = next(iter(values)) if selected else None
    return {
        "status": "selected" if selected else "conflict" if conflict else "ineligible" if candidates else "missing",
        "value": float(value) if value is not None else None,
        "value_decimal": str(value) if value is not None else None,
        "selected_priority": selected[0]["report_type"] if selected else None,
        "conflict_reasons": (["same_priority_value_conflict"] if len(values) > 1 else []) + (["duplicate_scope_rows"] if duplicate else []),
        "lower_priority_disagreement": bool(selected and any(Decimal(r["value_decimal"]) != value for r in valid)),
        "selected_sources": [_candidate(r) for r in selected], "candidates": [_candidate(r) for r in candidates],
    }


def row_periods(row):
    year, month, quarter = row.get("year"), row.get("month"), row.get("report_quarter")
    if year is None:
        return []
    if row.get("period_kind") == "month" and month in range(1, 13):
        months = [month]
    elif row.get("period_kind") == "quarter" and quarter in (1, 2, 3, 4):
        months = range(3 * quarter - 2, 3 * quarter + 1)
    elif row.get("period_kind") == "year":
        months = range(1, 13)
    else:
        return []
    return [f"{year:04d}-{m:02d}" for m in months]


def requested_rows(rows, selection):
    selected = set(selection["periods"])
    # Keep unparseable records as audit rows, not as silently eligible values.
    return [r for r in rows if not row_periods(r) or selected.intersection(row_periods(r))]


def _group_key(row):
    return (row.get("enterprise_id"), row.get("permit_code"), row.get("pollutant_key"),
            row.get("scope"), row.get("outlet_code") or row.get("outlet_name") or "__group__", row.get("unit"))


def _group_fields(row):
    result = {name: row.get(name) for name in (
        "enterprise_id", "enterprise_name", "permit_code", "pollutant_key", "pollutant_code", "pollutant_name",
        "scope", "outlet_code", "outlet_name", "unit", "dimension")}
    result["outlet_key"] = _group_key(row)[4]
    return result


def summarize_period(rows, selection):
    """Sum only the explicitly selected full calendar months, including across years.

    Native totals never fill missing monthly values or enter the same sum.
    A partial-month date request can expose overlapping records, not an exact
    daily total. No implicit season and no inferred missing-month zeroes.
    """
    groups = {}
    for row in requested_rows(rows, selection):
        groups.setdefault(_group_key(row), []).append(row)
    results = []
    for key, group in groups.items():
        output = _group_fields(group[0])
        months = []
        for period in selection["periods"]:
            candidates = [r for r in group if r.get("period_kind") == "month" and row_periods(r) == [period]]
            months.append(dict(_choose(candidates), period=period, year=int(period[:4]), month=int(period[5:])))
        covered = all(m["status"] == "selected" for m in months)
        complete = covered and selection["whole_months"]
        total = sum((Decimal(m["value_decimal"]) for m in months), Decimal(0)) if covered else None
        output.update({
            "requested_periods": selection["periods"], "requested_start": selection["requested_start"], "requested_end": selection["requested_end"],
            "months": months, "month_coverage_complete": covered, "complete": complete,
            "status": "complete" if complete else "conflict" if any(m["status"] == "conflict" for m in months) else "granularity_mismatch" if not selection["whole_months"] else "incomplete",
            "period_total": float(total) if complete else None, "period_total_decimal": str(total) if complete else None,
            "covered_full_months_total": float(total) if total is not None else None,
            "missing_periods": [m["period"] for m in months if m["status"] in {"missing", "ineligible"}],
            "conflict_periods": [m["period"] for m in months if m["status"] == "conflict"],
        })
        results.append(output)
    return sorted(results, key=lambda item: json.dumps(_group_key(item), ensure_ascii=False))


def summarize_native_periods(rows, selection):
    """Resolve reported quarter/year totals separately, without disaggregation."""
    groups = {}
    selected = set(selection["periods"])
    for row in rows:
        periods = row_periods(row)
        if row.get("period_kind") in {"quarter", "year"} and selected.intersection(periods):
            groups.setdefault((_group_key(row), row["period_kind"], tuple(periods)), []).append(row)
    result = []
    for (_, kind, periods), candidates in groups.items():
        entry = dict(_group_fields(candidates[0]), **_choose(candidates, monthly=False))
        entry.update(period_kind=kind, covered_periods=list(periods),
                     fully_within_requested_months=selection["whole_months"] and set(periods).issubset(selected),
                     exactly_matches_request=selection["whole_months"] and set(periods) == selected,
                     aggregation_rule="Original reported total only; do not add to monthly totals or prorate.")
        result.append(entry)
    return sorted(result, key=lambda item: json.dumps([_group_key(item), item["period_kind"], item["covered_periods"]], ensure_ascii=False))
