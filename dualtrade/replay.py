"""Prepare and summarize an isolated MT5 technical research replay."""
from __future__ import annotations
import argparse
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import hashlib
import json
import csv
from collections import Counter
from pathlib import Path
from statistics import median
import pandas as pd
from .config import ROOT, load
from .storage import read_json, write_json, atomic_write
from . import calendar as cal

LIMITATIONS = [
    "Technical rules only: point-in-time earnings, macro, fundamentals, sentiment and politician disclosure histories are not supplied. Filtered variants are unavailable, not assumed to pass.",
    "One-minute OHLC modelling and recorded broker spreads cannot resolve intraminute path, true historical slippage or liquidity.",
    "The current verified universe has survivorship bias. Current contract fees, margin and negative swap percentages are cost assumptions, not historical specifications.",
    "Historical broker EET/EEST timestamps are assumed to follow Europe/Athens. Exchange holidays/early closes use XNYS; the current broker 09:35-15:55 session is assumed historically.",
    "The replay is driven by AAPL ticks: off-session equity marks and exact New York midnight are unavailable. The loss check precedes the next-session baseline reset. This is not acceptance of live midnight recovery.",
    "Dividend adjustments and historical corporate actions are not fully reconstructed. Financing debits use broker-day rollovers, Friday triple charges and no assumed credits.",
    "Positions still open at test end are marked separately as test-end liquidations. No age or Friday swing exit is added to the strategy.",
    "Results are in-sample diagnostics with unchanged thresholds, not evidence of future profitability or approval to trade.",
    "Per-style drawdown reconstructs fees at deal time and applies the total modeled financing debit at closure; intraholding financing allocation is approximate. Overall drawdown uses the replay equity marks.",
    "Logged rejections are reported; some shared capacity/risk returns in the EA do not emit a separate rejection record.",
    "Doubled costs change sizing and the minimum net reward/risk filter, so the stress case can select fewer trades."
]


def prepare(start: date, end: date):
    cfg=load();directory=Path(cfg['bridge_directory']).parent/'DualTradeTests'
    inspected=read_json(ROOT/'runtime/preflight-broker.json', {})
    rows=[]
    for ticker, item in inspected.get('mappings',{}).items():
        if not item['verified']:continue
        costs=item['costs'];commission=max(costs['commission_round_turn_per_lot'],costs['minimum_commission_round_turn']/item['minimum'])
        rows.append('|'.join(map(str,(ticker,item['symbol'],commission,costs['slippage_points'],costs['max_margin_rate']))))
    if not rows:raise ValueError('Run read-only preflight first; no verified universe')
    sessions=[];day=start
    while day<=end:
        window=cal.session(day)
        if window:
            op,cl=window
            # Stock CFD schedule is a current-specification assumption, disclosed.
            close=min(cl,datetime.combine(day,datetime.strptime('15:55','%H:%M').time(),cal.NY).timestamp())
            prior=cal.session(cal.previous_session(day))
            offset=int(datetime.fromtimestamp(op,timezone.utc).astimezone(ZoneInfo('Europe/Athens')).utcoffset().total_seconds())
            sessions.append('|'.join(map(str,(int(day.strftime('%Y%m%d')),int(op+900),int(close),offset,int(prior[0]),int(prior[1])))))
        day+=timedelta(days=1)
    atomic_write(directory/'universe.csv','\n'.join(rows)+'\n')
    atomic_write(directory/'sessions.csv','\n'.join(sessions)+'\n')
    metadata={'prepared_at':datetime.now(timezone.utc).isoformat(),'start':start.isoformat(),'end':end.isoformat(),
              'universe':[r.split('|')[:2] for r in rows],'sessions':len(sessions),'limitations':LIMITATIONS,
              'inputs_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (directory/'universe.csv',directory/'sessions.csv')}}
    write_json(ROOT/'build/replay-inputs.json',metadata)
    return {'prepared':True,'symbols':len(rows),'sessions':len(sessions),'directory':str(directory)}


def summarize(directory=None):
    cfg=load();directory=Path(directory) if directory else Path(cfg['bridge_directory']).parent/'DualTradeTests'
    metadata=read_json(ROOT/'build/replay-inputs.json',{})
    result={'generated_at':datetime.now(timezone.utc).isoformat(),'inputs':metadata,'limitations':LIMITATIONS,'runs':{},
            'comparisons':{'technical_plus_events':{'available':False,'reason':'Point-in-time event history unavailable'},
                           'technical_plus_events_plus_sentiment':{'available':False,'reason':'Prospective sentiment evidence required'}}}
    for tag in ('base','stress'):
        path=directory/f'replay-{tag}-deals.csv'
        if not path.exists():continue
        valid=read_json(directory/f'replay-{tag}-valid.json',{})
        if valid.get('valid') is not True or valid.get('version')!='0.1.4':
            result['runs'][tag]={'valid':False,'reason':'Replay consistency validation is missing or failed; no performance conclusions'}
            continue
        deals=pd.read_csv(path,sep='|');curve=pd.read_csv(directory/f'replay-{tag}-curve.csv',sep='|')
        carrying=pd.read_csv(directory/f'replay-{tag}-carry.csv',sep='|')
        trades=[]
        broker_zone=ZoneInfo('Europe/Athens')
        def utc(server):
            wall=datetime.fromtimestamp(int(server),timezone.utc).replace(tzinfo=None)
            return int(wall.replace(tzinfo=broker_zone).timestamp())
        for pid,rows in deals.groupby('position_id'):
            opening=rows[rows.entry==0];closing=rows[rows.entry.isin((1,3))]
            if opening.empty:continue
            complete=not closing.empty and closing.volume.sum()+1e-8>=opening.volume.sum()
            costs=float(rows.estimated_fee.sum())+float(carrying.loc[carrying.position_id==pid,'estimated_financing'].sum())
            trades.append({'position_id':int(pid),'symbol':str(opening.iloc[0].symbol),'style':'day' if int(opening.iloc[0].magic)==cfg['magic_day'] else 'swing',
                'direction':'long' if int(opening.deal_type.iloc[0])==0 else 'short',
                'opened_server':int(opening.server_time.min()),'closed_server':int(closing.server_time.max()) if complete else None,
                'opened_utc':utc(opening.server_time.min()),'closed_utc':utc(closing.server_time.max()) if complete else None,
                'holding_hours':float((utc(closing.server_time.max())-utc(opening.server_time.min()))/3600) if complete else None,
                'gross_pnl':float(rows.gross_pnl.sum()),'estimated_costs':costs,'net_pnl':float(rows.gross_pnl.sum())-costs,'complete':bool(complete),
                'test_end_liquidation':bool(closing['comment'].fillna('').str.contains('end of test',case=False).any())})
        # Report both subperiods without selecting or retuning parameters.
        midpoint=int(curve.utc.min()+(curve.utc.max()-curve.utc.min())/2) if len(curve) else 0
        styles={}
        for style in ('day','swing'):
            selected=[t for t in trades if t['style']==style and t['complete']]
            holds=sorted(t['holding_hours'] for t in selected);pnl=sum(t['net_pnl'] for t in selected)
            events=[];magic=cfg['magic_day'] if style=='day' else cfg['magic_swing']
            for row in deals[deals.magic==magic].itertuples():events.append((utc(row.server_time),row.gross_pnl-row.estimated_fee))
            for t in selected:
                cost=float(carrying.loc[carrying.position_id==t['position_id'],'estimated_financing'].sum())
                events.append((t['closed_utc'],-cost))
            if events and len(curve):
                event_series=pd.Series([v for _,v in events],index=[int(t) for t,_ in events]).groupby(level=0).sum().sort_index().cumsum()
                realized=event_series.reindex(event_series.index.union(pd.Index(curve.utc.astype(int)))).sort_index().ffill().reindex(curve.utc.astype(int)).fillna(0).to_numpy()
                values=pd.Series(realized+curve[style+'_floating'].to_numpy())
                drawdown=float((values.cummax().clip(lower=0)-values).max())
            else:drawdown=0
            styles[style]={'closed_trades':len(selected),'net_pnl':pnl,'net_expectancy':pnl/len(selected) if selected else None,
                'maximum_drawdown_usd_approximate':drawdown,
                'win_rate':sum(t['net_pnl']>0 for t in selected)/len(selected) if selected else None,
                'median_holding_hours':median(holds) if holds else None,'p90_holding_hours':holds[max(0,__import__('math').ceil(.9*len(holds))-1)] if holds else None}
        if len(curve):
            peaks=curve.equity.cummax().clip(lower=1000);dd=(peaks-curve.equity)
            final=float(curve.equity.iloc[-1]);max_dd=float(dd.max())
        else:final=1000;max_dd=0
        rejection={};audit=ROOT/'build'/f'replay-{tag}-audit.csv'
        if audit.exists():
            counts=Counter()
            for row in csv.reader(audit.read_text(encoding='utf-8-sig').splitlines(),delimiter='|'):
                if len(row)!=7 or row[1] not in ('SIGNAL','REJECT'):continue
                reason=row[3];style='unknown' if reason in ('STALE_ENTRY_BAR','HISTORY') else 'day' if float(row[4])==0 else 'swing'
                counts[(style,reason)]+=1
            rejection={style:{reason:n for (s,reason),n in counts.items() if s==style} for style in ('day','swing','unknown')}
        result['runs'][tag]={'valid':True,'cost_multiplier':1 if tag=='base' else 2,'styles':styles,'closed_net_pnl':sum(t['net_pnl'] for t in trades if t['complete']),
            'last_marked_equity':final,'maximum_drawdown_usd':max_dd,'trades':trades,'rejections':rejection,
            'chronological_subperiods':{name:{'closed_trades':len(part),'net_pnl':sum(t['net_pnl'] for t in part)} for name,part in
                (('first_half',[t for t in trades if t['complete'] and t['closed_utc']<midpoint]),('second_half',[t for t in trades if t['complete'] and t['closed_utc']>=midpoint]))},
            'curve_rows':len(curve),'midpoint_utc':midpoint,
            'raw_files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (path,directory/f'replay-{tag}-curve.csv',directory/f'replay-{tag}-carry.csv')}}
    write_json(ROOT/'runtime/reports/historical-replay.json',result)
    lines=['# Historical technical replay','', 'Research diagnosis only. Event, sentiment and politician-council benefits have not been tested.', '',
           '| Cost case | Style | Closed trades | Net P&L | Expectancy/trade | Median hold | P90 hold | Approx. style drawdown |',
           '|---|---|---:|---:|---:|---:|---:|---:|']
    for name,run in result['runs'].items():
        if not run.get('valid'):lines.append(f'| {name} | INVALIDATED | - | - | - | - | - | - |');continue
        for style,s in run['styles'].items():
            number=lambda x:'n/a' if x is None else f'{x:.2f}'
            lines.append(f'| {name} | {style} | {s["closed_trades"]} | ${s["net_pnl"]:.2f} | {number(s["net_expectancy"])} | {number(s["median_holding_hours"])}h | {number(s["p90_holding_hours"])}h | ${s["maximum_drawdown_usd_approximate"]:.2f} |')
    lines+=['','All runs start with USD 1,000. Costs are assumptions based on current verified specifications. Thresholds were not tuned. Sample size is small; no future return claim follows.','',*['- '+x for x in LIMITATIONS]]
    atomic_write(ROOT/'runtime/reports/historical-replay.md','\n'.join(lines)+'\n')
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=('prepare','report'));p.add_argument('--start',default='2026-01-01');p.add_argument('--end',default='2026-10-20');a=p.parse_args()
    if a.command=='prepare':print(json.dumps(prepare(date.fromisoformat(a.start),date.fromisoformat(a.end)),indent=2))
    else:
        r=summarize();print(json.dumps({'runs':{k:{x:v for x,v in t.items() if x not in ('trades','raw_files')} for k,t in r['runs'].items()},'report':'runtime/reports/historical-replay.json'},indent=2))


if __name__=='__main__':main()
