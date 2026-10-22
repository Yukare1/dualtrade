import json
import math
from pathlib import Path
from .storage import fingerprint

ROOT = Path(__file__).resolve().parents[1]


def load(path=None):
    path = Path(path) if path else ROOT / "config.local.json"
    if not path.exists():
        path = ROOT / "config.example.json"
    cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    if cfg.get("schema") != 1:
        raise ValueError("Unsupported configuration schema")
    cfg['research'].setdefault('context_provider', 'official_agent')
    from .day_pool import pool_size
    cfg['research']['day_pool_size'] = pool_size(cfg)
    if cfg['research']['context_provider'] != 'official_agent':
        raise ValueError('Only the official document agent context policy is supported; Reddit is retired')
    r = cfg["risk"]
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in r.values()):
        raise ValueError("Risk limits must be finite numbers")
    if not (0 < r["target"] <= 40 and r["target"] <= r["absolute_ceiling"] <= 100 and 0 < r["aggregate"] <= 80 and 0 < r["daily_loss"] <= 200 and 0.2 <= r["free_margin_fraction"] < 1 and 1 <= r["daily_entries"] <= 5 and r["cooldown_seconds"] >= 3600):
        raise ValueError("Configuration exceeds authorized risk limits")
    cfg.setdefault('magic_investment', 26102203)
    magics = [cfg[k] for k in ('magic_day', 'magic_swing', 'magic_investment')]
    if any(type(v) is not int or v <= 0 for v in magics) or len(set(magics)) != 3:
        raise ValueError("Day, swing and investment identifiers must differ")
    from .risk import validate
    cfg['shared_risk'] = validate(cfg)
    from .swing import options as swing_options, DEFAULTS as SWING_DEFAULTS
    swing = swing_options(cfg)
    if set(swing) != set(SWING_DEFAULTS) or any(type(v) is not bool for v in swing.values()):
        raise ValueError('Invalid swing settings')
    if not swing['long_only']:
        raise ValueError('New technical swing policy is long-only')
    cfg['swing'] = swing
    from .investment import options, DEFAULTS
    inv = options(cfg)
    if not isinstance(inv['enabled'], bool): raise ValueError('Investment enabled must be boolean')
    for key, expected in DEFAULTS.items():
        if key not in ('enabled', 'events_file') and inv[key] != expected:
            raise ValueError('Investment research policy change requires a reviewed policy revision: '+key)
    if not isinstance(inv['events_file'], str): raise ValueError('Invalid material event file')
    return cfg


def bridge(cfg):
    p = Path(cfg["bridge_directory"])
    return p if p.is_absolute() else ROOT / p


def config_hash(cfg):
    # Council membership/importance changes require a new observation session.
    council=Path(cfg.get('council_file','council.example.json'))
    if not council.is_absolute():council=ROOT/council
    from .news_guard import policy_signature
    return fingerprint({'config':cfg,'council':json.loads(council.read_text(encoding='utf-8-sig')) if council.exists() else None,
                        'news_guard':policy_signature()})
