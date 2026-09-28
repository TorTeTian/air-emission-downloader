"""Explicit, inclusive requested periods; no implicit year or season defaults."""

from calendar import monthrange
from datetime import date
import re


def _boundary(value, end=False):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}(?:-\d{2})?", value):
        raise ValueError("Use YYYY-MM or YYYY-MM-DD for start/end")
    parts = [int(v) for v in value.split("-")]
    if len(parts) == 2:
        parts.append(monthrange(*parts)[1] if end else 1)
    return date(*parts)


def _periods_between(start, end):
    year, month = start.year, start.month
    result = []
    while (year, month) <= (end.year, end.month):
        result.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return result


def select_time(*, years=None, months=None, start=None, end=None, periods=None):
    """Resolve one of years(+months), explicit months, or an inclusive range.

    Returned JSON preserves exact day bounds. Partial months are downloadable
    as overlapping source reports but must never be called exact daily totals.
    """
    modes = bool(years) + bool(periods) + bool(start or end)
    if modes != 1:
        raise ValueError("Specify exactly one time mode: --years [--months], --periods, or --start with --end")
    if months is not None and not years:
        raise ValueError("--months requires --years")
    first = last = None
    if start or end:
        if not start or not end:
            raise ValueError("Both --start and --end are required")
        first, last = _boundary(start), _boundary(end, end=True)
        if first > last:
            raise ValueError("The start must not be after the end")
        selected = _periods_between(first, last)
        full_months = first.day == 1 and last.day == monthrange(last.year, last.month)[1]
        mode = "date_range"
    elif periods:
        if any(not re.fullmatch(r"\d{4}-\d{2}", p) for p in periods):
            raise ValueError("--periods requires explicit YYYY-MM values")
        selected = sorted({f"{_boundary(p):%Y-%m}" for p in periods})
        full_months, mode = True, "explicit_months"
    else:
        if any(not isinstance(y, int) or isinstance(y, bool) or not 1900 <= y <= 2099 for y in years):
            raise ValueError("Years must be integers from 1900 to 2099")
        chosen_months = range(1, 13) if months is None else months
        if not chosen_months or any(not isinstance(m, int) or isinstance(m, bool) or not 1 <= m <= 12 for m in chosen_months):
            raise ValueError("Months must be integers from 1 to 12")
        selected = sorted({f"{y:04d}-{m:02d}" for y in years for m in chosen_months})
        full_months, mode = True, "years_months"
    if not selected or any(not 1900 <= int(p[:4]) <= 2099 for p in selected):
        raise ValueError("Requested report years must be within 1900..2099")
    return {
        "mode": mode,
        "periods": selected,
        "years": sorted({int(p[:4]) for p in selected}),
        "requested_start": first.isoformat() if first else None,
        "requested_end": last.isoformat() if last else None,
        "whole_months": full_months,
        "aggregation_rule": "Sum only valid requested full-month values; keep reported quarter/year totals separate; never prorate.",
    }


def time_from_args(args):
    return select_time(**{k: getattr(args, k, None) for k in ("years", "months", "start", "end", "periods")})


def report_period(record):
    """Use the reported period, not submission date; stop on unknown formats."""
    text = str(record.get("reportTime") or record.get("report_period") or "").strip()
    match = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", text)
    if not match:
        raise ValueError("Unrecognized report year: " + text)
    year = int(match[1])
    month = re.search(r"年\s*(\d{1,2})\s*月", text)
    quarter = re.search(r"第?\s*0?([1-4])\s*季", text)
    declared = str(record.get("type") or record.get("report_type") or record.get("reportType") or "")
    if month:
        months = [int(month[1])]
        if not 1 <= months[0] <= 12:
            raise ValueError("Invalid report month: " + text)
        kind = "month"
    elif quarter:
        q = int(quarter[1])
        months, kind = list(range(3 * q - 2, 3 * q + 1)), "quarter"
    elif "年报" in text or declared in {"年报", "年报表", "year", "annual"}:
        months, kind = list(range(1, 13)), "year"
    else:
        raise ValueError("Unrecognized report period; do not silently omit: " + text)
    return {"year": year, "kind": kind, "periods": [f"{year:04d}-{m:02d}" for m in months]}


def relevant_report(record, selection, index_year=None):
    coverage = report_period(record)
    if index_year is not None and coverage["year"] != index_year:
        raise ValueError("Report period differs from the requested annual index")
    return bool(set(coverage["periods"]) & set(selection["periods"]))
