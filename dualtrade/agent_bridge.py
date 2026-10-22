"""Discretionary demo reviews handed to the native EA, never Python order calls.

An approval is a short-lived risk ceiling for one immutable native candidate.
The EA recalculates volume and repeats all account, observation and price gates.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import time
from datetime import datetime, timedelta, timezone

from .config import ROOT, load, bridge, config_hash
from .storage import fingerprint, read_json, write_json, process_lock

ID = re.compile(r'[A-Za-z0-9.]{1,48}')
CHECKS = ('structure', 'trend', 'volatility', 'costs', 'events', 'levels', 'stop', 'capacity')


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def current_context(cfg):
    from .cli import source_hash
    return fingerprint({'config_hash': config_hash(cfg), 'source_hash': source_hash()})


def parse_request(text):
    lines = text.strip().splitlines()
    if len(lines) != 1:
        raise ValueError('ONE_NATIVE_REQUEST_REQUIRED')
    v = lines[0].split('|')
    if len(v) != 23 or v[:2] != ['REQUEST', '1'] or not ID.fullmatch(v[2]) or not re.fullmatch('[a-f0-9]{64}', v[3]):
        raise ValueError('INVALID_NATIVE_REQUEST')
    names = ('created', 'expires', 'symbol', 'style', 'direction', 'entry', 'stop', 'target',
             'atr', 'risk_ceiling', 'max_volume', 'bar', 'strategy', 'policy', 'score',
             'support_low', 'support_high', 'resistance_low', 'resistance_high')
    r = dict(zip(names, v[4:])) | {'id': v[2], 'context': v[3]}
    for key in names:
        if key == 'symbol': continue
        r[key] = float(r[key])
        if not number(r[key]): raise ValueError('NONFINITE_NATIVE_REQUEST')
    for key in ('created', 'expires', 'style', 'direction', 'bar', 'strategy', 'policy'):
        if not r[key].is_integer(): raise ValueError('INVALID_NATIVE_INTEGER')
        r[key] = int(r[key])
    if (r['style'] not in (0, 1) or r['direction'] not in (-1, 1) or
            not 0 < r['expires'] - r['created'] <= 55 or r['atr'] <= 0 or
            not 0 < r['risk_ceiling'] <= 40 or r['max_volume'] <= 0 or
            not re.fullmatch(r'[A-Za-z0-9_.-]{1,30}', r['symbol'])):
        raise ValueError('INVALID_NATIVE_CANDIDATE')
    if not (r['stop'] < r['entry'] < r['target'] if r['direction'] == 1 else r['target'] < r['entry'] < r['stop']):
        raise ValueError('INVALID_NATIVE_LEVELS')
    return r


def request(cfg, identity):
    if not isinstance(identity, str) or not ID.fullmatch(identity):
        raise ValueError('INVALID_REQUEST_ID')
    return parse_request((bridge(cfg) / 'agent-requests' / (identity + '.txt')).read_text(encoding='ascii'))


def request_live(r, cfg, now):
    if not r['created'] <= now <= r['expires']:
        raise ValueError('NATIVE_REQUEST_EXPIRED')
    if r['context'] != current_context(cfg):
        raise ValueError('SOURCE_OR_CONFIG_CHANGED')


def pending(cfg, now):
    out = []
    context = current_context(cfg)
    for path in (bridge(cfg) / 'agent-requests').glob('*.txt'):
        try:
            r = parse_request(path.read_text(encoding='ascii'))
            if r['context'] == context and r['created'] <= now < r['expires'] and not (bridge(cfg) / 'agent-decisions' / path.name).exists():
                out.append(r)
        except (ValueError, OSError): continue
    return sorted(out, key=lambda r: (r['expires'], -r['score'], r['id']))[:3]


def wait_candidate(cfg, timeout=50, *, clock=time.time, monotonic=time.monotonic, sleep=time.sleep):
    """Wait inside a scheduled turn; never extend native decision authority.

    A minute heartbeat can arrive just before a bar. Bounded polling catches its
    request in this turn instead of waiting another minute. Late work still fails
    closed. The monotonic deadline survives wall-clock adjustments.
    """
    if not number(timeout) or not 0 <= timeout <= 50:
        raise ValueError('WAIT_MUST_BE_BETWEEN_ZERO_AND_FIFTY_SECONDS')
    end = monotonic() + timeout
    while True:
        now = clock()
        items = pending(cfg, now)
        if items:
            return {'pending': items, 'checked_at': now,
                    'seconds_left': min(r['expires']-now for r in items)}
        remaining = end-monotonic()
        if remaining <= 0:
            return {'pending': [], 'checked_at': now, 'timed_out': True}
        sleep(min(.5, remaining))


def broker_timestamp(value, offset):
    t=datetime.fromisoformat(value.replace('Z','+00:00'))
    return t.timestamp() if t.tzinfo else t.replace(tzinfo=timezone.utc).timestamp()-offset


def history_session_windows(cfg, symbol, offset, now):
    """Recent verified sessions only; the current clock is not a historical zone.

    Broker schedules are server wall times, as in broker.inspect. Across common
    US/European clock-change weekends, validate only the current session rather
    than applying today's offset to prior-session clocks. Every requested frame
    still needs a completed current-session bucket. Older bars remain context.
    """
    from . import calendar as cal
    from .broker import native_sessions
    native = native_sessions(bridge(cfg) / 'broker-native.txt', now)
    if native is None or native[0] != offset or symbol not in native[1]:
        raise ValueError('HISTORY_SESSION_CLOCK_UNAVAILABLE')
    today = cal.now_ny(now).date()
    prior = cal.previous_session(today)
    day = prior
    ambiguous_prior_offset = False
    previous_offset = datetime.combine(day, datetime.min.time(), cal.NY).utcoffset()
    while day <= today:
        local_offset = datetime.combine(day, datetime.min.time(), cal.NY).utcoffset()
        # Do not infer the broker timezone from a single current UTC offset.
        # Last Sundays in March/October also need historical clock evidence.
        possible_broker_change = (day > prior and day.weekday() == 6 and
                                  day.month in (3, 10) and (day + timedelta(days=7)).month != day.month)
        if local_offset != previous_offset or possible_broker_change:
            ambiguous_prior_offset = True
        previous_offset = local_offset
        day += timedelta(days=1)
    windows = []
    for day in ((today,) if ambiguous_prior_offset else (prior, today)):
        exchange = cal.session(day)
        if exchange is None:
            continue
        hours = native[1][symbol].get((day.weekday() + 1) % 7)
        if not isinstance(hours, list) or len(hours) != 1:
            raise ValueError('HISTORY_SESSION_SCHEDULE_UNAVAILABLE')
        start, end = hours[0]
        ny_offset = int(datetime.combine(day, datetime.min.time(), cal.NY).utcoffset().total_seconds())
        local_start, local_end = start - offset + ny_offset, end - offset + ny_offset
        if not 0 <= local_start < local_end < 86400:
            raise ValueError('HISTORY_SESSION_SCHEDULE_UNSUPPORTED')
        midnight = datetime.combine(day, datetime.min.time(), cal.NY).timestamp()
        opening, closing = max(exchange[0], midnight + local_start), min(exchange[1], midnight + local_end)
        if opening >= closing:
            raise ValueError('HISTORY_SESSION_SCHEDULE_UNSUPPORTED')
        windows.append((opening, closing))
    return windows


def closed_history(rows, offset, seconds, now, count, *, session_windows=None):
    bars=[]
    for row in rows:
        stamp=broker_timestamp(row['time'],offset)
        prices={k:row[k] for k in ('open','high','low','close')}
        if any(not number(v) or v<=0 for v in prices.values()) or not prices['low']<=min(prices['open'],prices['close'])<=max(prices['open'],prices['close'])<=prices['high']:
            raise ValueError('INVALID_BROKER_CANDLE')
        if stamp+seconds<=now:
            bars.append({'time':int(stamp),**prices})
    bars.sort(key=lambda r:r['time'])
    if len(bars)<count or len({b['time'] for b in bars})!=len(bars):
        raise ValueError('CLOSED_HISTORY_MISSING_OR_DUPLICATED')
    if session_windows is not None:
        expected = set()
        for opening, closing in session_windows:
            if not number(opening) or not number(closing) or not opening < closing:
                raise ValueError('HISTORY_SESSION_SCHEDULE_UNSUPPORTED')
            bucket = math.floor((opening + offset) / seconds) * seconds - offset
            while bucket < closing:
                if bucket + seconds > opening and bucket + seconds <= now:
                    expected.add(int(bucket))
                bucket += seconds
        if not expected or bars[-1]['time'] != max(expected):
            raise ValueError('CLOSED_HISTORY_STALE')
        actual = {b['time'] for b in bars if min(expected) <= b['time'] <= max(expected)}
        if actual != expected:
            raise ValueError('CLOSED_HISTORY_SESSION_GAP')
    return bars[-count:]


def inspect(cfg, identity, now):
    r = request(cfg, identity); request_live(r, cfg, now)
    from .decision_revision import observation_context
    observed_context = observation_context(cfg)
    from .mt5_mcp import NativeMT5
    m=NativeMT5();m.call('get_workspace_info');m.verify_demo()
    snap=read_json(bridge(cfg)/'snapshot.json',{})
    contract=next(c for c in snap.get('contracts',[]) if c['symbol']==r['symbol'])
    quote=m.call('get_marketwatch_symbols',{'symbol':r['symbol'],'limit':1})['symbols']
    if len(quote)!=1 or quote[0]['symbol']!=r['symbol']:raise ValueError('EXACT_QUOTE_REQUIRED')
    q=quote[0];offset=contract['server_offset'];frames={}
    session_windows = history_session_windows(cfg, r['symbol'], offset, now)
    start=datetime.fromtimestamp(now+offset-90*86400,timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds')
    end=datetime.fromtimestamp(now+offset,timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds')
    for name,seconds,count in (('M15',900,64),('H1',3600,48),('H4',14400,48)):
        data=m.call('get_chart_history',{'symbol':r['symbol'],'period':name,
            'datetime_from':start,'datetime_to':end,'limit':count+4})
        if data.get('ok') is not True:raise ValueError('BROKER_HISTORY_UNAVAILABLE')
        frames[name]=closed_history(data['history'],offset,seconds,now,count,
                                    session_windows=session_windows)
    evidence={'schema':1,'checked_at':now,'request':r,'observation_context':observed_context,
        'quote':{'time':broker_timestamp(q['update_time'],offset),'bid':q['bid'],'ask':q['ask']},
        'spec':{k:q[k] for k in ('volume_min','volume_max','volume_step','contract_size','tick_size')},
        'contract':{k:contract.get(k) for k in ('previous_high','previous_low','entry_allowed','earnings_close','earnings_end','sec_close_until','veto_buy','veto_sell','ai_news')},
        'closed_bars_utc':frames,'history_session_windows_utc':session_windows,
        'win_probability':None}
    evidence['news_context_sha256'] = fingerprint(contract.get('ai_news', {}))
    # No partially completed, expired inspection is handed to the reviewer.
    finished=time.time(); request_live(r,cfg,finished)
    if snap.get('observation_context') != observed_context or observation_context(cfg) != observed_context:
        raise ValueError('DECISION_MECHANISM_CHANGED_SINCE_SNAPSHOT')
    if not 0 <= finished-evidence['quote']['time'] <= 30:
        raise ValueError('STALE_EXECUTABLE_QUOTE')
    path=ROOT/'runtime/agent-evidence'/(identity+'.json');write_json(path,evidence)
    return {'evidence_path':str(path),'evidence_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'request':r}


def validate_review(review, r, evidence, evidence_hash, snap, ea, cfg, now):
    request_live(r, cfg, now)
    if (review.get('schema') != 1 or review.get('request_id') != r['id'] or
            review.get('context') != r['context'] or review.get('evidence_sha256') != evidence_hash or
            evidence.get('request') != r or not number(evidence.get('checked_at')) or
            not r['created'] <= evidence['checked_at'] <= now <= evidence['checked_at'] + 55):
        raise ValueError('REVIEW_NOT_BOUND_TO_CURRENT_EVIDENCE')
    if review.get('action') not in ('APPROVE', 'REJECT'):
        raise ValueError('EXPLICIT_REVIEW_ACTION_REQUIRED')
    if not isinstance(review.get('rationale_tr'), str) or not 40 <= len(review['rationale_tr']) <= 4000:
        raise ValueError('REVIEW_RATIONALE_REQUIRED')
    if review.get('win_probability') is not None:
        raise ValueError('NO_CALIBRATED_WIN_PROBABILITY_AVAILABLE')
    budget = review.get('risk_usd', 0)
    if not number(budget): raise ValueError('INVALID_REVIEW_RISK')
    if review['action'] == 'APPROVE':
        from .decision_revision import observation_context
        observed_context = observation_context(cfg)
        if (evidence.get('observation_context') != observed_context or
                snap.get('observation_context') != observed_context):
            raise ValueError('DECISION_MECHANISM_CHANGED_SINCE_INSPECTION')
        observed = snap.get('observation_ready' if r['style'] == 0 else 'swing_observation_ready') is True
        if not observed:
            from .demo_exception import active
            waived = (r['style'] == 0 and ea.get('day_observation_waiver_active') is True and
                      active(cfg, now, 'day', ea.get('day_observation_waiver_until')) is not None)
            if not waived:
                raise ValueError('CURRENT_DECISION_OBSERVATION_REQUIRED')
        if set(review.get('checks', {})) != set(CHECKS) or any(review['checks'][k] is not True for k in CHECKS):
            raise ValueError('ALL_PRETRADE_CHECKS_REQUIRED')
        if not 0 < budget <= min(r['risk_ceiling'], 40, ea.get('equity', 0) * .04):
            raise ValueError('RISK_EXCEEDS_NATIVE_CEILING')
        if (ea.get('demo') is not True or ea.get('agent_review_required') is not True or
                ea.get('decision_context') != r['context'] or
                not 0 <= now - ea.get('time', 0) <= 10 or not 0 <= now-snap.get('time', 0) <= 60 or
                any(ea.get(k) for k in ('halt','hard_halt','week_halt','unreconciled','intent_pending','foreign_exposure'))):
            raise ValueError('NATIVE_HEALTH_GATE_BLOCKED')
        c = next((c for c in snap.get('contracts', []) if c['symbol'] == r['symbol']), {})
        if evidence.get('news_context_sha256') != fingerprint(c.get('ai_news', {})):
            raise ValueError('NEWS_CONTEXT_CHANGED_SINCE_INSPECTION')
        ready = snap.get('ready' if r['style'] == 0 else 'swing_ready')
        if (not ready or not c.get('verified') or not c.get('entry_allowed') or
                c.get('veto_buy' if r['direction'] == 1 else 'veto_sell') or
                not c.get('ai_news', {}).get('ready')):
            raise ValueError('RESEARCH_OR_EVENT_GATE_BLOCKED')
        q = evidence.get('quote', {})
        if not 0 <= now-q.get('time', 0) <= 30:
            raise ValueError('STALE_EXECUTABLE_QUOTE')
    elif budget != 0:
        raise ValueError('REJECTION_CANNOT_AUTHORIZE_RISK')
    return '|'.join(map(str, ('AGENT', 1, r['id'], r['context'], int(now), r['expires'],
                              review['action'], budget, format(r['entry'], '.10f'),
                              format(r['stop'], '.10f'), format(r['target'], '.10f'), 'OPEN'))) + '\n'


def prepare_review(cfg, path, now):
    review = read_json(Path(path), {})
    r = request(cfg, review.get('request_id'))
    evidence_path = ROOT / 'runtime/agent-evidence' / (r['id'] + '.json')
    evidence = read_json(evidence_path, {})
    text = validate_review(review, r, evidence, hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
                           read_json(bridge(cfg)/'snapshot.json', {}), read_json(bridge(cfg)/'ea-status.json', {}), cfg, now)
    return bridge(cfg)/'agent-decisions'/(r['id']+'.txt'), text


def prepare_close(cfg, review, now):
    from .mt5_mcp import NativeMT5
    if review.get('schema') != 1 or review.get('action') != 'CLOSE' or not isinstance(review.get('rationale_tr'),str) or len(review['rationale_tr']) < 40:
        raise ValueError('EXPLICIT_EXIT_REVIEW_REQUIRED')
    ea = read_json(bridge(cfg)/'ea-status.json', {})
    if (ea.get('demo') is not True or not 0 <= now-ea.get('time',0) <= 10 or
            review.get('nonce') != ea.get('agent_nonce') or review.get('context') != current_context(cfg) or
            ea.get('decision_context') != review['context']):
        raise ValueError('EXIT_CONTEXT_OR_HEARTBEAT_CHANGED')
    m=NativeMT5();m.call('get_workspace_info');m.verify_demo()
    p=next((p for p in m.call('get_trading_open_positions').get('positions',[]) if str(p.get('ticket'))==str(review.get('ticket'))),None)
    magics={cfg['magic_day'],cfg['magic_swing'],cfg['magic_investment']}
    if (p is None or int(p.get('magic',-1)) not in magics or p.get('symbol')!=review.get('symbol') or
            str(p.get('identifier',p.get('position_id')))!=str(review.get('identifier')) or p.get('volume')!=review.get('volume')):
        raise ValueError('EXIT_POSITION_IDENTITY_OR_OWNERSHIP_CHANGED')
    return bridge(cfg)/'agent-close.txt', '|'.join(map(str,('CLOSE',1,ea['agent_nonce'],review['context'],int(now),int(now)+55,
        p['ticket'],review['identifier'],p['symbol'],p['volume'],'THESIS_INVALIDATED'))) + '\n'


def publish(cfg, path, now, close=False):
    from .mt5_mcp import NativeMT5
    review = read_json(Path(path), {})
    # One publisher per candidate. A later agent cannot overwrite an earlier
    # approval/rejection or its audit trail while the EA is consuming it.
    identity = 'close' if close else review.get('request_id')
    if not isinstance(identity,str) or not ID.fullmatch(identity):
        raise ValueError('INVALID_REQUEST_ID')
    with process_lock(ROOT/'runtime/agent-publish-locks'/(identity+'.lock')):
        target, content = prepare_close(cfg, review, now) if close else prepare_review(cfg, path, now)
        if not close and target.exists():
            raise ValueError('NATIVE_REQUEST_ALREADY_DECIDED')
        m = NativeMT5(); m.call('get_workspace_info'); m.verify_demo()
        # NativeMT5 independently reconstructs/validates the payload before writing it.
        archive = ROOT/'runtime/agent-reviews'/('close-'+str(time.time_ns()) + '.json' if close else review['request_id']+'.json')
        write_json(archive, review)
        m.call('write_file', {'path': str(target), 'content': content, 'overwrite': close, 'encoding': 'ascii'})
    return {'published': True, 'action': review['action'], 'note': 'Native gates and reconciliation remain authoritative; not a fill confirmation'}


def validate_publish_content(cfg, path, content, now):
    path = Path(path).resolve(); directory = bridge(cfg).resolve()
    if path == directory/'agent-close.txt':
        parts = content.strip().split('|')
        if len(parts) != 11: raise ValueError('INVALID_EXIT_PACKET')
        review = {'schema':1,'action':'CLOSE','nonce':parts[2],'context':parts[3],'ticket':parts[6],
                  'identifier':parts[7],'symbol':parts[8],'volume':float(parts[9]),'rationale_tr':'Validated native-owned position discretionary thesis exit.'}
        _, expected = prepare_close(cfg, review, now)
        # A second validation must retain the already-bounded decision time.
        e=expected.strip().split('|'); e[4:6]=parts[4:6]
        if not int(parts[4]) <= now <= int(parts[5]) <= int(parts[4])+55: raise ValueError('EXIT_PACKET_EXPIRED')
        if '|'.join(e)+'\n' != content: raise ValueError('EXIT_PACKET_CHANGED')
    elif path.parent == directory/'agent-decisions' and path.suffix == '.txt' and ID.fullmatch(path.stem):
        review_path = ROOT/'runtime/agent-reviews'/(path.stem+'.json')
        _, expected = prepare_review(cfg, review_path, now)
        parts=content.strip().split('|'); e=expected.strip().split('|')
        if len(parts)!=12 or not int(parts[4]) <= now <= int(parts[5]): raise ValueError('REVIEW_PACKET_EXPIRED')
        e[4]=parts[4]
        if '|'.join(e)+'\n' != content: raise ValueError('REVIEW_PACKET_CHANGED')
    else: raise ValueError('OUTSIDE_AGENT_HANDOFF')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('pending'); inspect_parser=sub.add_parser('inspect'); inspect_parser.add_argument('id')
    wait=sub.add_parser('wait'); wait.add_argument('--timeout',type=float,default=50)
    for name in ('review','exit-review'):
        cmd=sub.add_parser(name);cmd.add_argument('file')
    arm=sub.add_parser('arm');arm.add_argument('--style',choices=('day','swing'),required=True);arm.add_argument('--until',required=True)
    arm.add_argument('--observation-waiver-today',action='store_true',help='Explicit user-approved 22 October day-demo exception only')
    args=p.parse_args(argv); cfg=load(); now=time.time()
    if args.command=='pending': result={'pending':pending(cfg,now)}
    elif args.command=='wait': result=wait_candidate(cfg,args.timeout)
    elif args.command=='inspect': result=inspect(cfg,args.id,now)
    elif args.command=='arm':
        from datetime import datetime
        from .mt5_mcp import NativeMT5
        until=datetime.fromisoformat(args.until)
        if until.tzinfo is None:raise ValueError('EXPLICIT_SESSION_TIMEZONE_REQUIRED')
        result=NativeMT5().arm_agent_demo(args.style,until.timestamp(),observation_waiver=args.observation_waiver_today)
    else: result=publish(cfg,args.file,now,close=args.command=='exit-review')
    print(json.dumps(result,ensure_ascii=True,indent=2))


if __name__=='__main__': main()
