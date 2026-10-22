"""Session-aware coverage checks use broker hours, never an age-only cutoff."""
from datetime import datetime, timedelta, timezone

import pytest

from dualtrade import agent_bridge as agent, calendar as cal


def ny(day, hour, minute=0):
    return datetime.fromisoformat(day).replace(hour=hour, minute=minute, tzinfo=cal.NY).timestamp()


def schedule(tmp_path, now, offset=10800, *, missing_day=None):
    # Broker stock CFD session 09:35�15:55 New York, converted to server time.
    ny_offset = int(cal.now_ny(now).utcoffset().total_seconds())
    opening = 9 * 3600 + 35 * 60 + offset - ny_offset
    closing = 15 * 3600 + 55 * 60 + offset - ny_offset
    lines = [f'CLOCK|{int(now)}|{int(now + offset)}']
    lines += [f'SESSION|AAPL.NAS|{d}|0|{opening}|{closing}'
              for d in range(1, 6) if d != missing_day]
    (tmp_path / 'broker-native.txt').write_text('\n'.join(lines), encoding='utf-8')
    return {'bridge_directory': str(tmp_path)}


def rows(stamps, offset):
    return [dict(time=datetime.fromtimestamp(t + offset, timezone.utc).replace(tzinfo=None).isoformat(),
                 open=100., high=101., low=99., close=100.5) for t in stamps]


def m15(day):
    # Last bucket opens at 15:45 and overlaps the broker's 15:55 close.
    return [ny(day, 9, 30) + i * 900 for i in range(26)]


def test_missing_latest_in_session_bar_is_stale(tmp_path):
    now = ny('2026-10-22', 10, 15)
    cfg = schedule(tmp_path, now)
    windows = agent.history_session_windows(cfg, 'AAPL.NAS', 10800, now)
    stamps = m15('2026-10-21') + [ny('2026-10-22', 9, 30), ny('2026-10-22', 9, 45)]
    with pytest.raises(ValueError, match='CLOSED_HISTORY_STALE'):
        agent.closed_history(rows(stamps, 10800), 10800, 900, now, 2, session_windows=windows)


def test_interior_session_gap_is_rejected_but_overnight_gap_is_valid(tmp_path):
    now = ny('2026-10-22', 10, 15)
    cfg = schedule(tmp_path, now)
    windows = agent.history_session_windows(cfg, 'AAPL.NAS', 10800, now)
    stamps = m15('2026-10-21') + [ny('2026-10-22', 9, 30), ny('2026-10-22', 9, 45), ny('2026-10-22', 10)]
    assert agent.closed_history(rows(stamps, 10800), 10800, 900, now, 2,
                                session_windows=windows)[-1]['time'] == ny('2026-10-22', 10)
    stamps.remove(ny('2026-10-21', 11, 15))
    with pytest.raises(ValueError, match='CLOSED_HISTORY_SESSION_GAP'):
        agent.closed_history(rows(stamps, 10800), 10800, 900, now, 2, session_windows=windows)


def test_monday_morning_h4_accepts_last_friday_completed_bucket(tmp_path):
    now = ny('2026-10-19', 10)
    cfg = schedule(tmp_path, now)
    windows = agent.history_session_windows(cfg, 'AAPL.NAS', 10800, now)
    # Server 16:00 and 20:00 correspond to New York 09:00 and 13:00.
    stamps = [ny('2026-10-16', 9), ny('2026-10-16', 13), ny('2026-10-19', 9)]
    bars = agent.closed_history(rows(stamps, 10800), 10800, 14400, now, 2, session_windows=windows)
    assert bars[-1]['time'] == ny('2026-10-16', 13)


def test_holiday_and_early_close_require_no_phantom_afternoon_bars(tmp_path):
    now = ny('2026-11-27', 13, 30)
    cfg = schedule(tmp_path, now, offset=7200)
    windows = agent.history_session_windows(cfg, 'AAPL.NAS', 7200, now)
    assert windows[0][0] == ny('2026-11-25', 9, 35)  # Thanksgiving skipped by exchange calendar.
    assert windows[-1][1] == ny('2026-11-27', 13)
    stamps = m15('2026-11-25') + [ny('2026-11-27', 9, 30) + i * 900 for i in range(14)]
    assert agent.closed_history(rows(stamps, 7200), 7200, 900, now, 2,
                                session_windows=windows)[-1]['time'] == ny('2026-11-27', 12, 45)


def test_missing_prior_session_schedule_never_skips_coverage(tmp_path):
    now = ny('2026-10-19', 10)
    cfg = schedule(tmp_path, now, missing_day=5)
    with pytest.raises(ValueError, match='HISTORY_SESSION_SCHEDULE_UNAVAILABLE'):
        agent.history_session_windows(cfg, 'AAPL.NAS', 10800, now)


@pytest.mark.parametrize('day,first_h4_hour', [('2026-10-26', 6), ('2026-11-02', 9)])
def test_clock_change_requires_completed_current_h4_before_review(tmp_path, day, first_h4_hour):
    # The first partial-session H4 bucket follows the server grid, not NY 09:00.
    # Oct 26: broker+2 / NY-4 gives 06:00�10:00 NY; Nov 2 gives 09:00�13:00.
    offset = 7200
    now = ny(day, first_h4_hour + 4) - 60
    cfg = schedule(tmp_path, now, offset)
    windows = agent.history_session_windows(cfg, 'AAPL.NAS', offset, now)
    assert len(windows) == 1 and windows[0][0] == ny(day, 9, 35)
    prior = cal.previous_session(datetime.fromisoformat(day).date()).isoformat()
    stamps = [ny(prior, 9), ny(prior, 13), ny(day, first_h4_hour)]
    with pytest.raises(ValueError, match='CLOSED_HISTORY_STALE'):
        agent.closed_history(rows(stamps, offset), offset, 14400, now, 2, session_windows=windows)
    now = ny(day, first_h4_hour + 4) + 60
    cfg = schedule(tmp_path, now, offset)
    windows = agent.history_session_windows(cfg, 'AAPL.NAS', offset, now)
    bars = agent.closed_history(rows(stamps, offset), offset, 14400, now, 2, session_windows=windows)
    assert bars[-1]['time'] == ny(day, first_h4_hour)


def test_clock_change_m15_checks_current_session_gaps_without_trusting_prior_offset(tmp_path):
    day = '2026-10-26'
    now, offset = ny(day, 10, 15), 7200
    cfg = schedule(tmp_path, now, offset, missing_day=5)
    windows = agent.history_session_windows(cfg, 'AAPL.NAS', offset, now)
    # No prior-session schedule is used when its clock cannot be verified.
    assert len(windows) == 1
    stamps = [ny(day, 9, 30), ny(day, 9, 45), ny(day, 10)]
    assert len(agent.closed_history(rows(stamps, offset), offset, 900, now, 2,
                                    session_windows=windows)) == 2
    with pytest.raises(ValueError, match='CLOSED_HISTORY_SESSION_GAP'):
        agent.closed_history(rows([stamps[0], stamps[2]], offset), offset, 900, now, 2,
                             session_windows=windows)
    with pytest.raises(ValueError, match='CLOSED_HISTORY_STALE'):
        agent.closed_history(rows(stamps[:2], offset), offset, 900, now, 2,
                             session_windows=windows)


def test_stale_native_clock_is_rejected(tmp_path):
    now = ny('2026-10-22', 10)
    cfg = schedule(tmp_path, now - 16)
    with pytest.raises(ValueError, match='HISTORY_SESSION_CLOCK_UNAVAILABLE'):
        agent.history_session_windows(cfg, 'AAPL.NAS', 10800, now)


def test_production_inspection_always_requires_verified_session_clock(tmp_path, monkeypatch):
    now = ny('2026-10-22', 10)
    cfg = {'bridge_directory': str(tmp_path)}  # No fresh native session file.
    monkeypatch.setattr(agent, 'request', lambda *a: {'symbol': 'AAPL.NAS'})
    monkeypatch.setattr(agent, 'request_live', lambda *a: None)
    monkeypatch.setattr(agent, 'read_json', lambda *a: {
        'contracts': [{'symbol': 'AAPL.NAS', 'server_offset': 10800}]})

    class Broker:
        def verify_demo(self):
            pass

        def call(self, command, *args):
            if command == 'get_workspace_info':
                return {}
            if command == 'get_marketwatch_symbols':
                return {'symbols': [{'symbol': 'AAPL.NAS'}]}
            raise AssertionError('Unverified history must not be requested or published')

    monkeypatch.setattr('dualtrade.mt5_mcp.NativeMT5', Broker)
    with pytest.raises(ValueError, match='HISTORY_SESSION_CLOCK_UNAVAILABLE'):
        agent.inspect(cfg, 'fixture', now)
