"""Regression evidence for observation certificates and economic imports."""
from datetime import date
from types import SimpleNamespace

import pytest

from dualtrade import calendar as cal
from dualtrade.ai_news import validate_assessment
from dualtrade.collector import Collector
from dualtrade.material_news import classify, numeric_evidence, verify_review


class MemoryStore:
    def __init__(self, data, prefix):
        self.data, self.prefix = data, prefix

    def get(self, key, default=None):
        return self.data.get(self.prefix + key, default)

    def set(self, key, value):
        self.data[self.prefix + key] = value


@pytest.mark.parametrize('prefix', ['', 'swing:', 'investment:'])
@pytest.mark.parametrize('valid', [True, False])
def test_completed_observation_cannot_certify_changed_revision_after_window(prefix, valid):
    data = {}
    collector = object.__new__(Collector)
    collector.store = MemoryStore(data, prefix)
    session = cal.session(date(2026, 10, 23))
    start, finish = session[0] + 900, session[1] - 300
    for now in range(start, finish + 1, 30):
        collector.observation(now, True, session, 'observed-revision')
    certificate = dict(data[prefix + 'observation_pass'])
    collector.observation(finish + 60, valid, session, 'unobserved-revision')
    assert data[prefix + 'observation_pass'] == certificate
    assert certificate['config_hash'] == 'observed-revision'


@pytest.mark.parametrize('prefix', ['', 'swing:', 'investment:'])
def test_late_completion_uses_matching_observed_revision(prefix):
    collector = object.__new__(Collector)
    collector.store = MemoryStore({}, prefix)
    session = cal.session(date(2026, 10, 23))
    start, finish = session[0] + 900, session[1] - 300
    for now in range(start, finish, 15):
        collector.observation(now, True, session, 'observed-revision')
    assert collector.store.get('observation_pass') is None
    collector.observation(finish + 5, True, session, 'observed-revision')
    assert collector.store.get('observation_pass')['config_hash'] == 'observed-revision'


def economic_review(source_amount='$120 million', anchor='$120 million', value=120):
    url = 'https://www.apple.com/newsroom/2026/10/results/'
    quote = 'FY2027 net income guidance increased from $100 million to ' + source_amount + '.'
    economics = dict(kind='guidance', source=url, event_key='AAPL.FY2027.net_income',
                     quote=quote, period='FY2027', metric='net_income', unit='USD million',
                     before=100, after=value, before_text='$100 million', after_text=anchor)
    review = dict(evidence_url=url, facts=[{'quote': 'Apple announced results.'}],
                  documents_read=[dict(url=url, sha256='hash', parts=1)],
                  assessment=dict(schema=1, direction='positive', confidence=.9,
                                  material=True, horizon='long_term',
                                  rationale_tr='The complete amount must match the source evidence.',
                                  evidence_facts=[0], economics=[economics]))
    event = dict(published=1000, url=url, source_id='IR:AAPL', ticker='AAPL', id='event')
    doc = dict(url=url, hash='hash', text='Apple announced results. ' + quote, links='[]')
    return review, event, doc, economics


@pytest.mark.parametrize('source_amount,anchor,value', [
    ('$120 million', '$12', 12),
    ('$120 million', '20 million', 20),
    ('$120 million', '$120', 120),
    ('$120.5 million', '$120', 120),
    ('$1,200 million', '200 million', 200),
    ('€120 million', '120 million', 120),
    ('€120 million', '€120 million', 120),
    ('£120 million', '£120 million', 120),
    ('EUR 120 million', 'EUR 120 million', 120),
    ('120 million EUR', '120 million', 120),
    ('120 million EUR', '120 million EUR', 120),
    ('$120 million EUR', '$120 million EUR', 120),
    ('$120 billion', '$120 billion', 120),
    ('$1,20 million', '$1,20 million', 120),
])
@pytest.mark.parametrize('importer', ['ai', 'primary'])
def test_economic_import_rejects_truncated_or_wrong_unit_amounts(source_amount, anchor, value, importer):
    review, event, doc, economics = economic_review(source_amount, anchor, value)
    with pytest.raises(ValueError, match='ANCHOR'):
        if importer == 'ai':
            validate_assessment(review, event, [doc], {(doc['url'], doc['hash'], 1)}, 1100)
        else:
            primary = dict(economics, ticker='AAPL', reviewed=True, reviewer='fixture', published_at=1000)
            response = SimpleNamespace(text=doc['text'], content=doc['text'].encode())
            providers = SimpleNamespace(request=lambda *a, **k: response, key=lambda name: 'Fixture test@example.test')
            verify_review(primary, {'symbols': {'AAPL': {'cik': '320193'}}}, providers, 1100)


def test_complete_amount_keeps_positive_guidance_positive():
    review, event, doc, _ = economic_review()
    result = validate_assessment(review, event, [doc], {(doc['url'], doc['hash'], 1)}, 1100)
    assert classify(result['economics'][0], {}) == 'positive'


@pytest.mark.parametrize('anchor,value,unit', [
    ('$100 million', 100, 'USD million'),
    ('$100 million', 100_000_000, 'USD'),
    ('$1,200.5 million', 1200.5, 'USD million'),
    ('USD 100 million', 100, 'USD million'),
    ('100 million USD', 100, 'USD million'),
    ('€100 million', 100, 'EUR million'),
    ('£100 million', 100, 'GBP million'),
    ('100', 100, 'USD million'),
])
def test_complete_numeric_evidence_supports_exact_scales_and_currency(anchor, value, unit):
    assert numeric_evidence(anchor, value, unit, 'The amount is ' + anchor + '.')
