"""Wider coverage without weakening provenance, identity or event independence."""
import time
import pytest
from dualtrade.news_guard import assess_news, news_votes, MAX_AGE, VOTE_MAX_AGE
from dualtrade.policy import sentiment_veto

NOW = 1800000000


def row(title, host='reuters.com', key='a', age=3600, **overrides):
    value = {'ticker': 'AAPL', 'title': title, 'description': '', 'snippet': '',
             'url': f'https://{host}/business/{key}', 'publisher': host, 'cluster': key,
             'time': NOW-age, 'retrieved_at': NOW, 'provider': 'Marketaux', 'score': -.8}
    value.update(overrides)
    return value


@pytest.mark.parametrize('title,kind', [
    ('Apple launches new iPhone with faster chips', 'product_launch'),
    ('Apple partners with Microsoft on enterprise software', 'partnership'),
    ('Apple wins government contract for secure devices', 'contract'),
    ('Apple announces share buyback and dividend increase', 'corporate_action'),
    ('Apple faces antitrust lawsuit over its store', 'regulatory_legal'),
    ('Apple appoints new CEO to lead its business', 'management'),
    ('Apple reports higher demand and production capacity', 'operations'),
    ('Apple reports Q3 earnings and revenue results', 'earnings_results'),
    ('Apple opens retail store in a new city', 'general_reporting'),
])
def test_expanded_event_types_and_general_reporting_are_admitted(title, kind):
    check = assess_news([row(title)], NOW)[0]['credibility']
    assert check['eligible'] and check['veto_eligible']
    assert check['claim_type'] == kind


@pytest.mark.parametrize('title', [
    'Apple will launch a new iPhone next month',
    'Apple says new chip does not require extra cooling',
    'Apple releases a software update in May',
])
def test_ordinary_modal_negation_and_month_words_are_not_false_news_signals(title):
    check = assess_news([row(title)], NOW)[0]['credibility']
    assert check['eligible'] and check['veto_eligible']


@pytest.mark.parametrize('host', ['financefeeds.com', 'gurufocus.com', 'foxbusiness.com',
                                 'livemint.com', 'business-standard.com', 'finance.yahoo.com'])
def test_new_editorial_domains_are_accepted_without_borrowing_trust_from_their_name(host):
    title = 'Apple launches new iPhone with faster chips'
    assert assess_news([row(title, host)], NOW)[0]['credibility']['veto_eligible']
    assert not assess_news([row(title, host+'.attacker.example')], NOW)[0]['credibility']['eligible']


@pytest.mark.parametrize('title,extra,status', [
    ('Analysts raise Apple price target after strong quarter', {}, 'analysis_context'),
    ('Apple could rise to a new high this quarter', {}, 'analysis_context'),
    ('Apple prepares a new product launch', {'description': 'Unconfirmed rumor circulating online'}, 'context_only'),
    ('Apple denies reports of weaker iPhone demand', {}, 'context_only'),
    ('Apple supplier announces layoffs across factories', {}, 'context_only'),
])
def test_analysis_rumours_denials_and_indirect_news_are_context_not_fake_or_directional(title, extra, status):
    report = assess_news([row(title, **extra)], NOW)[0]['credibility']
    assert report['eligible'] and report['status'] == status and not report['veto_eligible']
    assert not news_votes([row(title, **extra)], NOW)


@pytest.mark.parametrize('host', ['prnewswire.com', 'businesswire.com', 'globenewswire.com'])
def test_distribution_networks_do_not_supply_editorial_votes(host):
    value = row('Apple introduces new iPhone with faster chips', host)
    check = assess_news([value], NOW)[0]['credibility']
    assert check['eligible'] and check['status'] == 'press_release_context'
    assert not news_votes([value], NOW)


@pytest.mark.parametrize('hours,accepted,voting', [(8, True, True), (24, True, True), (48, True, True),
                                                (49, True, False), (72, True, False), (72.001, False, False)])
def test_age_boundaries_retain_context_and_do_not_retimestamp_news(hours, accepted, voting):
    original = row('Apple launches new iPhone with faster chips', age=hours*3600)
    result = assess_news([original], NOW)[0]
    assert result['credibility']['eligible'] == accepted
    assert result['credibility']['veto_eligible'] == voting
    assert result['time'] == original['time'] and result['retrieved_at'] == original['retrieved_at']


def test_age_reduces_weight_and_reposted_old_story_cannot_become_fresh():
    title = 'Apple launches new iPhone with faster chips'
    fresh = news_votes([row(title, age=3600)], NOW)[0]
    older = news_votes([row(title, age=25*3600)], NOW)[0]
    assert older['weight'] == pytest.approx(fresh['weight']/2)
    old = row(title, age=49*3600)
    repost = row(title, 'bloomberg.com', 'copy', age=3600)
    assert not news_votes([old, repost], NOW)


def test_identical_syndication_does_not_create_extra_source_groups_or_votes():
    values = [row('Apple launches new iPhone with faster chips', host, host)
              for host in ('reuters.com', 'finance.yahoo.com', 'livemint.com')]
    votes = news_votes(values, NOW)
    assert len(votes) == 1 and len(votes[0]['source_groups']) == 1
    assert not sentiment_veto(values, 1, 'news', NOW)


def test_three_reviewed_single_source_events_still_need_two_editorial_groups():
    titles = ['Apple revenue falls 10 percent in Q3 after weak demand',
              'Apple cuts guidance for Q3 after disappointing sales',
              'Apple announces layoffs affecting 500 workers']
    values = [row(title, key=str(i), age=8*3600) for i, title in enumerate(titles)]
    assert not sentiment_veto(values, 1, 'news', NOW)
    values[-1]['url'] = 'https://apnews.com/business/layoffs'
    assert sentiment_veto(values, 1, 'news', NOW)


def test_old_positive_event_has_less_weight_than_fresh_opposing_evidence():
    negative = [row(title, host, str(i)) for i, (title, host) in enumerate([
        ('Apple revenue falls 10 percent in Q3 after weak demand', 'reuters.com'),
        ('Apple cuts guidance for Q3 after disappointing sales', 'apnews.com'),
        ('Apple announces layoffs affecting 500 workers', 'bloomberg.com')])]
    positive = [row(title, host, 'positive'+host, score=.8) for title, host in [
        ('Apple launches iPhone with faster chip and improved battery life', 'reuters.com'),
        ('Apple launches new iPhone with faster chip amid battery improvements', 'bloomberg.com')]]
    assert not sentiment_veto(negative+positive, 1, 'news', NOW)
    for item in positive: item['time'] = NOW-36*3600
    assert sentiment_veto(negative+positive, 1, 'news', NOW)


def test_real_headline_shapes_keep_relevant_products_but_reject_wrong_entity_tags():
    # Real headline shapes from the earlier API run, with synthetic unit-test times.
    good = row('Supermicro Introduces the NVIDIA Vera CPU Rack', 'manilatimes.net', ticker='NVDA')
    good['url'] = 'https://www.manilatimes.net/tmt-newswire/pr-newswire/new-nvidia-system'
    assert assess_news([good], NOW)[0]['credibility']['status'] == 'press_release_context'
    wrong = row('Tiger Brokers Clinches Best Retail Broker in Singapore', 'prnewswire.com', ticker='AAPL')
    assert assess_news([wrong], NOW)[0]['credibility']['reasons'] == ['AMBIGUOUS_OR_MISSING_SUBJECT']


@pytest.mark.parametrize('repost', [False, True])
def test_collector_keeps_news_beyond_six_hours_while_social_retention_stays_separate(tmp_path, monkeypatch, repost):
    from dualtrade.collector import Collector
    from dualtrade.config import load
    from dualtrade.storage import Store
    from dualtrade import calendar, council
    now = time.time(); cfg = load(); cfg['bridge_directory'] = str(tmp_path)
    cfg['research']['sentiment_validation_file'] = str(tmp_path/'validation.json')
    store = Store(tmp_path/'state.db')
    store.set('pool:'+calendar.now_ny(now).date().isoformat(), ['AAPL'])
    value = row('Apple launches new iPhone with faster chips')
    value.update(time=now-49*3600, retrieved_at=now-100)
    store.set('evidence:news', [value])
    if repost:
        store.set('job:news:AAPL', {'attempt': now, 'success': now, 'error': '',
                                   'data': [{**value, 'time': now-3600}]})
    social = {**value, 'time': now-7*3600, 'author': 'one'}
    store.set('evidence:social', [social])
    monkeypatch.setattr(calendar, 'session', lambda day: (now-1800, now+7200))
    monkeypatch.setattr(council, 'snapshot', lambda *a, **k: {'healthy': False, 'decisions': {}})
    monkeypatch.setattr('dualtrade.investment_runner.build', lambda collector, snapshot, *a: snapshot.update(investment_ready=False))

    class Broker:
        def inspect(self): return {'account': {}, 'mappings': {}, 'positions': [], 'foreign_exposure': False}
    class Feeds:
        def social(self, symbols): return []
        def macro(self, stamp): return []
        def alpha(self, *a, **k): return []
    Collector(cfg, broker=Broker(), providers=Feeds(), store=store).cycle(now)
    assert len(store.get('evidence:news')) == (2 if repost else 1)
    assert all(r['credibility']['status'] == 'context_only' for r in store.get('evidence:news'))
    assert not news_votes(store.get('evidence:news'), now)
    assert store.get('evidence:social') == []
