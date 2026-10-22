"""Synthetic adversarial cases; these are not a real-world accuracy benchmark."""
from copy import deepcopy
import time

import pytest

from dualtrade.news_guard import assess_news, canonical_url, news_report, news_summary, news_votes, MAX_AGE, VOTE_MAX_AGE, MATCH_WINDOW
from dualtrade.policy import sentiment_veto


NOW = 1800000000
HEADLINES = (
    ('Apple revenue falls 10 percent in Q3 as demand weakens',
     'Apple Q3 revenue falls 10 percent amid weak demand and slowing sales'),
    ('Apple cuts guidance for Q3 after weak demand and slowing sales',
     'Apple cuts Q3 guidance amid slowing sales and weak demand'),
    ('Apple announces layoffs affecting 500 workers as demand weakens',
     'Apple confirms layoffs of 500 workers amid weak demand'),
)


def article(title=HEADLINES[0][0], host='reuters.com', identity='a', now=NOW, **overrides):
    row = {'ticker': 'AAPL', 'title': title, 'description': '', 'snippet': '',
           'url': f'https://{host}/business/{identity}', 'publisher': host,
           'cluster': identity, 'time': now - 100, 'retrieved_at': now - 10,
           'provider': 'Marketaux', 'score': -.8, 'reliable': True, 'author': ''}
    row.update(overrides)
    return row


def reports(now=NOW):
    return [article(title, host, str(i) + host, now)
            for i, pair in enumerate(HEADLINES)
            for title, host in zip(pair, ('reuters.com', 'bloomberg.com'))]


def test_corroboration_produces_one_vote_per_event_and_three_event_veto():
    rows = reports()
    original = deepcopy(rows)
    assessed = assess_news(rows, NOW)
    assert rows == original
    assert all(row['credibility']['status'] == 'corroborated_reporting' for row in assessed)
    assert news_summary(assessed)['eligible_events'] == 3
    assert len(news_votes(rows, NOW)) == 3
    assert sentiment_veto(rows, 1, 'news', NOW)
    assert not sentiment_veto(rows, -1, 'news', NOW)
    assert not sentiment_veto(rows[:4], 1, 'news', NOW)


@pytest.mark.parametrize('url', [
    'https://reuters.com.attacker.example/business/story',
    'https://reuters.com@attacker.example/business/story',
    'https://attacker.example/reuters.com/story',
    'https://news.reuters.com/business/story',  # Subdomains are not implicitly trusted.
    'https://reuters.com:8080/business/story',
    'http://reuters.com/business/story', 'https://reuters.com/',
    'https://reuters.com\\@attacker.example/story', 'https://reuters.com/\narticle',
])
def test_publisher_name_and_forged_acceptance_cannot_authorize_spoofed_url(url):
    row = article(url=url, publisher='Reuters', credibility={'eligible': True, 'version': 1})
    check = assess_news([row], NOW)[0]
    assert not check['reliable'] and not check['credibility']['eligible']


def test_canonical_urls_remove_tracking_but_keep_article_identity():
    assert canonical_url('https://www.reuters.com/story/?utm_source=x&article=2#part')[1] == 'https://reuters.com/story?article=2'


@pytest.mark.parametrize('extra', [
    {'snippet': 'This is parody and satire'},
    {'snippet': 'Guaranteed returns! To the moon 🚀'},
    {'time': NOW + 1}, {'time': NOW - MAX_AGE - 1},
    {'retrieved_at': NOW + 1}, {'retrieved_at': NOW - 101},
    {'time': float('nan')}, {'retrieved_at': None}, {'time': True},
    {'score': float('nan')}, {'score': float('inf')}, {'score': True},
    {'title': 'Microsoft revenue falls 10 percent in Q3 as demand weakens'},
    {'provider': 'Unknown'}, {'provider': None},
])
def test_ambiguous_speculative_stale_or_invalid_rows_never_enter_policy(extra):
    rows = [article(**extra), article(HEADLINES[0][1], 'bloomberg.com', 'b')]
    assert not assess_news(rows, NOW)[0]['credibility']['eligible']
    assert not sentiment_veto(rows, 1, 'news', NOW)


def test_single_trusted_report_is_unconfirmed_not_declared_false():
    check = assess_news([article()], NOW)[0]['credibility']
    assert check['status'] == 'single_source' and check['eligible'] and check['veto_eligible']
    assert check['weight'] < 1
    assert 'REVIEWED_SOURCE_REPORTING' in check['reasons']
    assert not sentiment_veto([article()], 1, 'news', NOW)


def test_legal_company_suffix_and_url_path_are_not_sarcasm_markers():
    for text in ('Carlsberg A/S is preparing an IPO', 'See https://example.test/s for details'):
        check = assess_news([article(description=text)], NOW)[0]['credibility']
        assert 'SATIRE_OR_MANIPULATION' not in check['reasons']
    check = assess_news([article(description='Guaranteed success /s')], NOW)[0]['credibility']
    assert 'SATIRE_OR_MANIPULATION' in check['reasons']


def test_report_explains_withheld_content_and_reassesses_cached_flags():
    rows = reports() + [article(host='troll.example', identity='troll')]
    report = news_report(rows, NOW)
    assert report['counts'] == {'corroborated_reporting': 6, 'unverified': 1}
    assert report['articles'][-1]['credibility']['reasons'] == ['UNKNOWN_SOURCE_DOMAIN']
    assert report['articles'][0]['credibility']['supporting_urls']
    assert news_report(rows, NOW + MAX_AGE + 101)['eligible_events'] == 0


@pytest.mark.parametrize('kind', ['identical', 'same_owner', 'wire', 'uuid', 'tracking_url'])
def test_syndication_and_common_ownership_do_not_supply_independent_support(kind):
    rows = reports()[:2]
    if kind == 'identical': rows[1]['title'] = rows[0]['title']
    if kind == 'same_owner':
        rows[0]['url'] = 'https://wsj.com/business/a'
        rows[1]['url'] = 'https://marketwatch.com/business/b'
    if kind == 'wire': rows[1]['description'] = 'Reporting by Reuters'
    if kind == 'uuid': rows[1]['cluster'] = rows[0]['cluster']
    if kind == 'tracking_url': rows[1]['url'] = rows[0]['url'] + '?utm_source=x'
    assert len(news_votes(rows, NOW)) == 1
    assert not any(r['credibility']['status'] == 'corroborated_reporting' for r in assess_news(rows, NOW))
    assert not sentiment_veto(rows, 1, 'news', NOW)


@pytest.mark.parametrize('change', ['numbers', 'direction', 'denial'])
def test_conflicting_reporting_is_quarantined_instead_of_majority_vote(change):
    rows = reports()[:2]
    third = article(HEADLINES[0][1], 'apnews.com', 'third')
    if change == 'numbers': third['title'] = third['title'].replace('10 percent', '20 percent')
    if change == 'direction': third['title'] = third['title'].replace('falls', 'rises')
    if change == 'denial': third['description'] = 'The company denies this report'
    checked = assess_news(rows + [third], NOW)
    assert checked[0]['credibility']['status'] == 'conflicting'
    assert not news_votes(rows + [third], NOW)


def test_troll_denial_cannot_poison_independent_trusted_reporting():
    rows = reports()[:2] + [article(host='troll.example', identity='troll', description='The company denies this report')]
    assert len(news_votes(rows, NOW)) == 1


def test_different_periods_and_late_articles_cannot_confirm_the_same_claim():
    rows = reports()[:2]
    rows[1]['title'] = rows[1]['title'].replace('Q3', 'Q2')
    assert len(news_votes(rows, NOW)) == 2
    assert not any(r['credibility']['status'] == 'corroborated_reporting' for r in assess_news(rows, NOW))
    rows = reports()[:2]
    rows[1]['time'] -= MATCH_WINDOW + 1
    assert len(news_votes(rows, NOW)) == 2
    assert not any(r['credibility']['status'] == 'corroborated_reporting' for r in assess_news(rows, NOW))


def test_opposing_sentiment_on_same_report_abstains_and_legacy_cache_is_rejected():
    rows = reports()[:2]
    rows[1]['score'] = .8
    assert not news_votes(rows, NOW)
    old = [{'time': NOW - 1, 'score': -.8, 'reliable': True,
            'cluster': str(i), 'publisher': str(i % 2)} for i in range(5)]
    assert not sentiment_veto(old, 1, 'news', NOW)
    assessed = assess_news(reports(), NOW)
    assert not news_votes(assessed, NOW + VOTE_MAX_AGE + 101)


def test_collector_preserves_reports_and_exposes_verdict_counts(tmp_path, monkeypatch):
    from dualtrade.collector import Collector
    from dualtrade.config import load
    from dualtrade.storage import Store
    from dualtrade import calendar, council
    from dualtrade import official_news
    real_news_store=official_news.NewsStore
    def isolated_news_store(directory=None):
        from pathlib import Path
        assert directory is not None and Path(directory).resolve().is_relative_to(tmp_path.resolve())
        return real_news_store(directory)
    monkeypatch.setattr(official_news,'NewsStore',isolated_news_store)
    now = time.time()
    cfg = load()
    cfg['bridge_directory'] = str(tmp_path)
    cfg['research']['sentiment_validation_file'] = str(tmp_path / 'validation.json')
    store = Store(tmp_path / 'state.db')
    store.set('pool:' + calendar.now_ny(now).date().isoformat(), ['AAPL'])
    store.set('job:news:AAPL', {'attempt': now, 'success': now, 'data': reports(now), 'error': ''})
    monkeypatch.setattr(calendar, 'session', lambda day: (now - 1800, now + 7200))
    monkeypatch.setattr(council, 'snapshot', lambda *args, **kwargs: {'healthy': False, 'decisions': {}})
    monkeypatch.setattr('dualtrade.investment_runner.build', lambda collector, snapshot, *args: snapshot.update(investment_ready=False))

    class Broker:
        def inspect(self):
            return {'account': {}, 'mappings': {}, 'positions': [], 'foreign_exposure': False}

    class Feeds:
        def news(self, pool): return reports(now)
        def social(self, pool): return []
        def macro(self, stamp): return []
        def alpha(self, *args, **kwargs): return []

    snap = Collector(cfg, broker=Broker(), providers=Feeds(), store=store).cycle(now)
    assert snap['news_credibility']['eligible_events'] == 3
    assert len(store.get('evidence:news')) == 6
    assert (tmp_path/'official-news/state.sqlite3').exists()
    assert sentiment_veto(store.get('evidence:news'), 1, 'news', now)


def test_policy_change_invalidates_old_sentiment_acceptance(monkeypatch):
    from dualtrade.config import load, config_hash
    from dualtrade import news_guard
    cfg = load()
    before = config_hash(cfg)
    monkeypatch.setattr(news_guard, 'policy_signature', lambda: 'changed-policy')
    assert config_hash(cfg) != before


def test_marketaux_retains_provenance_without_asserting_truth(tmp_path, monkeypatch):
    from dualtrade.providers import Providers
    from dualtrade.config import load
    from dualtrade.storage import Store
    from datetime import datetime, timezone
    monkeypatch.setenv('MARKETAUX_API_TOKEN', 'test-only-token')
    monkeypatch.setattr('dualtrade.providers.time.time', lambda: NOW)
    valid = {'published_at': datetime.fromtimestamp(NOW - 100, timezone.utc).isoformat(),
             'uuid': 'a', 'url': 'https://reuters.com/business/a', 'source': 'Reuters',
             'title': HEADLINES[0][0], 'description': 'Original article description',
             'snippet': 'Original extract', 'entities': [{'symbol': 'AAPL', 'sentiment_score': -.8}]}

    class Response:
        def json(self):
            return {'data': [valid, {**valid, 'published_at': 'bad'},
                             {**valid, 'published_at': '2026-10-22T12:00:00'},
                             {**valid, 'entities': [{'symbol': 'AAPL', 'sentiment_score': float('nan')}]},
                             {**valid, 'entities': [{'symbol': 'AAPL', 'sentiment_score': True}]},
                             {**valid, 'entities': None}]}

    feeds = Providers(load(), Store(tmp_path / 'state.db'))
    monkeypatch.setattr(feeds, 'request', lambda *args, **kwargs: Response())
    rows = feeds.news(['AAPL'])
    assert len(rows) == 1
    assert rows[0]['reliable'] is False and rows[0]['sentiment_valid'] is True
    assert rows[0]['retrieved_at'] == NOW and rows[0]['provider'] == 'Marketaux'
    assert rows[0]['description'] == valid['description']
    assert rows[0]['snippet'] == valid['snippet']
    assert assess_news(rows, NOW)[0]['credibility']['status'] == 'single_source'


def test_sentiment_acceptance_does_not_conflate_valid_score_with_true_reporting(tmp_path):
    from dualtrade.config import load
    from dualtrade.storage import write_json
    from dualtrade.validation import validate_sentiment
    cfg = load()
    cfg['research']['sentiment_validation_file'] = str(tmp_path / 'acceptance.json')
    # Synthetic labels test the validation plumbing only, in an isolated folder.
    rows = []
    for label in (-1, 0, 1):
        for _ in range(10):
            rows.append({'source': 'social', 'text': str(label), 'label': label})
            rows.append({'source': 'news', 'label': label, 'score': label,
                         'sentiment_valid': True, 'reliable': False,
                         'url': 'https://example.test/story', 'retrieved_at': NOW,
                         'provider': 'Marketaux'})
    labels = tmp_path / 'labels.json'
    write_json(labels, rows)

    class Model:
        def classify(self, text): return int(text), True
        def model_signature(self): return 'synthetic-test-model'

    assert validate_sentiment(cfg, Model(), labels)['passed']
    for row in rows:
        if row['source'] == 'news':
            row.pop('sentiment_valid')
            row['reliable'] = True  # Old credibility flags cannot validate the score.
    write_json(labels, rows)
    assert not validate_sentiment(cfg, Model(), labels)['passed']
