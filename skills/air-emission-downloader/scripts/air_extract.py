"""Lossless, offline extraction of MEE public air-emission records.

``extract_air(body, report_meta, enterprise)`` emits every row in emissionInfo
sections whose names start with ``air``. Monthly fields and the public Q2/Q3
month columns are eligible for JJA. Annual/other-quarter totals are retained as
audit records, never disaggregated. No network, file I/O, or third-party imports.

``summarize_jja(rows)`` keeps enterprise totals and outlet/group scopes separate.
It requires three usable months, uses monthly reports before quarterly reports,
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
    elif typ == "quarter" and quarter == 2:
        fields = [(6, "thirdMonth", "month")]
    elif typ == "quarter" and quarter == 3:
        fields = [(7, "firstMonth", "month"), (8, "secondMonth", "month")]
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
                                and period_kind == "month" and target_month in (6, 7, 8)
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
                    "raw_row": deepcopy(raw_row), "schema_warnings": warnings, "jja_eligible": eligible,
                })
                result.append(record)
    return result


def _candidate(row):
    names = ("report_id", "report_type", "report_submit_time", "source_url", "json_pointer", "row_json_pointer", "row_id", "field",
             "value_raw", "value", "value_decimal", "value_status", "unit", "unit_raw", "unit_source", "unit_status", "jja_eligible",
             "identity_conflicts", "pollutant_identity_conflict", "schema_warnings")
    return {name: row.get(name) for name in names}


def summarize_jja(rows):
    """Return JJA groups and all month candidates; never sum across scopes.

    Values in different units or raw pollutant codes produce separate groups.
    An unresolved conflict between valid monthly revisions blocks that month;
    it does not fall through to a quarterly report. Annual rows cannot fill gaps.
    """
    groups = {}
    for row in rows:
        if row.get("period_kind") != "month" or row.get("month") not in (6, 7, 8):
            continue
        outlet_key = row.get("outlet_code") or row.get("outlet_name") or "__group__"
        key = (row.get("enterprise_id"), row.get("permit_code"), row.get("pollutant_key"),
               row.get("scope"), outlet_key, row.get("unit"), row.get("year"))
        groups.setdefault(key, []).append(row)
    results = []
    for key, group in groups.items():
        representative = group[0]
        output = {name: representative.get(name) for name in (
            "enterprise_id", "enterprise_name", "permit_code", "pollutant_key", "pollutant_code", "pollutant_name",
            "scope", "outlet_code", "outlet_name", "unit", "dimension", "year")}
        output["outlet_key"] = key[4]
        months, selected_values = [], []
        for month in (6, 7, 8):
            candidates = [row for row in group if row["month"] == month]
            valid = [row for row in candidates if row.get("jja_eligible") and row.get("value_decimal") is not None]
            preferred = [row for row in valid if row.get("report_type") == "month"]
            pool = preferred or [row for row in valid if row.get("report_type") == "quarter"]
            unique_rows = {}
            for row in pool:
                signature = (row.get("report_id"), row.get("source_url"), row.get("json_pointer"), row.get("value_decimal"))
                unique_rows.setdefault(signature, row)
            pool = list(unique_rows.values())
            documents = {}
            for row in pool:
                document = row.get("report_id") or row.get("source_url") or row.get("source_row_report_id") or "unidentified_report"
                documents.setdefault(document, set()).add(row.get("row_json_pointer"))
            values = {Decimal(row["value_decimal"]) for row in pool}
            duplicate_rows = any(len(pointers) > 1 for pointers in documents.values())
            conflict = len(values) > 1 or duplicate_rows
            selected = pool if len(values) == 1 and not conflict else []
            value = next(iter(values)) if selected else None
            if value is not None:
                selected_values.append(value)
            status = "selected" if selected else "conflict" if conflict else "ineligible" if candidates else "missing"
            entry = {
                "month": month, "status": status, "value": float(value) if value is not None else None,
                "value_decimal": str(value) if value is not None else None,
                "selected_priority": selected[0]["report_type"] if selected else None,
                "conflict_reasons": (["same_priority_value_conflict"] if len(values) > 1 else []) + (["duplicate_scope_rows"] if duplicate_rows else []),
                "lower_priority_disagreement": bool(selected and any(Decimal(row["value_decimal"]) != value for row in valid)),
                "selected_sources": [_candidate(row) for row in selected], "candidates": [_candidate(row) for row in candidates],
            }
            months.append(entry)
        complete = len(selected_values) == 3
        total = sum(selected_values, Decimal(0)) if complete else None
        output.update({
            "months": months, "complete": complete, "status": "complete" if complete else "conflict" if any(item["status"] == "conflict" for item in months) else "incomplete",
            "jja": float(total) if total is not None else None, "jja_value": float(total) if total is not None else None,
            "jja_decimal": str(total) if total is not None else None,
            "missing_months": [item["month"] for item in months if item["status"] in {"missing", "ineligible"}],
            "conflict_months": [item["month"] for item in months if item["status"] == "conflict"],
        })
        results.append(output)
    return sorted(results, key=lambda item: json.dumps([item.get(name) for name in ("enterprise_id", "permit_code", "pollutant_key", "scope", "outlet_key", "unit", "year")], ensure_ascii=False))
