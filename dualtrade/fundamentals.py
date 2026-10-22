"""Point-in-time SEC transformations. Never infer acceptance from period-end."""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timezone, date, timedelta
import importlib.util
from pathlib import Path


def accepted_filings(submissions):
    rec = submissions["filings"]["recent"]
    result = {a: t for a, t in zip(rec["accessionNumber"], rec["acceptanceDateTime"])}
    for older in submissions.get('historical', []):
        result.update(zip(older.get('accessionNumber', []), older.get('acceptanceDateTime', [])))
    return result


def point_in_time(facts, acceptances, as_of):
    cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    result = deepcopy(facts)
    for taxonomy in result.get("facts", {}).values():
        for concept in taxonomy.values():
            for unit, rows in concept.get("units", {}).items():
                kept = []
                for row in rows:
                    accepted = acceptances.get(row.get("accn"))
                    if not accepted: continue
                    stamp = datetime.fromisoformat(accepted.replace("Z", "+00:00"))
                    if stamp.tzinfo is None: continue
                    if stamp <= cutoff:
                        kept.append({**row, "accepted_at": accepted})
                concept["units"][unit] = kept
    return result


def latest_fact(facts, tags, instant=False):
    for tag in tags:
        rows = facts.get("facts", {}).get("us-gaap", {}).get(tag, {}).get("units", {}).get("USD", [])
        rows = [r for r in rows if ("start" not in r if instant else "start" in r)]
        if rows:
            row = max(rows, key=lambda r: (r["end"], r["accepted_at"]))
            return {"value": row["val"], "period_start": row.get("start"), "period_end": row["end"],
                    "accession": row["accn"], "accepted_at": row["accepted_at"], "tag": tag, "unit": "USD"}
    return None


def analyse(facts, submissions, as_of, sector="", kind="stock"):
    if kind == "etf":
        return {"kind": "etf", "as_of": as_of, "company_ratios": None, "note": "Fund exposure context; issuer EPS is not applicable"}
    acceptances = accepted_filings(submissions)
    filtered = point_in_time(facts, acceptances, as_of)
    path = Path(__file__).parent / "frozen" / "analyst.py"
    spec = importlib.util.spec_from_file_location("frozen_analyst", path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    report = mod.analyse(filtered, quarters=8)
    # Reject independent per-metric trailing windows ending in different quarters.
    latest_end = report["rows"][0]["end"] if report["rows"] else None
    report["ttm"] = {}
    provenance = {}
    for metric in ("revenue", "net_income", "eps_diluted"):
        values, _ = mod.series(filtered, metric)
        ends = sorted(values)[-4:]
        contiguous = len(ends) == 4 and ends[-1] == latest_end
        if contiguous:
            contiguous = all(0 < (date.fromisoformat(values[ends[i]]["start"]) - date.fromisoformat(ends[i-1])).days <= 7 for i in range(1, 4))
        report["ttm"][metric] = sum(values[e]["value"] for e in ends) if contiguous else None
        provenance[metric] = [{**v, "end": e} for e, v in sorted(values.items())[-8:]]
    report.update({"as_of": as_of, "retrieved_at": datetime.now(timezone.utc).isoformat(),
                   "kind": kind, "sector": sector, "quarter_details": provenance,
                   "derived_eps_approximate": any(v.get("derived") for v in provenance["eps_diluted"]),
                   "filing_acceptances": acceptances, "source_facts": filtered})
    report["balance_sheet"] = {
        "cash": latest_fact(filtered, ["CashAndCashEquivalentsAtCarryingValue"], True),
        "debt": latest_fact(filtered, ["LongTermDebtCurrent", "LongTermDebtNoncurrent", "LongTermDebt"], True),
        "operating_cash_flow": latest_fact(filtered, ["NetCashProvidedByUsedInOperatingActivities"]),
        "capital_expenditure": latest_fact(filtered, ["PaymentsToAcquirePropertyPlantAndEquipment"]),
    }
    # Debt values are tagged components, not an invented total; cash flows retain YTD periods.
    report["balance_sheet"]["debt_note"] = "Reported tagged component; not necessarily total debt"
    if sector == "bank":
        for row in report["rows"]: row["gross_margin"] = row["operating_margin"] = None
    report['investment_metrics'] = investment_metrics(filtered, as_of)
    return report


def investment_metrics(facts, as_of):
    """Consolidated USD flows, including YTD differences, with filing provenance."""
    from .investment import number
    tags = {
        'revenue': ['RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues', 'SalesRevenueNet'],
        'net_income': ['NetIncomeLossAvailableToCommonStockholdersBasic', 'NetIncomeLoss'],
        'operating_income': ['OperatingIncomeLoss'],
        'ocf': ['NetCashProvidedByUsedInOperatingActivities'],
        'capex': ['PaymentsToAcquirePropertyPlantAndEquipment', 'PaymentsToAcquireProductiveAssets'],
    }
    nodes = facts.get('facts', {}).get('us-gaap', {})
    provenance = {}
    def rows(tag, unit='USD', taxonomy='us-gaap'):
        source = facts.get('facts', {}).get(taxonomy, {}).get(tag, {}).get('units', {}).get(unit, [])
        return [r for r in source if number(r.get('val')) and r.get('accepted_at')
                and r.get('form', '').split('/')[0] in ('10-Q', '10-K')]
    def flow(names):
        merged = {}
        for tag in names:
            periods = {}
            for r in rows(tag):
                if not r.get('start'): continue
                key = r['start'], r['end']
                if key not in periods or r['accepted_at'] > periods[key]['accepted_at']: periods[key] = r
            values = {}
            for (start, end), r in sorted(periods.items()):
                length = (date.fromisoformat(end)-date.fromisoformat(start)).days+1
                if 80 <= length <= 101:
                    values[end] = {**r, 'tag': tag, 'derived': False}
                elif 160 <= length <= 380:
                    prior = [p for (st, en), p in periods.items() if st == start and
                             80 <= (date.fromisoformat(end)-date.fromisoformat(en)).days <= 101]
                    if prior:
                        p = max(prior, key=lambda v: v['end'])
                        values.setdefault(end, {**r, 'val': r['val']-p['val'],
                            'start': (date.fromisoformat(p['end'])+timedelta(days=1)).isoformat(),
                            'tag': tag, 'derived': True, 'inputs': [p, r]})
            for end, r in values.items(): merged.setdefault(end, r)
        return merged
    series = {k: flow(v) for k, v in tags.items()}
    ends = sorted(series['revenue'])[-8:]
    out = {'complete': False, 'checked_at': datetime.fromisoformat(as_of.replace('Z', '+00:00')).timestamp(),
           'period_end': ends[-1] if ends else '', 'provenance': provenance, 'missing': []}
    if len(ends) != 8: out['missing'].append('EIGHT_QUARTERS')
    for name, values in series.items():
        selected = [values.get(e) for e in ends]
        if len(selected) != 8 or any(v is None for v in selected): out['missing'].append(name); continue
        if any(not 0 < (date.fromisoformat(selected[i]['start'])-date.fromisoformat(ends[i-1])).days <= 7 for i in range(1,8)):
            out['missing'].append(name+':NONCONTIGUOUS'); continue
        provenance[name] = selected
        out[name] = sum(v['val'] for v in selected[-4:])
        if name in ('revenue', 'net_income'): out['previous_'+name] = sum(v['val'] for v in selected[:4])
    def instant(names, unit='USD', taxonomy='us-gaap'):
        candidates = [dict(r, tag=tag) for tag in names for r in rows(tag, unit, taxonomy) if not r.get('start')]
        return max(candidates, key=lambda r: (r['end'], r['accepted_at'])) if candidates else None
    cash = instant(['CashAndCashEquivalentsAtCarryingValue'])
    total = instant(['DebtAndCapitalLeaseObligations', 'DebtAndFinanceLeaseObligations', 'DebtLongtermAndShorttermCombinedAmount'])
    if not total:
        current, noncurrent = instant(['DebtCurrent']), instant(['LongTermDebtNoncurrent'])
        if current and noncurrent and current['end'] == noncurrent['end']:
            total={'val':current['val']+noncurrent['val'],'end':current['end'],'components':[current,noncurrent]}
    # Alternative components are accepted only with explicit short-term evidence.
    if not total:
        components = [instant([tag]) for tag in ('LongTermDebtCurrent', 'LongTermDebtNoncurrent', 'ShortTermBorrowings', 'CommercialPaper')]
        if all(components) and len({r['end'] for r in components}) == 1:
            total = {'val': sum(r['val'] for r in components), 'end': components[0]['end'], 'components': components}
    share_sources=[r for r in (instant(['CommonStockSharesOutstanding'], 'shares'),instant(['EntityCommonStockSharesOutstanding'], 'shares', 'dei')) if r]
    common = max(share_sources,key=lambda r:(r['end'],r['accepted_at'])) if share_sources else None
    diluted = [r for r in rows('WeightedAverageNumberOfDilutedSharesOutstanding', 'shares') if r.get('start') and r['end'] == out['period_end']]
    for name, row in (('cash', cash), ('debt', total), ('shares', common)):
        if not row or row['end'] < out['period_end'] or (name in ('cash','debt') and row['end'] != out['period_end']) or row['val'] < 0:
            out['missing'].append(name); continue
        out[name] = row['val']; provenance[name] = row
    if common and diluted and 'shares' in out:
        newest = max(diluted, key=lambda r: r['accepted_at'])
        ratio = newest['val']/common['val'] if common['val'] > 0 else 0
        if not .5 <= ratio <= 2: out['missing'].append('SHARE_COUNT_OR_SPLIT_INCONSISTENCY')
        out['shares'] = max(common['val'], newest['val']); provenance['diluted_shares'] = newest
    if 'ocf' in out and 'capex' in out: out['fcf'] = out['ocf']-out['capex']
    out['complete'] = not out['missing']
    return out
