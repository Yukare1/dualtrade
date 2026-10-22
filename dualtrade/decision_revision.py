"""Version observation evidence by conservative decision semantics.

This is a lexical/AST equivalence rule, not a proof of arbitrary program
equivalence. Decision code, unknown production files and configuration changes
invalidate observation. Functional acceptance and native request bindings still
use the independent, full-source identity. Legacy certificates are not migrated.
"""
from __future__ import annotations

import ast
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
import re
import tomllib

from .config import ROOT

SCHEMA = 1

# Conservative module boundary: mixed collector/CLI/storage modules stay in.
DECISION_FILES = frozenset('''
dualtrade/__init__.py dualtrade/agent_bridge.py dualtrade/ai_news.py
dualtrade/broker.py dualtrade/calendar.py dualtrade/cli.py dualtrade/collector.py
dualtrade/config.py dualtrade/context_agent.py dualtrade/council.py
dualtrade/credentials.py dualtrade/day_pool.py dualtrade/decision_revision.py
dualtrade/alpha_mcp.py dualtrade/shadow.py dualtrade/shadow_runner.py
dualtrade/investment_shadow.py
dualtrade/demo_exception.py dualtrade/fundamentals.py dualtrade/investment.py
dualtrade/investment_runner.py dualtrade/material_news.py dualtrade/mt5_mcp.py
dualtrade/news_guard.py dualtrade/official_news.py dualtrade/policy.py
dualtrade/providers.py dualtrade/risk.py dualtrade/storage.py dualtrade/swing.py
dualtrade/validation.py dualtrade/frozen/analyst.py dualtrade/frozen/ptr.py
MQL5/Experts/DualTrade.mq5 MQL5/Include/DualAgent.mqh
MQL5/Include/DualAgentBridge.mqh MQL5/Include/DualExecution.mqh
MQL5/Include/DualPolicy.mqh MQL5/Include/DualRisk.mqh
MQL5/Include/DualSignals.mqh MQL5/Include/DualSwing.mqh
MQL5/Include/Frozen/SRGuardCore.mqh
MQL5/Include/Frozen/SRGuardSignalRules.mqh
'''.split())

# Exact paths, never broad filename globs: a new production file is included.
NONDECISION_FILES = frozenset('''
dualtrade/history.py dualtrade/replay.py dualtrade/reporting.py dualtrade/x_watch.py
MQL5/Experts/DualTradeReplay.mq5 MQL5/Experts/DualTradeIntegrationTests.mq5
MQL5/Experts/DualTrade.ex5 MQL5/Experts/DualTradeIntegrationTests.ex5
MQL5/Experts/DualTradeReplay.ex5
MQL5/Include/Frozen/SRGuardFXEngine.mqh
'''.split())

# Prompt, model invocation, response handling and entry gates remain included.
# Unknown new functions/statements are also included by default.
SERVICE_INFRASTRUCTURE = frozenset('''
record_failure event token_usage notify_windows
'''.split())
SERVICE_INFRASTRUCTURE_GLOBALS = frozenset(('ERROR_LOG_BYTES',))
SERVICE_PATH = 'scripts/event-agent/service.py'


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                             ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class _WithoutDocstrings(ast.NodeTransformer):
    def strip(self, node):
        self.generic_visit(node)
        if (node.body and isinstance(node.body[0], ast.Expr) and
                isinstance(node.body[0].value, ast.Constant) and
                isinstance(node.body[0].value.value, str)):
            node.body = node.body[1:]
        return node

    visit_Module = strip
    visit_ClassDef = strip
    visit_FunctionDef = strip
    visit_AsyncFunctionDef = strip


@lru_cache(maxsize=256)
def python_semantics(text, service=False):
    tree = _WithoutDocstrings().visit(ast.parse(text))
    if service:
        body = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in SERVICE_INFRASTRUCTURE:
                continue
            if (isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) and
                    t.id in SERVICE_INFRASTRUCTURE_GLOBALS for t in node.targets)):
                continue
            body.append(node)
        tree.body = body
    return ast.dump(tree, annotate_fields=True, include_attributes=False)


_MQL_TOKEN = re.compile(
    r'(?P<space>[^\S\n]+)|(?P<newline>\n)|'
    r'(?P<line>//[^\n]*)|(?P<block>/\*[\s\S]*?\*/)|'
    r'(?P<literal>"(?:\\[\s\S]|[^"\\\n])*"|\'(?:\\[\s\S]|[^\'\\\n])*\')|'
    r'(?P<identifier>[A-Za-z_][A-Za-z_0-9]*)|'
    r'(?P<number>0[xX][0-9a-fA-F]+|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?[uUlLfF]*)|'
    r'(?P<operator>>>?=|<<=|\+\+|--|&&|\|\||==|!=|<=|>=|<<|>>|\+=|-=|\*=|/=|%=|&=|\|=|\^=|::|##)|'
    r'(?P<other>.)')


@lru_cache(maxsize=128)
def mql_semantics(text):
    # Backslash-newline splicing precedes comment handling in preprocessing.
    text = re.sub(r'\\\r?\n', '', text).replace('\r\n', '\n').replace('\r', '\n')
    tokens, line, positions = [], [], []

    def finish():
        if line:
            if line[0] == '#':
                # Function-like macros require '(' adjacent to the name.
                adjacent = (len(line) > 3 and line[1] == 'define' and line[3] == '(' and
                            positions[2][1] == positions[3][0])
                tokens.append(['directive', line.copy(), adjacent])
            else:
                tokens.extend(['token', token] for token in line)
        line.clear()
        positions.clear()

    position = 0
    for match in _MQL_TOKEN.finditer(text):
        if match.start() != position:
            raise ValueError('INVALID_MQL_TOKEN_STREAM')
        position = match.end()
        kind, token = match.lastgroup, match.group()
        if kind == 'newline':
            finish()
        elif kind not in ('space', 'line', 'block'):
            if kind == 'other' and token in ('"', "'"):
                raise ValueError('UNTERMINATED_MQL_LITERAL')
            if kind == 'other' and text[match.start():].startswith('/*'):
                raise ValueError('UNTERMINATED_MQL_COMMENT')
            line.append(token)
            positions.append((match.start(), match.end()))
    if position != len(text):
        raise ValueError('INVALID_MQL_TOKEN_STREAM')
    finish()
    return tokens


@lru_cache(maxsize=256)
def _file_semantic_hash(relative, raw):
    if relative.endswith('.py'):
        semantic = python_semantics(raw.decode('utf-8-sig'), relative == SERVICE_PATH)
    elif Path(relative).suffix in ('.mq5', '.mqh'):
        semantic = mql_semantics(raw.decode('utf-8-sig'))
    else:
        semantic = {'unknown_production_bytes': sha256(raw).hexdigest()}
    return _digest(semantic)


def decision_manifest(root=None):
    root = Path(root) if root is not None else ROOT
    missing = sorted(path for path in DECISION_FILES | {SERVICE_PATH} if not (root / path).is_file())
    if missing:
        raise ValueError('DECISION_SOURCE_MISSING:' + ','.join(missing))
    paths = set(DECISION_FILES)
    for folder in ('dualtrade', 'MQL5'):
        for path in (root / folder).rglob('*'):
            if not path.is_file() or '__pycache__' in path.parts:
                continue
            relative = path.relative_to(root).as_posix()
            if relative not in NONDECISION_FILES:
                paths.add(relative)
    manifest = {}
    for relative in sorted(paths | {SERVICE_PATH}):
        path = root / relative
        if path.is_symlink():
            raise ValueError('DECISION_SOURCE_SYMLINK_UNSUPPORTED')
        manifest[relative] = _file_semantic_hash(relative, path.read_bytes())
    return manifest


def _json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def decision_configuration(cfg, root=None):
    root = Path(root) if root is not None else ROOT
    council = Path(cfg.get('council_file', 'council.example.json'))
    if not council.is_absolute():
        council = root / council
    model_path = Path.home() / '.codex' / 'config.toml'
    try:
        model = tomllib.loads(model_path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        model = {}
    project = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8-sig'))
    # No credentials or unrelated application settings enter the output.
    return {'config': cfg, 'council': _json(council),
            'official_news': _json(root / 'official-news.json'),
            'review_schema': _json(root / 'scripts/event-agent/review.schema.json'),
            'model': {key: model.get(key) for key in ('model', 'model_reasoning_effort')},
            'dependencies': project.get('project', {}).get('dependencies', []),
            'dependency_lock': [line.strip() for line in (root / 'requirements.lock.txt').read_text().splitlines()
                                if line.strip() and not line.lstrip().startswith('#')]}


def observation_revision(cfg, root=None):
    return {'schema': SCHEMA, 'decision_hash': _digest(decision_manifest(root)),
            'decision_config': _digest(decision_configuration(cfg, root))}


def observation_context(cfg, root=None):
    """Observation key only; never substitute for full-source order bindings."""
    return _digest(observation_revision(cfg, root))
