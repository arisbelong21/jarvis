"""Bantuan uji: jalur proyek + mematikan efek samping (audit ke disk)."""
import os
import sys
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def no_audit():
    """Patch core.agent.audit.record supaya uji tidak menulis log ke proyek."""
    return mock.patch("core.agent.audit.record", lambda *a, **k: None)


def no_trust():
    """Trust bawaan mati — uji tidak bergantung pada memory/tool_trust.json."""
    return mock.patch("core.agent.permissions._trust_enabled", lambda: False)
