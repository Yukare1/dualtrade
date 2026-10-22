"""Prospective versioned portfolios. No legacy swing history is reclassified."""
from copy import deepcopy
from .investment import candidate, sized_order, exit_action, options
from .policy import floor_volume, position_budget
from .shadow import ShadowLedger, ShadowPosition
from .calendar import now_ny


def run(collector, snapshot, mappings, now):
    store, mt = collector.store, collector.broker.mt
    state = store.get('investment:shadow', {'schema': 1, 'started_at': now, 'books': {
        name: {'equity': 1000., 'positions': {}, 'closed': [], 'peak': 1000., 'max_drawdown': 0., 'rejections': {}}
        for name in ('fundamental', 'fundamental_news')}})
    contracts = {c['symbol']: c for c in snapshot['contracts']}
    prices = {}
    for c in snapshot['contracts']:
        q = mt.symbol_info_tick(c['symbol'])
        if q is not None and 0 <= now-(q.time-c['server_offset']) <= 30: prices[c['symbol']] = (float(q.bid), float(q.ask))
    settings = options(collector.cfg)
    for name, book in state['books'].items():
        day = now_ny(now).date().isoformat()
        # Mark before rolling the day, preserving an unresolved loss halt.
        marked = book['equity']+sum(p.get('direction',1)*(prices.get(p['symbol'],(p['entry'],p['entry']))[0 if p.get('direction',1)==1 else 1]-p['entry'])*p['quantity']*p['unit_value']-p['costs'] for p in book['positions'].values())
        if marked <= book.get('baseline',1000)-200: book['halted']=True
        if book.get('day') != day and not book.get('halted'):
            book.update(day=day, baseline=marked, entries=0)
        for style, p in list(book['positions'].items()):
            if p['symbol'] not in prices: continue
            bid, ask=prices[p['symbol']]
            if style=='day':
                ShadowLedger({name:book}).mark(p['symbol'],bid,ask,now,
                    macro_flatten=any(e['time']-300<=now<=e.get('end',e['time'])+1800 for e in snapshot['events']),
                    issuer_exit=contracts[p['symbol']]['sec_close_until']>=now)
                continue
            c=contracts[p['symbol']];report=snapshot['investment_report'].get(c['ticker'],{})
            p['costs']+=max(0,now-p.get('financing_checked',p['opened']))/86400*c['finance_long']*p['quantity']
            p['financing_checked']=now
            news=report.get('news',{}) if name=='fundamental_news' else {}
            thesis={**report.get('thesis',{}),'entry_period':p['entry_period']}
            action,reason=exit_action(report.get('analysis',{}),thesis,news,c['investment']['daily_close'],early=p['early'],opened=p['opened'],now=now,cfg=collector.cfg)
            if bid<=p['stop']:action,reason='exit','STOP'
            if book.get('halted'):action,reason='exit','DAILY_LOSS'
            paid=p['costs'];risk=(max(0,p['entry']-p['stop'])*p['unit_value']+180*c['finance_long']+c['commission'])*p['quantity']+paid
            if risk>p['budget']+.01:action,reason='exit','FINANCING_RESERVE'
            if action=='promote':p['early']=False
            event_id=news.get('event_id','')
            if action=='reduce' and event_id not in p.setdefault('reductions',[]):
                target=floor_volume(p['quantity']/2,mappings[c['ticker']]['minimum'],p['quantity'],mappings[c['ticker']]['step'])
                p['reductions'].append(event_id)
                if target<=0:action='exit'
                else:
                    fraction=(p['quantity']-target)/p['quantity']
                    pnl=(bid-p['entry'])*(p['quantity']-target)*p['unit_value']-p['costs']*fraction
                    book['equity']+=pnl;book.setdefault('realized_reductions',[]).append({'time':now,'event_id':event_id,'pnl':pnl})
                    p['costs']*=1-fraction;p['quantity']=target
            if action=='exit':
                pnl=(bid-p['entry'])*p['quantity']*p['unit_value']-p['costs']
                book['equity']+=pnl;book['closed'].append({**p,'closed':now,'exit':bid,'pnl':pnl,'reason':reason})
                del book['positions'][style]
        marked=book['equity']+sum(p.get('direction',1)*(prices.get(p['symbol'],(p['entry'],p['entry']))[0 if p.get('direction',1)==1 else 1]-p['entry'])*p['quantity']*p['unit_value']-p['costs'] for p in book['positions'].values())
        book['marked_equity']=marked;book['peak']=max(book['peak'],marked);book['max_drawdown']=max(book['max_drawdown'],book['peak']-marked)
        for r in snapshot.get('_day_candidates',[]):
            symbol=r[1]
            if symbol not in prices:continue
            c=contracts[symbol];spec=mappings[c['ticker']];entry,stop,tp,unit_risk=map(float,r[5:9])
            p=ShadowPosition(symbol,'day',r[3],int(r[4]),float(r[0]),entry,stop,tp,1,spec['contract_size'],c['commission'],abs(entry-stop),c['close'])
            ShadowLedger({name:book}).offer(p,r[10]=='1',r[11]=='1',spec,now,prices,unit_risk,entry*spec['contract_size']*c['margin_rate'])
        if 'investment' in book['positions'] or book.get('halted') or book.get('entries',0)>=5:continue
        candidates=[]
        for ticker,report in snapshot['investment_report'].items():
            c=contracts[report['decision']['symbol']]
            if c['symbol'] not in prices or not c['open']<=now<c['close']-1800 or not c.get('investment_event_ok'):continue
            if not c['investment']['ready'] or c['investment']['action'] in (1,2) and name=='fundamental_news':continue
            ask=prices[c['symbol']][1]
            decision=report['decision'] if name=='fundamental_news' else candidate(report['analysis'],ask,c['investment']['atr'],{},now,collector.cfg)
            signal=c['investment'].get('signal','')
            if signal and signal in book.get('used_signals',[]):continue
            if decision['allowed'] and decision.get('event_id','') not in book.get('used_events',[]):candidates.append((ticker,report,decision,c))
        candidates.sort(key=lambda v:(-v[2]['score'],prices[v[3]['symbol']][1]/v[2]['fair_value'],v[0]))
        for ticker,report,decision,c in candidates:
            symbol=c['symbol'];spec=mappings[ticker]
            if any(p['symbol']==symbol for p in book['positions'].values()) or now-book.get('last_entry',{}).get(symbol,0)<3600:continue
            planned=sum(p.get('budget',0) for p in book['positions'].values())
            budget=position_budget(marked,book.get('baseline',1000),planned,planned)
            order=sized_order(decision,prices[symbol][1],c['investment']['atr'],{**spec,'commission':c['commission']},marked,budget,collector.cfg)
            if not order['allowed']:
                book['rejections'][order['reason']]=book['rejections'].get(order['reason'],0)+1;continue
            used_margin=sum(prices.get(p['symbol'],(p['entry'],p['entry']))[1]*p['quantity']*p['unit_value']*.2 for p in book['positions'].values())
            qty=floor_volume(min(order['quantity'],max(0,marked*.8-used_margin)/(prices[symbol][1]*spec['contract_size']*c['margin_rate'])),spec['minimum'],spec['maximum'],spec['step'])
            if qty<=0:continue
            book['positions']['investment']={'symbol':symbol,'style':'investment','entry':prices[symbol][1],
                'quantity':qty,'direction':1,'unit_value':spec['contract_size'],'stop':order['stop'],'opened':now,
                'early':decision['early'],'event_id':decision.get('event_id',''),'entry_period':report['analysis']['metrics']['period_end'],
                'budget':min(budget,decision['risk_cap']),'costs':qty*c['commission'],'financing_checked':now}
            if decision.get('event_id'):book.setdefault('used_events',[]).append(decision['event_id'])
            if c['investment'].get('signal'):book.setdefault('used_signals',[]).append(c['investment']['signal'])
            book['entries']=book.get('entries',0)+1;book.setdefault('last_entry',{})[symbol]=now;break
    spy=mappings.get('SPY')
    if spy and spy['symbol'] in prices:
        c=contracts[spy['symbol']];price=prices[spy['symbol']][1]
        if 'benchmark' not in state:
            qty=floor_volume(250/(price*spy['contract_size']),spy['minimum'],spy['maximum'],spy['step'])
            state['benchmark']={'symbol':spy['symbol'],'quantity':qty,'entry':price,'opened':now,'equity':1000.,
                'costs':qty*c['commission'],'last':now,'available':qty>0,'reason':'' if qty>0 else 'MINIMUM_LOT_EXCEEDS_25_PERCENT'}
        b=state['benchmark'];b['costs']+=max(0,now-b['last'])/86400*c['finance_long']*b['quantity'];b['last']=now
        b['marked_equity']=1000+(prices[spy['symbol']][0]-b['entry'])*b['quantity']*spy['contract_size']-b['costs']
    state['time']=now;state['limitations']=['Quotes sampled at collector cadence; intrapoll crossings are unknown', 'Separate prospective books; legacy technical results are not reused']
    store.set('investment:shadow',state)
