"""v2.3.4: Codex v2.3.3 レビュー対応テスト.

P1: bind_manually / identify が persist 失敗を session.persisted に
    反映し、discovery tool の response に `persisted` を含める。
P2: clear_session が store 削除失敗 (lock timeout) を store_error で
    返し、clear_persisted_binding tool が success=false にする。
"""
from __future__ import annotations
import contextlib
from unittest.mock import MagicMock

import pytest

from visa_mcp.session_store import SessionStore, SessionStoreLockTimeout
from visa_mcp.session_manager import SessionManager


@pytest.fixture
def fake_registry():
    reg = MagicMock()
    d = MagicMock()
    d.display_name = "Yokogawa 7563"
    d.metadata.manufacturer = "Yokogawa"
    d.metadata.model = "7563"
    reg.get_definition.return_value = d
    reg.match_idn.return_value = None
    return reg


@pytest.fixture
def fake_visa():
    return MagicMock()


# ==============================================================
# P1: persist 結果を session.persisted に反映
# ==============================================================


def test_bind_manually_sets_persisted_true_on_success(
    tmp_path, fake_registry, fake_visa
):
    store = SessionStore(tmp_path / "s.json")
    sm = SessionManager(fake_visa, fake_registry, store=store)
    session = sm.bind_manually("GPIB0::2::INSTR", "Yokogawa", "7563")
    assert session.persisted is True
    assert session.persist_error is None


def test_bind_manually_sets_persisted_false_on_lock_timeout(
    tmp_path, fake_registry, fake_visa, monkeypatch
):
    """upsert が lock timeout で raise したら session.persisted=False
    + persist_error="lock_timeout" になり、bind_manually 自体は
    in-memory session を返す (None ではない)。"""
    store = SessionStore(tmp_path / "s.json")
    sm = SessionManager(fake_visa, fake_registry, store=store)

    import visa_mcp.session_store as mod

    @contextlib.contextmanager
    def fake_lock(*a, **k):
        raise SessionStoreLockTimeout("simulated")
        yield

    monkeypatch.setattr(mod, "_file_lock", fake_lock)
    session = sm.bind_manually("GPIB0::2::INSTR", "Yokogawa", "7563")
    assert session is not None
    assert session.persisted is False
    assert session.persist_error == "lock_timeout"
    # in-memory には居る
    assert sm.get_session("GPIB0::2::INSTR") is not None


def test_session_without_store_persisted_is_none(
    fake_registry, fake_visa
):
    """store 無効環境では persisted=None (in-memory only)。"""
    sm = SessionManager(fake_visa, fake_registry)  # store=None
    session = sm.bind_manually("X", "Yokogawa", "7563")
    assert session.persisted is None


def test_to_dict_does_not_break_with_persist_fields(
    tmp_path, fake_registry, fake_visa
):
    """to_dict() は従来 key を維持しつつ動く (後方互換)。"""
    store = SessionStore(tmp_path / "s.json")
    sm = SessionManager(fake_visa, fake_registry, store=store)
    session = sm.bind_manually("X", "Yokogawa", "7563")
    d = session.to_dict()
    assert "resource_name" in d
    assert "definition_loaded" in d


# ==============================================================
# P2: clear_session の store_error 報告
# ==============================================================


def test_clear_session_reports_store_error_on_lock_timeout(
    tmp_path, fake_registry, fake_visa, monkeypatch
):
    store = SessionStore(tmp_path / "s.json")
    sm = SessionManager(fake_visa, fake_registry, store=store)
    sm.bind_manually("GPIB0::2::INSTR", "Yokogawa", "7563")

    import visa_mcp.session_store as mod

    @contextlib.contextmanager
    def fake_lock(*a, **k):
        raise SessionStoreLockTimeout("simulated")
        yield

    monkeypatch.setattr(mod, "_file_lock", fake_lock)
    out = sm.clear_session("GPIB0::2::INSTR")
    # in-memory は消えた
    assert out["removed_from_in_memory"] is True
    # store 削除は失敗
    assert out["removed_from_store"] is False
    assert out["store_error"] == "lock_timeout"


def test_clear_session_no_error_on_success(
    tmp_path, fake_registry, fake_visa
):
    store = SessionStore(tmp_path / "s.json")
    sm = SessionManager(fake_visa, fake_registry, store=store)
    sm.bind_manually("X", "Yokogawa", "7563")
    out = sm.clear_session("X")
    assert out["store_error"] is None
    assert out["removed_from_store"] is True


def test_v2_3_4_version():
    import visa_mcp
    parts = visa_mcp.__version__.split(".")
    assert tuple(int(p) for p in parts[:3]) >= (2, 3, 4)
