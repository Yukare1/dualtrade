from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace as N

import pytest

from dualtrade.ai_news import apply, decision, read_state, part_count, PART_SIZE
from dualtrade.official_news import OfficialNews, NewsStore, NewsError, settings, sources
from dualtrade.material_news import assess
from dualtrade.investment import candidate

NOW = datetime(2026, 10, 22, 14, tzinfo=timezone.utc).timestamp()
URL = 'https://www.apple.com/newsroom/2026/10/results/'
TEXT = 'Apple announced results. FY2027 net income guidance increased from $100 million to $120 million. ' + 'Complete source material. ' * 400


def setup(tmp_path, *, source_id='IR:AAPL', url=URL, text=TEXT, links=None):
    store, cfg = NewsStore(tmp_path), settings()
    source = next(s for s in sources(cfg) if s['id'] == source_id)
    row = dict(title='Apple reports earnings guidance', url=url, published_at=NOW-60,
               excerpt='Financial results released.', form='8-K', items=['2.02'])
    store.ingest(source, [row], cfg, NOW, False)
    event = store.claim(NOW, 1)[0]
    with store.db:
        store.db.execute('INSERT INTO documents VALUES (?,?,?,?,?,?)',
                         (event['id'], url, 'hash', text, NOW, json.dumps(links or [])))
    return store, OfficialNews(cfg, store), event


def review(event):
    return dict(event_id=event['id'], evidence_url=event['url'], document_sha256='hash',
        summary_tr='Şirket kâr beklentisini yükseltti.', why_it_matters_tr='Gelecek dönem kâr beklentisi arttı.',
        uncertainties_tr='Haberin fiyat etkisi ve gerçekleşecek kâr belirsiz.',
        facts=[dict(claim_tr='Şirket finansal sonuçlarını açıkladı.', quote='Apple announced results.')],
        documents_read=[dict(url=event['url'], sha256='hash', parts=part_count(TEXT))],
        assessment=dict(schema=1, direction='positive', confidence=.9, material=True, horizon='long_term',
                        rationale_tr='Açıklanan net kâr beklentisindeki artış şirket açısından olumlu.', evidence_facts=[0],
                        economics=[dict(kind='guidance', source=event['url'], event_key='AAPL.FY2027.net_income',
                            quote='FY2027 net income guidance increased from $100 million to $120 million.',
                            period='FY2027', metric='net_income', unit='USD million', before=100, after=120,
                            before_text='$100 million', after_text='$120 million')]))


def serve(monitor, event, text=TEXT):
    for p in range(1, part_count(text)+1):
        r = monitor.document(event['id'], NOW, part=p)
        assert r['text'] == text[(p-1)*PART_SIZE:p*PART_SIZE]


def state(review_row=None, *, status='analyzed', ticker='AAPL'):
    from dualtrade.official_news import sources
    return dict(healthy=True, error=None,
        sources={s['id']: dict(ok=True, last_success=NOW, next_check=NOW+300) for s in sources(settings())},
        events=[] if review_row is None else [dict(id='event', ticker=ticker, url=URL,
                         published=NOW-60, observed=NOW-50, state=status, analysis=review_row)])


def test_complete_document_chunks_required_before_trade_eligibility(tmp_path):
    store, monitor, event = setup(tmp_path)
    r = review(event)
    monitor.document(event['id'], NOW, part=1)
    with pytest.raises(NewsError, match='AI_DOCUMENT_PARTS_NOT_READ'):
        monitor.complete([r], NOW)
    serve(monitor, event)
    monitor.complete([r], NOW)
    saved = json.loads(store.db.execute('SELECT analysis FROM events').fetchone()[0])
    assert saved['eligible_for_trading'] and saved['verification'] == 'FULL_TEXT_PARTS_AND_QUOTES'
    assert saved['assessment']['economics'][0]['before'] == 100
    assert saved['assessment']['economics'][0]['primary_verified'] is True
    loaded = read_state(NOW, tmp_path)
    loaded['sources'] = state()['sources']
    d = decision(loaded, 'AAPL', NOW)
    assert d['ready'] and d['veto_sell'] and not d['veto_buy']
    news = assess(d['material_events'], 'AAPL', {'revenue': 1000}, NOW, True)
    assert news['positive']
    news['positive']['reference_price'] = 100
    a = dict(ready=True, score=60, fair_value=110, leverage=2)
    assert candidate(a, 100, 2, news, NOW)['early']
    # A bullish model cannot bypass a weak balance sheet or absent reference.
    assert not candidate({**a, 'score': 50}, 100, 2, news, NOW)['allowed']
    del news['positive']['reference_price']
    assert not candidate(a, 100, 2, news, NOW)['allowed']


@pytest.mark.parametrize('mutate,error', [
    (lambda r: r['assessment'].update(confidence=float('nan')), 'AI_ASSESSMENT_INCOMPLETE'),
    (lambda r: r['assessment'].update(direction='buy'), 'AI_DIRECTION_INVALID'),
    (lambda r: r['assessment'].update(evidence_facts=[True]), 'AI_ASSESSMENT_EVIDENCE_REQUIRED'),
    (lambda r: r['documents_read'][0].update(sha256='old'), 'AI_DOCUMENT_MANIFEST_INVALID'),
    (lambda r: r['assessment']['economics'][0].update(after=999), 'AI_ECONOMIC_NUMBER_WITHOUT_ANCHOR'),
    (lambda r: r['assessment']['economics'][0].update(period='FY2028'), 'AI_COMPARABLE_GUIDANCE_REQUIRED'),
    (lambda r: r['assessment']['economics'][0].update(kind='bankruptcy'), 'AI_ECONOMIC_KIND_INVALID'),
])
def test_untrusted_or_unsupported_model_claims_cannot_reach_trading(tmp_path, mutate, error):
    store, monitor, event = setup(tmp_path)
    serve(monitor, event)
    r = review(event); mutate(r)
    with pytest.raises(NewsError, match=error):
        monitor.complete([r], NOW)
    assert store.db.execute('SELECT state FROM events').fetchone()[0] == 'leased'


def test_sec_cover_and_all_discovered_exhibits_must_be_read(tmp_path):
    root = 'https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/main.htm'
    exhibit = root.replace('main.htm', 'ex99.htm')
    store, monitor, event = setup(tmp_path, source_id='SEC:AAPL', url=root, links=[exhibit])
    serve(monitor, event)
    r = review(event)
    with pytest.raises(NewsError, match='AI_ROOT_OR_EXHIBITS_UNREAD'):
        monitor.complete([r], NOW)
    with store.db:
        store.db.execute('INSERT INTO documents VALUES (?,?,?,?,?,?)',
                         (event['id'], exhibit, 'exhibit-hash', 'Full earnings release and outlook. '*5, NOW, '[]'))
    monitor.document(event['id'], NOW, url=exhibit, part=1)
    r['documents_read'].append(dict(url=exhibit, sha256='exhibit-hash', parts=1))
    assert monitor.complete([r], NOW)['completed']


def test_changed_source_invalidates_old_chunk_receipts(tmp_path):
    store, monitor, event = setup(tmp_path); serve(monitor, event)
    with store.db:
        store.db.execute("UPDATE documents SET hash='new-hash'")
    r = review(event); r['document_sha256'] = 'new-hash'; r['documents_read'][0]['sha256'] = 'new-hash'
    with pytest.raises(NewsError, match='AI_DOCUMENT_PARTS_NOT_READ'):
        monitor.complete([r], NOW)


def saved_assessment(direction='negative', confidence=.9, **extra):
    return dict(eligible_for_trading=True, reviewed_at=NOW-10,
        assessment=dict(schema=1, direction=direction, confidence=confidence, material=True,
                        horizon='multi_day', rationale_tr='Kaynak metinle desteklenen şirket riski.',
                        expires_at=NOW+300, economics=[], **extra))


def test_pending_uncertain_missing_and_stale_analysis_fail_closed_for_new_entries():
    assert not decision(state({}, status='pending'), 'AAPL', NOW)['ready']
    assert not decision(state(saved_assessment('uncertain')), 'AAPL', NOW)['ready']
    assert not decision(state(saved_assessment(confidence=.5)), 'AAPL', NOW)['ready']
    s = state(saved_assessment()); s['sources']['SEC:AAPL']['ok'] = False
    assert not decision(s, 'AAPL', NOW)['ready']
    s = state(saved_assessment()); s['events'][0]['analysis']['assessment']['expires_at'] = NOW-1
    d = decision(s, 'AAPL', NOW)
    assert not d['ready'] and not d['veto_buy']
    assert decision(state({}, status='baseline'), 'AAPL', NOW)['ready']
    assert decision(state({}, status='pending', ticker='MSFT'), 'AAPL', NOW)['ready']
    assert not decision(state({}, status='pending', ticker='MACRO'), 'AAPL', NOW)['ready']


def test_native_bridge_gets_veto_and_pending_flags_without_protective_mutations():
    c = dict(ticker='AAPL', entry_allowed=True, investment_event_ok=True, swing_sources_ok=True,
             veto_buy=False, veto_sell=False, earnings_close=123, sec_close_until=456)
    snapshot = {'contracts': [c]}
    apply(snapshot, state(saved_assessment()), NOW)
    assert c['veto_buy'] and c['entry_allowed'] and c['earnings_close'] == 123
    apply(snapshot, state({}, status='pending'), NOW)
    assert not c['entry_allowed'] and not c['investment_event_ok'] and not c['swing_sources_ok']
    assert c['sec_close_until'] == 456


def test_negative_economics_are_durable_and_repeated_reviews_do_not_duplicate(tmp_path):
    text = TEXT.replace('$120 million', '$70 million')
    store, monitor, event = setup(tmp_path, text=text); serve(monitor, event, text)
    r = review(event)
    r['assessment']['direction'] = 'negative'
    e = r['assessment']['economics'][0]
    e['after'], e['after_text'] = 70, '$70 million'
    e['quote'] = e['quote'].replace('$120 million', '$70 million')
    monitor.complete([r], NOW)
    s = read_state(NOW, tmp_path); s['sources'] = state()['sources']
    d = decision(s, 'AAPL', NOW+90000)
    news = assess(d['material_events']*2, 'AAPL', {'revenue':1000}, NOW+90000)
    assert news['action'] == 'exit' and len(news['events']) == 1


def test_missing_or_corrupt_state_cannot_authorize_entries(tmp_path):
    assert not read_state(NOW, tmp_path)['healthy']
    (tmp_path/'state.sqlite3').write_text('not a database')
    assert not read_state(NOW, tmp_path)['healthy']


def test_provider_articles_are_queued_and_fetched_as_complete_bodies(tmp_path, monkeypatch):
    store = NewsStore(tmp_path); monitor = OfficialNews(settings(), store)
    row = dict(ticker='AAPL', url='https://www.reuters.com/technology/apple-results/', time=NOW-60,
               title='Apple reports results', provider='Marketaux', credibility=dict(eligible=True, status='single_source'))
    assert monitor.ingest_reporting([row], NOW) == 1
    assert monitor.ingest_reporting([row], NOW) == 0
    event = store.claim(NOW, 1)[0]
    body = '<html><nav>irrelevant navigation</nav><article>' + TEXT + '</article></html>'
    monkeypatch.setattr(monitor, 'download', lambda *a, **k: (body.encode(), {}))
    doc = monitor.document(event['id'], NOW)
    assert doc['characters'] == len(TEXT.strip())
    serve(monitor, event, TEXT.strip())
    r = review(event); r['document_sha256'] = doc['sha256']
    r['documents_read'][0].update(sha256=doc['sha256'], parts=doc['parts'])
    with pytest.raises(NewsError, match='AI_ECONOMICS_REQUIRES_ISSUER_OR_SEC'):
        monitor.complete([r], NOW)
    r['assessment']['economics'] = []
    assert monitor.complete([r], NOW)['completed']


@pytest.mark.parametrize('html', ['<html>Summary only</html>',
    '<article>Subscribe to continue ' + 'article words '*100 + '</article>',
    '<article>Short teaser</article>'])
def test_paywall_or_page_shell_cannot_be_presented_as_a_full_article(tmp_path, monkeypatch, html):
    store = NewsStore(tmp_path); monitor = OfficialNews(settings(), store)
    row = dict(ticker='AAPL', url='https://www.reuters.com/apple-results/', time=NOW-60,
               title='Apple reports results', credibility=dict(eligible=True, status='single_source'))
    monitor.ingest_reporting([row], NOW); event = store.pending(NOW)[0]
    monkeypatch.setattr(monitor, 'download', lambda *a, **k: (html.encode(), {}))
    with pytest.raises(NewsError, match='FULL_ARTICLE_UNAVAILABLE'):
        monitor.document(event['id'], NOW)
    assert not store.db.execute('SELECT * FROM documents').fetchall()


def test_unknown_publisher_and_rejected_news_cannot_enter_full_text_queue(tmp_path):
    monitor = OfficialNews(settings(), NewsStore(tmp_path))
    row = dict(ticker='AAPL', url='https://www.reuters.com.attacker.example/apple/', time=NOW-60,
               title='Apple reports results', credibility=dict(eligible=True))
    assert monitor.ingest_reporting([row], NOW) == 0
    row['url'] = 'https://www.reuters.com/apple/'
    row['credibility']['eligible'] = False
    assert monitor.ingest_reporting([row], NOW) == 0
