"""Read-only broker history export and quality audit. No synthetic sentiment."""
from __future__ import annotations
from datetime import datetime,timezone,timedelta
import hashlib
import time
import pandas as pd
from .config import ROOT
from .broker import ReadOnlyMT5
from .storage import write_json


def export_history(cfg,days=365):
    broker=ReadOnlyMT5(cfg);details=broker.inspect();mt=broker.mt
    end=datetime.now(timezone.utc);start=end-timedelta(days=days)
    out=ROOT/'runtime/history';out.mkdir(parents=True,exist_ok=True)
    result={'retrieved_at':end.isoformat(),'requested_days':days,'datasets':[],
        'limitations':['Rates are broker bid OHLC, not a complete historical bid/ask tick tape.',
        'Historical commission, corporate actions, financing and margin changes are not reconstructed.',
        'Available depth is constrained by the terminal history cache and broker retention.',
        'This is a data/closed-bar logic audit, not a profitability backtest. Native execution replay and point-in-time event history remain required.']}
    for ticker,m in details['mappings'].items():
        for name,tf,seconds in [('M15',mt.TIMEFRAME_M15,900),('H1',mt.TIMEFRAME_H1,3600),('H4',mt.TIMEFRAME_H4,14400),('D1',mt.TIMEFRAME_D1,86400)]:
            mt.symbol_select(m['symbol'],True)
            rates=mt.copy_rates_range(m['symbol'],tf,start,end)
            row={'ticker':ticker,'symbol':m['symbol'],'timeframe':name,'rows':0}
            if rates is None or not len(rates):
                row['issue']='BROKER_HISTORY_UNAVAILABLE';result['datasets'].append(row);continue
            df=pd.DataFrame(rates)
            # Exclude currently forming bar by terminal-provided position 0.
            current=mt.copy_rates_from_pos(m['symbol'],tf,0,1)
            if current is not None and len(current):df=df[df.time<current[0]['time']]
            path=out/f'{ticker}-{name}.csv';df.to_csv(path,index=False)
            valid=(df.low>0)&(df.high>=df[['open','close']].max(axis=1))&(df.low<=df[['open','close']].min(axis=1))
            delta=df.time.diff().dropna()
            row.update(rows=len(df),invalid_ohlc=int((~valid).sum()),non_increasing_times=int((delta<=0).sum()),
                gaps_over_one_bar=int((delta>seconds).sum()),first_broker_time=int(df.time.min()),last_broker_time=int(df.time.max()),
                file=str(path.relative_to(ROOT)),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            result['datasets'].append(row)
    write_json(out/'quality.json',result)
    return result


if __name__=='__main__':
    from .config import load
    r=export_history(load());print({'datasets':len(r['datasets']),'bars':sum(d['rows'] for d in r['datasets']),'report':'runtime/history/quality.json'})
