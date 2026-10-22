"""Read current readiness without changing control, permissions or evidence."""
from pathlib import Path
import json
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dualtrade.config import load,bridge,config_hash
from dualtrade.cli import source_hash
from dualtrade.storage import Store,read_json,fingerprint,write_json
from dualtrade.context_agent import evaluate
from dualtrade.ai_news import read_state

cfg=load();now=time.time();directory=bridge(cfg)
ea=read_json(directory/'ea-status.json',{});snap=read_json(directory/'snapshot.json',{})
store=Store(ROOT/'runtime/state.sqlite3')
context=fingerprint({'config_hash':config_hash(cfg),'source_hash':source_hash()})
acceptance=read_json(ROOT/'build/acceptance.json',{})
council=store.get('council_source',{})
coverage=evaluate([c['ticker'] for c in snap.get('contracts',[])],now,read_state(now))
report={'time':now,'source_hash':source_hash(),'context':context,
    'functional_acceptance_current':acceptance.get('passed') is True and acceptance.get('source_hash')==source_hash(),
    'ea':{k:ea.get(k) for k in ('version','demo','mode','orders_enabled','agent_review_required','day_observed','day_observation_waiver_active','day_observation_waiver_until','day_positions','swing_positions','investment_positions','halt','unreconciled','intent_pending')},
    'ea_fresh':0<=now-ea.get('time',0)<=10,'snapshot_fresh':0<=now-snap.get('time',0)<=60,
    'context_matches_native':ea.get('decision_context')==context,
    'observations':{style:store.get('observation_pass' if style=='day' else style+':observation_pass',{}).get('config_hash')==context for style in ('day','swing','investment')},
    'observation_progress':{style:store.get(('' if style=='day' else style+':')+'observation:'+__import__('dualtrade.calendar',fromlist=['now_ny']).now_ny(now).date().isoformat(),{}) for style in ('day','swing','investment')},
    'ready':{style:snap.get('ready' if style=='day' else style+'_ready',False) for style in ('day','swing','investment')},
    'issues':snap.get('issues',[]),'context_agent':{'ready':coverage['ready'],'issues':coverage['issues'],'social_media_coverage':False},
    'council':{'healthy':council.get('healthy',False),'issues':council.get('issues',[])},
    'feed_health':snap.get('feed_health',{}),'reddit_enabled':False,
    'orders_opened_by_this_check':0}
write_json(ROOT/'runtime/reports/agent-readiness.json',report)
print(json.dumps(report,ensure_ascii=True,indent=2))
