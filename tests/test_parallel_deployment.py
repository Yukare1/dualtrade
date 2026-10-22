"""Verify process ownership boundaries without touching real workers."""
import runpy
from types import SimpleNamespace

import pytest

from dualtrade.config import ROOT


def test_windows_venv_launcher_and_child_are_one_collector(monkeypatch):
    script = runpy.run_path(str(ROOT/'scripts/deploy-parallel-observation.py'))
    stopped = []
    parent = SimpleNamespace(pid=10, ppid=lambda: 1, terminate=lambda: stopped.append(10))
    child = SimpleNamespace(pid=11, ppid=lambda: 10, terminate=lambda: stopped.append(11))
    monkeypatch.setattr('psutil.wait_procs', lambda procs, timeout: (procs, []))
    script['stop_collector_family']([parent, child])
    assert stopped == [11, 10]


def test_independent_collectors_are_not_terminated(monkeypatch):
    script = runpy.run_path(str(ROOT/'scripts/deploy-parallel-observation.py'))
    stopped = []
    first = SimpleNamespace(pid=10, ppid=lambda: 1, terminate=lambda: stopped.append(10))
    second = SimpleNamespace(pid=11, ppid=lambda: 2, terminate=lambda: stopped.append(11))
    with pytest.raises(RuntimeError, match='Multiple independent'):
        script['stop_collector_family']([first, second])
    assert stopped == []
