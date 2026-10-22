"""Delayed official House disclosures: weighted evidence, never verified live holdings."""
from __future__ import annotations
from datetime import date,datetime,timedelta,timezone
from io import BytesIO
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re
import time
from urllib.parse import urlparse
import requests
from .config import ROOT
from .calendar import NY,now_ny
from .storage import Store,read_json,write_json,atomic_write,fingerprint
from .frozen import ptr

DIRECTORY=ROOT/'runtime/politicians'
DISCLAIMER='Public disclosures arrive days or weeks after trades (generally up to 45 days). They are not inside information and do not establish current open positions.'


def load_council(cfg=None):
    path=Path((cfg or {}).get('council_file','council.example.json'))
    if not path.is_absolute():path=ROOT/path
    rules=read_json(path)
    if not rules or rules.get('schema')!=1 or rules.get('mode') not in ('veto','shadow'):raise ValueError('Invalid council configuration')
    members=rules['members'];weights=[m['importance'] for m in members]
    if len(members)<2 or len({m['id'] for m in members})!=len(members):raise ValueError('Council needs distinct households')
    if any(not isinstance(w,(int,float)) or not math.isfinite(w) or w<=0 for w in weights):raise ValueError('Invalid importance weight')
    if max(weights)/sum(weights)>rules['maximum_household_weight'] or rules['maximum_household_weight']>.4:raise ValueError('One household exceeds the 40% cap')
    if rules['minimum_households']<2 or not .4<=rules['minimum_base_weight_coverage']<=1 or not 2/3<=rules['opposition_fraction']<=1:raise ValueError('Council consensus safeguards cannot be weakened')
    if not (300<=rules['refresh_seconds']<=86400 and 0<rules['feed_max_age_seconds']<=86400 and 0<rules['max_filing_age_days']<=90 and 0<rules['max_trade_age_days']<=180 and rules['freshness_half_life_days']>0):raise ValueError('Invalid council freshness policy')
    return rules


def household_weights(rules):
    total=sum(m['importance'] for m in rules['members'])
    return {m['id']:m['importance']/total for m in rules['members']}


def _member(filing, rules):
    return next((m for m in rules['members'] if any(ptr.member_match(filing,n) for n in m['index_names'])),None)


def _fetch(url,limit=30_000_000):
    if urlparse(url).hostname!='disclosures-clerk.house.gov':raise ValueError('Official House source required')
    response=requests.get(url,headers={'User-Agent':'DualTrade/0.1.3 personal demo research'},timeout=(5,20))
    response.raise_for_status()
    if len(response.content)>limit:raise ValueError('Disclosure exceeds size limit')
    return response.content


def parse_pdf(data,filing):
    """Cross-check layout parsing against independently counted dated table rows."""
    import pdfplumber
    with pdfplumber.open(BytesIO(data)) as document:
        if len(document.pages)>150:raise ValueError('Disclosure exceeds page limit')
        pages=[p.extract_text(layout=True) or '' for p in document.pages]
        if any(not p.strip() for p in pages):raise ValueError('SCANNED_PDF_COMPLETE_VISUAL_REVIEW_REQUIRED')
        text='\n'.join(pages).replace('\x00','')
        page_count=len(document.pages)
    parsed=ptr.parse_ptr_text(text,filing['filed'])
    pair_count=len(re.findall(r'\b\d{2}/\d{2}/\d{4}\s+\d{2}/\d{2}/\d{4}\s+\$',text))
    if parsed.get('doc_id')!=filing['doc_id'] or parsed.get('status')!='Member':raise ValueError('Filing identity or member status mismatch')
    expected=' '.join((filing['first'],filing['last'])).lower()
    if expected not in (parsed.get('name') or '').lower():raise ValueError('Filing name mismatch')
    if not pair_count or pair_count!=len(parsed['rows']):raise ValueError(f'Incomplete transaction table: dated rows={pair_count}, parsed={len(parsed["rows"])}')
    for row in parsed['rows']:
        if not row.get('date') or not row.get('notified') or row['date']>filing['filed'] or not row.get('asset_type') or row.get('amount_low') is None or row.get('amount_high') is None:raise ValueError('Incomplete or inconsistent transaction row')
        if row['amount_high']<row['amount_low'] or row['owner'] not in ptr.OWNERS:raise ValueError('Ambiguous owner or amount')
    return {'rows':parsed['rows'],'page_count':page_count,'row_count':pair_count,'text':text}


def refresh(cfg=None,store=None,now=None):
    now=time.time() if now is None else now;rules=load_council(cfg);store=store or Store(ROOT/'runtime/state.sqlite3')
    today=now_ny(now).date();cutoff=(today-timedelta(days=rules['max_filing_age_days'])).isoformat()
    collected=[];issues=[];manifest={};covered=set();old=store.get('council_source',{})
    for year in (today.year-1,today.year):
        url=ptr.INDEX_URL%(year,'FD')
        try:
            payload=_fetch(url);atomic_write(DIRECTORY/f'index-{year}.sha256',hashlib.sha256(payload).hexdigest())
            listings=ptr.parse_index(ptr.read_index_bytes(payload),year)
            if not listings:raise ValueError('Empty House index')
            manifest[str(year)]={'url':url,'sha256':hashlib.sha256(payload).hexdigest(),'retrieved_at':now}
        except Exception as exc:
            issues.append(f'INDEX_{year}: {type(exc).__name__}');continue
        for filing in listings:
            member=_member(filing,rules)
            if not member:continue
            covered.add(member['id'])
            # Candidate/annual reports are not inferred into current share counts.
            if filing['type'] not in ('P','A') or not filing.get('filed') or filing['filed']<cutoff or filing['filed']>today.isoformat():continue
            if filing['type']=='A':issues.append(f'{member["id"]}:{filing["doc_id"]}: AMENDMENT_REVIEW_REQUIRED');continue
            key=f'house:{year}:{filing["doc_id"]}';record=store.get('council_document:'+key)
            try:
                if record is None:
                    path=DIRECTORY/'raw'/str(year)/(filing['doc_id']+'.pdf')
                    payload=path.read_bytes() if path.exists() else _fetch(filing['url'],15_000_000)
                    if not payload.startswith(b'%PDF'):raise ValueError('Not a PDF')
                    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(payload)
                    parsed=parse_pdf(payload,filing);atomic_write(path.with_suffix('.txt'),parsed.pop('text'))
                    record={**filing,**parsed,'member_id':member['id'],'first_seen_at':now,'sha256':hashlib.sha256(payload).hexdigest()}
                    store.set('council_document:'+key,record)
                collected.append(record)
            except Exception as exc:issues.append(f'{member["id"]}:{filing["doc_id"]}: {type(exc).__name__}: {str(exc)[:150]}')
    for member in rules['members']:
        if member['id'] not in covered:issues.append(member['id']+': MEMBER_IDENTITY_NOT_FOUND')
    # Preserve documents during an outage; do not refresh their publication times.
    documents={f'{d["year"]}:{d["doc_id"]}':d for d in old.get('documents',[])}
    documents.update({f'{d["year"]}:{d["doc_id"]}':d for d in collected})
    result={'attempt':now,'success':now if not issues else old.get('success',0),'healthy':not issues,'issues':issues,
            'rules_hash':fingerprint(rules),'indexes':manifest,'documents':list(documents.values()),'disclaimer':DISCLAIMER}
    store.set('council_source',result);write_json(DIRECTORY/'source.json',result)
    return result


def evidence_rows(source,rules,as_of):
    """Every record becomes available only when actually first observed."""
    output=[];today=now_ny(as_of).date();members={m['id']:m for m in rules['members']}
    for doc in source.get('documents',[]):
        if doc['member_id'] not in members or doc['first_seen_at']>as_of:continue
        for index,row in enumerate(doc['rows']):
            item={**row,'member_id':doc['member_id'],'member':members[doc['member_id']]['name'],
                  'doc_id':doc['doc_id'],'url':doc['url'],'filed':doc['filed'],'available_at':doc['first_seen_at'],
                  'pdf_sha256':doc['sha256'],'row_index':index,'current_open_position_confirmed':False}
            trade_age=(today-date.fromisoformat(row['date'])).days;filing_age=(today-date.fromisoformat(doc['filed'])).days
            item['eligible']=False;item['reason']=''
            if not 0<=trade_age<=rules['max_trade_age_days'] or not 0<=filing_age<=rules['max_filing_age_days']:item['reason']='STALE_DISCLOSURE'
            elif (row.get('status') or '').lower() not in ('new','original'):item['reason']='AMENDMENT_OR_UNKNOWN_STATUS'
            elif not row.get('ticker') or row.get('asset_type')!='ST':item['reason']='OPTIONS_OR_NON_EQUITY_CONTEXT_ONLY'
            elif row['type'] not in ('P','S','S (partial)'):item['reason']='EXCHANGE_OR_AMBIGUOUS_DIRECTION'
            else:item['eligible']=True
            item['direction']=1 if row['type']=='P' else -1 if row['type'] in ('S','S (partial)') else 0
            item['holding_status']='purchase_reported_holding_unconfirmed' if row['type']=='P' else 'partial_sale_remaining_unknown' if row['type']=='S (partial)' else 'sale_reported_short_not_inferred' if row['type']=='S' else 'unknown'
            item['freshness']=2**(-max(0,trade_age)/rules['freshness_half_life_days'])
            output.append(item)
    return output


def evaluate(source,rules,tickers,as_of=None):
    now=time.time() if as_of is None else as_of;weights=household_weights(rules);rows=evidence_rows(source,rules,now)
    roles_ok=date.fromisoformat(rules['roles_reviewed_on'])<=now_ny(now).date()<date.fromisoformat(rules['roles_valid_until'])
    healthy=(source.get('healthy') is True and source.get('rules_hash')==fingerprint(rules) and 0<=now-source.get('success',0)<=rules['feed_max_age_seconds'] and roles_ok)
    decisions={}
    for ticker in sorted(set(tickers)):
        votes=[]
        for member in rules['members']:
            # One independent household vote; self/spouse/joint never multiply it.
            candidates=[r for r in rows if r['member_id']==member['id'] and r.get('ticker')==ticker and r.get('asset_type')=='ST']
            if not candidates:continue
            latest=max(r['date'] for r in candidates);latest_rows=[r for r in candidates if r['date']==latest]
            if not all(r['eligible'] for r in latest_rows):continue
            directions={r['direction'] for r in latest_rows}
            if len(directions)!=1:continue
            direction=directions.pop();freshness=min(r['freshness'] for r in latest_rows)
            votes.append({'member_id':member['id'],'member':member['name'],'direction':direction,'base_weight':weights[member['id']],
                          'effective_weight':weights[member['id']]*freshness,'trade_date':latest,'doc_ids':sorted({r['doc_id'] for r in latest_rows})})
        positive=sum(v['effective_weight'] for v in votes if v['direction']==1);negative=sum(v['effective_weight'] for v in votes if v['direction']==-1)
        total=positive+negative;coverage=sum(v['base_weight'] for v in votes)
        sufficient=coverage+1e-12>=rules['minimum_base_weight_coverage'] and len(votes)>=rules['minimum_households'] and total>0
        veto_buy=sufficient and sum(v['direction']==-1 for v in votes)>=rules['minimum_households'] and negative/total+1e-12>=rules['opposition_fraction']
        veto_sell=sufficient and sum(v['direction']==1 for v in votes)>=rules['minimum_households'] and positive/total+1e-12>=rules['opposition_fraction']
        ready=healthy or rules['mode']=='shadow'
        decisions[ticker]={'ready':ready,'buy_allowed':ready and not (rules['mode']=='veto' and veto_buy),
                           'sell_allowed':ready and not (rules['mode']=='veto' and veto_sell),'veto_buy':bool(veto_buy),'veto_sell':bool(veto_sell),
                           'status':'SOURCE_OR_ROLE_COVERAGE_UNAVAILABLE' if not healthy else 'INSUFFICIENT_EVIDENCE' if not sufficient else 'WEIGHTED_OPPOSITION' if veto_buy or veto_sell else 'MIXED_EVIDENCE',
                           'base_weight_coverage':coverage,'bullish_weight':positive,'reduction_weight':negative,'household_votes':votes,'as_of':now}
    return {'schema':1,'time':now,'healthy':healthy,'mode':rules['mode'],'weights':weights,'rules_hash':fingerprint(rules),
            'source_success':source.get('success',0),'issues':source.get('issues',[]),'decisions':decisions,'rows':rows,'disclaimer':DISCLAIMER}


def import_review(path,cfg=None,store=None,now=None):
    """Import a complete, visually reviewed transcription of a scanned official PTR.

    The imported rows are research data, never instructions or broker orders.
    Original index identity, PDF hash, dates and declared row count must match.
    """
    now=time.time() if now is None else now;rules=load_council(cfg);store=store or Store(ROOT/'runtime/state.sqlite3')
    review=read_json(Path(path));year=int(review['year']);doc_id=str(review['doc_id'])
    if review.get('reviewed_complete_document') is not True or not review.get('reviewer'):raise ValueError('Complete visual review and reviewer attribution required')
    listings=ptr.parse_index(ptr.read_index_bytes(_fetch(ptr.INDEX_URL%(year,'FD'))),year)
    filing=next((r for r in listings if r['doc_id']==doc_id and r['type']=='P'),None)
    if not filing or not _member(filing,rules):raise ValueError('Official watched-household PTR identity required')
    pdf=DIRECTORY/'raw'/str(year)/(doc_id+'.pdf')
    if not pdf.exists():raise ValueError('Refresh the official PDF before importing a review')
    digest=hashlib.sha256(pdf.read_bytes()).hexdigest()
    if review.get('pdf_sha256')!=digest:raise ValueError('Review does not match the downloaded PDF')
    rows=review.get('rows',[])
    if not rows or len(rows)!=review.get('row_count') or review.get('page_count',0)<1:raise ValueError('Complete page and row counts required')
    normalized=[]
    for row in rows:
        item=dict(row)
        if not all(k in item for k in ('owner','asset','ticker','asset_type','type','date','notified','amount','status')):raise ValueError('Incomplete reviewed transaction')
        if item['owner'] not in ptr.OWNERS or item['type'] not in ptr.TXN_TYPES:raise ValueError('Unrecognized owner or transaction type')
        traded=date.fromisoformat(item['date']);notified=date.fromisoformat(item['notified']);filed=date.fromisoformat(filing['filed'])
        if traded>filed or notified>filed or filed>now_ny(now).date():raise ValueError('Invalid reviewed transaction dates')
        low,high=ptr._amount(item['amount'])
        if low is None or high is None or low>high:raise ValueError('Disclosed amount must remain a range')
        if not re.fullmatch(r'[A-Z]{2}',item['asset_type']) or item['ticker'] is not None and not re.fullmatch(r'[A-Z][A-Z0-9.\-/]{0,9}',item['ticker']):raise ValueError('Ambiguous ticker or asset type')
        item.update(owner_name=ptr.OWNERS[item['owner']],type_name=ptr.TXN_TYPES[item['type']],amount_low=low,amount_high=high,
                    days_to_disclose=(filed-traded).days,late=(filed-traded).days>45)
        normalized.append(item)
    member=_member(filing,rules)
    record={**filing,'member_id':member['id'],'first_seen_at':now,'sha256':digest,'page_count':review['page_count'],
            'row_count':len(rows),'rows':normalized,'reviewer':review['reviewer'],'reviewed_at':now,'reviewed_complete_document':True}
    store.set(f'council_document:house:{year}:{doc_id}',record)
    return {'imported':doc_id,'rows':len(rows),'note':'Run council refresh to reconcile source health; no trading permission changed'}


def snapshot(cfg,tickers,store=None,now=None,refresh_if_due=False):
    now=time.time() if now is None else now;rules=load_council(cfg);store=store or Store(ROOT/'runtime/state.sqlite3');source=store.get('council_source',{})
    if refresh_if_due and now-source.get('attempt',0)>=rules['refresh_seconds']:source=refresh(cfg,store,now)
    result=evaluate(source,rules,tickers,now)
    write_json(DIRECTORY/'latest.json',result)
    return result


def report(cfg,store=None,now=None):
    rules=load_council(cfg);store=store or Store(ROOT/'runtime/state.sqlite3');source=store.get('council_source',{});now=time.time() if now is None else now
    tickers=set(cfg['symbols'])|{r.get('ticker') for d in source.get('documents',[]) for r in d['rows'] if r.get('ticker')}
    result=evaluate(source,rules,tickers,now);write_json(ROOT/'runtime/reports/politician-council.json',result)
    lines=['# Politician disclosure council','',DISCLAIMER,'','Weights reflect political influence only. Current open holdings and returns are not established.',
           '',f'Source coverage: {"ready" if result["healthy"] else "unavailable / incomplete; new swing entries blocked"}.',
           '',*['- '+issue for issue in result['issues']],'','| Household | Weight | Role |','|---|---:|---|']
    for m in rules['members']:lines.append(f'| {m["name"]} | {100*result["weights"][m["id"]]:.1f}% | {m["role"]} |')
    lines+=['','## Current broker-universe swing decisions','','| Symbol | Long entry | Short entry | Evidence |','|---|---|---|---|']
    for ticker in cfg['symbols']:
        d=result['decisions'][ticker];lines.append(f'| {ticker} | {"Allowed" if d["buy_allowed"] else "Blocked"} | {"Allowed" if d["sell_allowed"] else "Blocked"} | {d["status"]} |')
    lines+=['','## Reported transactions','', '| Household / owner | Symbol / asset | Type | Traded | Filed / lag | Disclosed amount range | Vote use | Source |','|---|---|---|---|---|---|---|---|']
    for r in sorted(result['rows'],key=lambda x:(x['filed'],x['date']),reverse=True):
        label=f'{r.get("ticker") or "No ticker"} / {r["asset_type"]}'
        lines.append(f'| {r["member"]} / {r["owner_name"]} | {label} | {r["type_name"]} | {r["date"]} | {r["filed"]} / {r.get("days_to_disclose","?")}d | {r["amount"]} | {"Eligible evidence" if r["eligible"] else r["reason"]} | [PTR {r["doc_id"]}]({r["url"]}) |')
    atomic_write(ROOT/'runtime/reports/politician-council.md','\n'.join(lines)+'\n')
    return {'healthy':result['healthy'],'documents':len(source.get('documents',[])),'rows':len(result['rows']),'weights':result['weights'],
            'issues':result['issues'],'report':'runtime/reports/politician-council.md','disclaimer':DISCLAIMER}
