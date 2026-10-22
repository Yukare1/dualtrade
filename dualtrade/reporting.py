from __future__ import annotations
from datetime import datetime,timezone
from statistics import median
import math
from .broker import ReadOnlyMT5


def broker_report(cfg):
    broker=ReadOnlyMT5(cfg);broker.connect();mt=broker.mt
    deals=mt.history_deals_get(datetime(2026,1,1,tzinfo=timezone.utc),datetime.now(timezone.utc))
    if deals is None:raise RuntimeError('DEAL_HISTORY_UNAVAILABLE')
    groups={};magics={cfg['magic_day']:'day',cfg['magic_swing']:'swing',cfg['magic_investment']:'investment'}
    for d in deals:
        if d.magic not in magics:continue
        p=groups.setdefault(d.position_id,{'style':magics[d.magic],'symbol':d.symbol,'net_pnl':0,'opened':None,'closed':None,'in_volume':0,'out_volume':0})
        p['net_pnl']+=d.profit+d.commission+d.swap+d.fee
        if d.entry==mt.DEAL_ENTRY_IN:
            if getattr(d, 'comment', '').startswith('DT:INV'): p['style']='investment'
            p['opened']=min(p['opened'] or d.time,d.time);p['in_volume']+=d.volume
        elif d.entry in (mt.DEAL_ENTRY_OUT,mt.DEAL_ENTRY_OUT_BY):
            p['closed']=max(p['closed'] or d.time,d.time);p['out_volume']+=d.volume
    output={}
    for style in ('day','swing','investment'):
        closed=[p for p in groups.values() if p['style']==style and p['opened'] and p['closed'] and p['out_volume']>=p['in_volume']-1e-8]
        hours=sorted((p['closed']-p['opened'])/3600 for p in closed)
        output[style]={'trades':len(closed),'net_pnl':sum(p['net_pnl'] for p in closed),'expectancy':sum(p['net_pnl'] for p in closed)/len(closed) if closed else None,
            'median_hours':median(hours) if hours else None,'p90_hours':hours[max(0,math.ceil(.9*len(hours))-1)] if hours else None}
    return {'styles':output,'positions':groups,'cost_basis':'Actual broker deal profit + commission + swap + fee; unattributed account cash adjustments require reconciliation.'}
