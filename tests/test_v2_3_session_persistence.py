"""v2.3.0: bindings / identified state 永続化テスト.

シナリオ:
1. SessionStore: file round-trip / atomic write / 不正ファイル耐性
2. SessionManager: bind_manually → store に書く
3. SessionManager: 新インスタンスで store から restore (definition 解決)
4. SessionManager: restore 時に definition 不在 → skip + record 残す
5. clear_session: store からも削除
6. clear_all: store 全消去
"""
from __future__ import annotations
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lab_visa_mcp.session_store import (
    SessionStore, default_session_store_path, SCHEMA_VERSION,
)


# ==============================================================
# SessionStore: file I/O
# ==============================================================


def test_session_store_round_trip(tmp_path: Path):
    """upsert → save → load で同じ data が返る。"""
    p = tmp_path / "sessions.json"
    s = SessionStore(p)
    s.upsert("GPIB0::2::INSTR",
             manufacturer="Yokogawa", model="7563",
             bind_method="manual")
    assert s.get("GPIB0::2::INSTR")["model"] == "7563"

    # 新インスタンスで load
    s2 = SessionStore(p)
    loaded = s2.load()
    assert "GPIB0::2::INSTR" in loaded
    assert loaded["GPIB0::2::INSTR"]["model"] == "7563"
    assert loaded["GPIB0::2::INSTR"]["bind_method"] == "manual"
    # 必須 timestamp field
    assert "bound_at" in loaded["GPIB0::2::INSTR"]
    assert "last_seen_at" in loaded["GPIB0::2::INSTR"]


def test_session_store_atomic_write_uses_tmp(tmp_path: Path):
    """save() は tmpfile + os.replace で書く (中断耐性)。"""
    p = tmp_path / "sessions.json"
    s = SessionStore(p)
    s.upsert("USB0::INSTR", manufacturer="A", model="B",
             bind_method="identify", idn_response="A,B,123,1.0")
    assert p.is_file()
    # tmp file は消えてるはず
    tmp_residue = [f for f in tmp_path.glob(".sessions_*.tmp")]
    assert not tmp_residue, f"tmp residue: {tmp_residue}"


def test_session_store_load_corrupt_file_safe(tmp_path: Path):
    """壊れた JSON でも空 dict として継続する (warning だけ)。"""
    p = tmp_path / "sessions.json"
    p.write_text("{ this is not json", encoding="utf-8")
    s = SessionStore(p)
    loaded = s.load()
    assert loaded == {}


def test_session_store_load_wrong_schema(tmp_path: Path):
    """schema version mismatch でも best-effort で読む (warning)。"""
    p = tmp_path / "sessions.json"
    p.write_text(json.dumps({
        "version": 99,
        "bindings": {
            "X": {"manufacturer": "A", "model": "B", "bind_method": "manual"},
        },
    }), encoding="utf-8")
    s = SessionStore(p)
    loaded = s.load()
    assert "X" in loaded


def test_session_store_load_missing_file_safe(tmp_path: Path):
    """存在しない path でも空 dict / 例外なし。"""
    s = SessionStore(tmp_path / "nonexistent.json")
    assert s.load() == {}


def test_session_store_remove(tmp_path: Path):
    p = tmp_path / "sessions.json"
    s = SessionStore(p)
    s.upsert("X", manufacturer="A", model="B", bind_method="manual")
    assert s.remove("X") is True
    assert s.get("X") is None
    assert s.remove("X") is False  # 二度目は False


def test_session_store_touch_preserves_bound_at(tmp_path: Path):
    """touch() は last_seen_at だけ更新、bound_at は不変。"""
    p = tmp_path / "sessions.json"
    s = SessionStore(p)
    s.upsert("X", manufacturer="A", model="B", bind_method="manual")
    before = s.get("X")
    bound_at_before = before["bound_at"]
    import time; time.sleep(0.01)
    s.touch("X")
    after = s.get("X")
    assert after["bound_at"] == bound_at_before
    assert after["last_seen_at"] >= before["last_seen_at"]


# ==============================================================
# default path: env override
# ==============================================================


def test_default_session_store_path_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VISA_MCP_SESSION_STORE", str(tmp_path / "x.json"))
    assert default_session_store_path() == tmp_path / "x.json"


def test_default_session_store_path_no_env(monkeypatch):
    monkeypatch.delenv("VISA_MCP_SESSION_STORE", raising=False)
    p = default_session_store_path()
    assert p.name == "sessions.json"
    assert ".visa-mcp" in p.parts


# ==============================================================
# SessionManager 連動
# ==============================================================


@pytest.fixture
def fake_registry():
    """get_definition('Yokogawa','7563') → mock definition を返す
    registry。"""
    reg = MagicMock()
    def_mock = MagicMock()
    def_mock.display_name = "Yokogawa 7563"
    reg.get_definition.return_value = def_mock
    reg.match_idn.return_value = None
    return reg


@pytest.fixture
def fake_visa():
    return MagicMock()


def test_bind_manually_persists_to_store(tmp_path, fake_registry, fake_visa):
    from lab_visa_mcp.session_manager import SessionManager
    store = SessionStore(tmp_path / "sessions.json")
    sm = SessionManager(fake_visa, fake_registry, store=store)
    sm.bind_manually("GPIB0::2::INSTR", "Yokogawa", "7563")
    rec = store.get("GPIB0::2::INSTR")
    assert rec is not None
    assert rec["manufacturer"] == "Yokogawa"
    assert rec["model"] == "7563"
    assert rec["bind_method"] == "manual"


def test_session_manager_restores_on_init(tmp_path, fake_registry, fake_visa):
    """1 つ目で bind → store 書き込み → 2 つ目で同じ store を渡すと
    in-memory session が復元される。"""
    from lab_visa_mcp.session_manager import SessionManager
    store_path = tmp_path / "sessions.json"
    store1 = SessionStore(store_path)
    sm1 = SessionManager(fake_visa, fake_registry, store=store1)
    sm1.bind_manually("GPIB0::2::INSTR", "Yokogawa", "7563")
    assert sm1.get_session("GPIB0::2::INSTR") is not None

    # 別インスタンス: in-memory は空のはず、store から restore
    store2 = SessionStore(store_path)
    sm2 = SessionManager(fake_visa, fake_registry, store=store2)
    restored = sm2.get_session("GPIB0::2::INSTR")
    assert restored is not None, "v2.3.0: restore されるべき"
    assert restored.definition is not None
    assert restored.idn_parsed["model"] == "7563"


def test_restore_skips_missing_definition(tmp_path, fake_visa):
    """registry に definition が無い record は skip するが store からは
    消さない (後で registry が更新されたら restore できる)。"""
    from lab_visa_mcp.session_manager import SessionManager
    store_path = tmp_path / "sessions.json"
    store = SessionStore(store_path)
    store.upsert("GPIB0::99::INSTR",
                 manufacturer="Unknown", model="ZZZ",
                 bind_method="manual")

    reg = MagicMock()
    reg.get_definition.return_value = None  # 未知機器
    sm = SessionManager(fake_visa, reg, store=store)
    assert sm.get_session("GPIB0::99::INSTR") is None
    # store からは消えていない
    s2 = SessionStore(store_path)
    assert s2.load().get("GPIB0::99::INSTR") is not None


def test_clear_session_removes_from_store(tmp_path, fake_registry, fake_visa):
    from lab_visa_mcp.session_manager import SessionManager
    store = SessionStore(tmp_path / "sessions.json")
    sm = SessionManager(fake_visa, fake_registry, store=store)
    sm.bind_manually("X", "Yokogawa", "7563")
    assert store.get("X") is not None
    sm.clear_session("X")
    assert store.get("X") is None
    assert sm.get_session("X") is None


def test_clear_all_clears_store(tmp_path, fake_registry, fake_visa):
    from lab_visa_mcp.session_manager import SessionManager
    store = SessionStore(tmp_path / "sessions.json")
    sm = SessionManager(fake_visa, fake_registry, store=store)
    sm.bind_manually("A", "Yokogawa", "7563")
    sm.bind_manually("B", "Yokogawa", "7563")
    sm.clear_all()
    assert store.list_all() == {}


def test_session_manager_without_store_works(fake_registry, fake_visa):
    """後方互換: store=None でも従来通り動く。"""
    from lab_visa_mcp.session_manager import SessionManager
    sm = SessionManager(fake_visa, fake_registry)  # 暗黙 store=None
    sm.bind_manually("X", "Yokogawa", "7563")
    assert sm.get_session("X") is not None
    # 例外なく完了する


# ==============================================================
# version sentinel
# ==============================================================


def test_v2_3_0_version():
    import lab_visa_mcp
    parts = lab_visa_mcp.__version__.split(".")
    assert tuple(int(p) for p in parts[:3]) >= (2, 3, 0)


def test_schema_version_constant():
    assert SCHEMA_VERSION == 1
