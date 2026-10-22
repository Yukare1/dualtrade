"""User-approved day-demo observation exception, 22 October only.

Never writes or certifies observation evidence. Every other gate stays in force;
the native EA independently enforces this exact date and deadline.
"""
from .config import ROOT, config_hash
from .storage import fingerprint, read_json

AUTHORIZATION = 'user-20261022-day-observation-waiver'
START = 1792676700  # 2026-10-22 16:45 Europe/Istanbul
END = 1792697100    # 2026-10-22 22:25 Europe/Istanbul; exclusive
PATH = ROOT / 'runtime/demo-observation-waiver-2026-10-22.json'


def account_pin(cfg):
    return fingerprint({'login': str(cfg['expected_login']), 'server': cfg['expected_server'],
                        'currency': 'USD', 'margin_mode': 'hedging', 'type': 'demo'})


def active(cfg, now, style, until, record=None):
    from .cli import source_hash
    r = read_json(PATH, {}) if record is None else record
    context = fingerprint({'config_hash': config_hash(cfg), 'source_hash': source_hash()})
    valid = (style == 'day' and type(until) in (int, float) and until == END and
             START <= now < END and r.get('schema') == 1 and r.get('approved') is True and
             r.get('authorization') == AUTHORIZATION and r.get('scope') == 'day-demo' and
             r.get('expires') == END and type(r.get('issued_at')) in (int, float) and
             START <= r['issued_at'] <= now and r.get('context') == context and
             r.get('account_pin') == account_pin(cfg))
    return {'authorization': AUTHORIZATION, 'context': context, 'expires': END} if valid else None
