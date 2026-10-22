"""Consume native signal journal once; apply executable bid/ask observations."""
from __future__ import annotations
import csv
from .shadow import ShadowLedger, ShadowPosition
from .policy import floor_volume
from .calendar import now_ny


def run_shadow(collector, snapshot, mappings, now):
    store,mt=collector.store,collector.broker.mt
    saved=store.get("shadow_run",{})
    ledger=ShadowLedger(saved.get("ledger"))
    if 'events_sentiment_council' not in ledger.state:
        # Start a separate book at this feature's introduction; never backfill votes.
        fresh=ShadowLedger().state['events_sentiment_council'];fresh['started_at']=now
        ledger.state['events_sentiment_council']=fresh
    contracts={c["symbol"]:c for c in snapshot["contracts"]}
    specs={m["symbol"]:m for m in mappings.values()}
    prices={}; stale=[]; context={}
    for symbol,c in contracts.items():
        quote=mt.symbol_info_tick(symbol)
        if quote is None or not 0<=now-(quote.time-c["server_offset"])<=30:
            stale.append(symbol);continue
        prices[symbol]=(quote.bid,quote.ask)
        frames={}
        for style,tf in (("day",mt.TIMEFRAME_M15),("swing",mt.TIMEFRAME_H4)):
            bars=mt.copy_rates_from_pos(symbol,tf,1,64)
            if bars is None or len(bars)<20: continue
            tr=[max(bars[i]["high"]-bars[i]["low"],abs(bars[i]["high"]-bars[i-1]["close"]),abs(bars[i]["low"]-bars[i-1]["close"])) for i in range(1,len(bars))]
            atr=sum(tr[:14])/14
            for v in tr[14:]: atr=(13*atr+v)/14
            frames[style]=float(atr)
        bars=mt.copy_rates_from_pos(symbol,mt.TIMEFRAME_D1,1,300)
        cross=0
        if bars is not None and len(bars)>=100:
            def ema(period):
                values=[float(bars[0]["close"])]; a=2/(period+1)
                for x in bars[1:]:values.append(a*x["close"]+(1-a)*values[-1])
                return values
            f,s=ema(20),ema(50)
            cross=-1 if f[-2]>=s[-2] and f[-1]<s[-1] else 1 if f[-2]<=s[-2] and f[-1]>s[-1] else 0
        context[symbol]=(frames,cross)
    ledger.mark_equity(prices)
    for symbol,(bid,ask) in prices.items():
        c=contracts[symbol];frames,cross=context[symbol]
        issuer=(c["earnings_close"]>0 and c["earnings_close"]<=now) or c["sec_close_until"]>=now
        macro=any(e["time"]-300<=now<=e.get("end",e["time"])+1800 for e in snapshot["events"])
        # Direction/style-specific management is applied to each isolated book.
        for variant,book in ledger.state.items():
            for style,p in list(book["positions"].items()):
                if p["symbol"]!=symbol:continue
                isolated=ShadowLedger({variant:book})
                isolated.mark(symbol,bid,ask,now,atr=frames.get(style),macro_flatten=macro,issuer_exit=issuer,crossed=cross==-p["direction"])
    journal=collector.directory/"candidates.csv"
    offset=saved.get("offset",0)
    if journal.exists():
        with journal.open("rb") as stream:
            if journal.stat().st_size<offset: raise RuntimeError("Shadow journal truncated; reconciliation required")
            stream.seek(offset)
            while True:
                line=stream.readline()
                if not line or not line.endswith(b"\n"):break
                offset=stream.tell()
                try:
                    r=next(csv.reader([line.decode("utf-8-sig").strip()],delimiter="|"))
                    if len(r) not in (13,15):continue
                    stamp=float(r[0]);symbol=r[1];style="day" if r[2]=="0" else "swing"
                    if symbol not in specs or symbol not in prices or not 0<=now-stamp<=60:continue
                    c=contracts[symbol];sp=specs[symbol];direction=int(r[4])
                    if not c["open"]<=stamp<c["close"]-1800:continue
                    if style=='day': snapshot.setdefault('_day_candidates', []).append(r)
                    entry,stop,target,unit_risk=map(float,r[5:9])
                    carry=c["finance_long"] if direction==1 else c["finance_short"]
                    # Include a conservative slippage allowance at both ends.
                    slippage=2*c["slippage"]*sp["point"]*sp["contract_size"]
                    p=ShadowPosition(symbol,style,r[3],direction,stamp,entry,stop,target,1,sp["contract_size"],c["commission"]+slippage,abs(entry-stop),c["close"],carry if style=="swing" else 0)
                    # Old journal rows have no contemporaneous council evidence.
                    council_ok=style=='day' or len(r)==15 and r[13]=='1'
                    ledger.offer(p,r[10]=="1",r[11]=="1",sp,stamp,prices,unit_risk,entry*sp["contract_size"]*c["margin_rate"],council_ok=council_ok)
                except (ValueError,IndexError):store.audit("SHADOW_INVALID_CANDIDATE",{"offset":offset})
    ledger.mark_equity(prices)
    store.set("shadow_run",{"offset":offset,"ledger":ledger.state,"last":now,"stale_symbols":stale})
    store.set("shadow",ledger.state)
