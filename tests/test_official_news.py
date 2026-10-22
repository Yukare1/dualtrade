from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest
import requests

from dualtrade.official_news import (
    OfficialNews, NewsStore, NewsError, settings, sources, parse_feed, parse_sec,
    allowed, importance, timestamp,
)

NOW = datetime(2026, 10, 22, 14, tzinfo=timezone.utc).timestamp()


def issuer():
    return next(s for s in sources(settings()) if s['id'] == 'IR:AAPL')


def item(age=60, title='Apple reports record earnings and revenue', url=None):
    return {'title': title, 'url': url or 'https://www.apple.com/newsroom/2026/10/results/',
            'published_at': NOW - age, 'excerpt': 'Quarterly results released.', 'form': None, 'items': []}


def test_first_run_baselines_old_content_and_deduplicates_across_restarts(tmp_path):
    store = NewsStore(tmp_path)
    cfg = settings()
    old, new = item(7200), item(60, url='https://www.apple.com/newsroom/2026/10/new/')
    assert store.ingest(issuer(), [old, new], cfg, NOW, False) == 1
    assert len(store.pending(NOW)) == 1
    store = NewsStore(tmp_path)
    assert store.ingest(issuer(), [old, new], cfg, NOW + 120, True) == 0
    assert len(store.pending(NOW + 120)) == 1


def test_only_important_new_known_dates_are_queued(tmp_path):
    store, cfg = NewsStore(tmp_path), settings()
    rows = [item(title='Apple introduces a local community education initiative'),
            item(title='Apple to report financial results next week', url='https://www.apple.com/calendar/'),
            item(age=-3600, url='https://www.apple.com/future/'),
            item(age=90000, url='https://www.apple.com/old/')]
    unknown = item(url='https://www.apple.com/unknown/')
    unknown['published_at'] = None
    rows[0]['excerpt'] = 'A community education initiative.'
    assert store.ingest(issuer(), rows + [unknown], cfg, NOW, True) == 0


def test_disabled_x_configuration_never_spends(tmp_path, monkeypatch):
    from dualtrade.x_watch import XWatch, Ledger, load_settings
    monkeypatch.setenv('X_BEARER_TOKEN', 'unused-test-secret')
    cfg = load_settings()
    assert cfg['enabled'] is False
    class HTTP:
        def get(self, *a, **kw):
            raise AssertionError('X must not be contacted')
    result = XWatch(cfg, Ledger(tmp_path / 'x.db'), HTTP(), tmp_path / 'x-report').check(NOW)
    assert result['status'] == 'DISABLED'
    assert result['budget']['daily_accounted_usd'] == 0


def test_feed_parses_atom_and_rss_and_rejects_external_links_and_entities():
    source = issuer()
    atom = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Apple earnings</title>
        <link href="https://www.apple.com/newsroom/results/"/><updated>2026-10-22T13:00:00Z</updated>
        <summary>Financial results</summary></entry></feed>'''
    assert len(parse_feed(atom, source)) == 1
    rss = b'''<rss><channel><item><title>Apple earnings</title><link>https://www.apple.com/newsroom/results/</link>
        <pubDate>Thu, 22 Oct 2026 13:00:00 GMT</pubDate></item></channel></rss>'''
    assert parse_feed(rss, source)[0]['published_at'] == timestamp('2026-10-22T13:00:00Z')
    with pytest.raises(NewsError):
        parse_feed(rss.replace(b'www.apple.com', b'www.apple.com.attacker.example'), source)
    with pytest.raises(NewsError, match='UNSAFE_XML'):
        parse_feed(b'<!DOCTYPE test [<!ENTITY foo "bar">]><rss/>', source)


def test_sec_identity_and_important_item_filter():
    source = next(s for s in sources(settings()) if s['id'] == 'SEC:AAPL')
    body = {'cik': '320193', 'filings': {'recent': {
        'accessionNumber': ['0000320193-26-000001'], 'form': ['8-K'],
        'primaryDocument': ['apple.htm'], 'acceptanceDateTime': ['2026-10-22T13:00:00Z'],
        'items': ['2.02,9.01'], 'primaryDocDescription': ['Current report']}}}
    row = parse_sec(body, source)[0]
    assert importance(row, source) == ['SEC_ITEM_2.02']
    assert allowed(row['url'], source, document=True)
    row['items'] = ['9.01']
    assert importance(row, source) == []
    body['cik'] = '999'
    with pytest.raises(NewsError, match='SEC_ISSUER_MISMATCH'):
        parse_sec(body, source)


@pytest.mark.parametrize('url', ['http://www.apple.com/newsroom/results/',
    'https://www.apple.com.attacker.example/news/', 'https://user:pass@www.apple.com/news/',
    'https://127.0.0.1/private/', 'https://www.apple.com:8443/news/'])
def test_only_reviewed_https_hosts_are_allowed(url):
    assert not allowed(url, issuer(), document=True)


def test_atomic_leases_prevent_duplicate_agent_batches(tmp_path):
    one, two, cfg = NewsStore(tmp_path), NewsStore(tmp_path), settings()
    one.ingest(issuer(), [item()], cfg, NOW, False)
    assert len(one.claim(NOW, 3)) == 1
    assert two.claim(NOW + 1, 3) == []
    assert len(two.claim(NOW + 1801, 3)) == 1


def test_review_requires_fetched_source_and_exact_quote(tmp_path):
    store, cfg = NewsStore(tmp_path), settings()
    store.ingest(issuer(), [item()], cfg, NOW, False)
    event = store.claim(NOW, 3)[0]
    monitor = OfficialNews(cfg, store)
    review = {'event_id': event['id'], 'evidence_url': event['url'], 'document_sha256': 'abc',
              'summary_tr': 'Şirket çeyrek sonuçlarını açıkladı.',
              'why_it_matters_tr': 'Açıklanan gelir ve beklentiler şirket değerlendirmesine yeni bilgi ekliyor.',
              'uncertainties_tr': 'Piyasa fiyatına etkisi bu belgeyle belirlenemez.',
              'facts': [{'claim_tr': 'Şirketin geliri yüz milyon dolar oldu.', 'quote': 'Revenue was $100 million.'}]}
    with pytest.raises(NewsError, match='FETCHED_PRIMARY_DOCUMENT_REQUIRED'):
        monitor.complete([review], NOW)
    with store.db:
        store.db.execute('INSERT INTO documents VALUES (?,?,?,?,?,?)',
                         (event['id'], event['url'], 'abc', 'Revenue was $100 million.', NOW, '[]'))
    review['facts'][0]['quote'] = 'Revenue was $999 million.'
    with pytest.raises(NewsError, match='QUOTE_NOT_IN_PRIMARY_DOCUMENT'):
        monitor.complete([review], NOW)
    assert store.db.execute('SELECT state FROM events').fetchone()[0] == 'leased'
    review['facts'][0]['quote'] = 'Revenue was $100 million.'
    assert monitor.complete([review], NOW)['completed'] == [event['id']]
    saved = json.loads(store.db.execute('SELECT analysis FROM events').fetchone()[0])
    assert saved['eligible_for_trading'] is False
    assert saved['verification'] == 'PRIMARY_TEXT_READ'
    assert not store.pending(NOW)
    with pytest.raises(NewsError, match='EVENT_NOT_PENDING'):
        monitor.complete([review], NOW)


def test_unread_document_is_deferred_and_not_marked_analyzed(tmp_path):
    store, cfg = NewsStore(tmp_path), settings()
    store.ingest(issuer(), [item()], cfg, NOW, False)
    event = store.claim(NOW, 3)[0]
    monitor = OfficialNews(cfg, store)
    assert monitor.complete([{'event_id': event['id'], 'outcome': 'unavailable'}], NOW)['completed'] == []
    assert store.pending(NOW + 1) == []
    assert len(store.pending(NOW + 3601)) == 1
    assert store.db.execute('SELECT analysis FROM events').fetchone()[0] is None


def test_doc_link_cannot_be_used_as_arbitrary_fetcher(tmp_path):
    store, cfg = NewsStore(tmp_path), settings()
    store.ingest(issuer(), [item()], cfg, NOW, False)
    event = store.pending(NOW)[0]
    with pytest.raises(NewsError, match='DOCUMENT_LINK_NOT_DISCOVERED'):
        OfficialNews(cfg, store).document(event['id'], NOW, 'https://www.apple.com/other-page/')


def test_failed_requests_consume_quota_without_leaking_response_details(tmp_path):
    store, cfg = NewsStore(tmp_path), settings()
    cfg['daily_request_limit'] = 1
    class HTTP:
        def get(self, *args, **kwargs):
            raise requests.Timeout('private user-agent / token data')
    monitor = OfficialNews(cfg, store, HTTP())
    with pytest.raises(NewsError, match='NETWORK_FAILURE'):
        monitor.download(issuer()['url'], issuer(), NOW)
    with pytest.raises(NewsError, match='DAILY_REQUEST_LIMIT'):
        monitor.download(issuer()['url'], issuer(), NOW)


def test_fed_includes_policy_but_excludes_routine_bank_enforcement():
    source = next(s for s in sources(settings()) if s['id'] == 'FED:press')
    assert importance(item(title='Federal Reserve issues FOMC statement'), source) == ['monetary_policy']
    assert not importance(item(title='Federal Reserve terminates enforcement action with Small Bank'), source)
