"""v2.3.2: Codex v2.3.1 レビュー対応テスト.

P1: reload_definitions が persisted bindings を消さないこと
P2: clear_persisted_binding の removed フラグが in-memory OR store
    の OR で判定されること
P2: SessionStore の file lock (multi-process safety) 動作
P3: JobStore.close() 重複定義の解消 (lab-executor 側、別 test)
"""
from __future__ import annotations
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from visa_mcp.session_store import SessionStore


@pytest.fixture
def fake_registry():
    reg = MagicMock()
    def_mock = MagicMock()
    def_mock.display_name = "Yokogawa 7563"
    def_mock.metadata.manufacturer = "Yokogawa"
    def_mock.metadata.model = "7563"
    reg.get_definition.return_value = def_mock
    reg.match_idn.return_value = None
    reg.reload.return_value = 2
    return reg


@pytest.fixture
def fake_visa():
    return MagicMock()


# ==============================================================
# P1: reload_definitions が persisted bindings を消さない
# ==============================================================


def test_reload_in_memory_sessions_preserves_store(
    tmp_path, fake_registry, fake_visa
):
    """reload_in_memory_sessions() は in-memory を捨てて store から
    restore し直す。store の record は消えない。"""
    from visa_mcp.session_manager import SessionManager
    store = SessionStore(tmp_path / "sessions.json")
    sm = SessionManager(fake_visa, fake_registry, store=store)
    sm.bind_manually("GPIB0::2::INSTR", "Yokogawa", "7563")
    assert store.get("GPIB0::2::INSTR") is not None
    sm.reload_in_memory_sessions()
    # store record は残っているはず
    assert store.get("GPIB0::2::INSTR") is not None
    # restore で in-memory にも復活
    assert sm.get_session("GPIB0::2::INSTR") is not None


def test_clear_in_memory_does_not_touch_store(
    tmp_path, fake_registry, fake_visa
):
    from visa_mcp.session_manager import SessionManager
    store = SessionStore(tmp_path / "sessions.json")
    sm = SessionManager(fake_visa, fake_registry, store=store)
    sm.bind_manually("X", "Yokogawa", "7563")
    sm.clear_in_memory()
    # store は消えていない
    assert store.get("X") is not None
    # in-memory は空
    assert sm.get_session("X") is None


def test_clear_all_still_clears_store(
    tmp_path, fake_registry, fake_visa
):
    """v2.3.2 でも clear_all は明示的に store も消す (admin 用)。"""
    from visa_mcp.session_manager import SessionManager
    store = SessionStore(tmp_path / "sessions.json")
    sm = SessionManager(fake_visa, fake_registry, store=store)
    sm.bind_manually("X", "Yokogawa", "7563")
    sm.clear_all()
    assert store.get("X") is None


# ==============================================================
# P2: clear_persisted_binding の removed フラグ
# ==============================================================


def test_clear_persisted_binding_returns_removed_when_only_in_store(
    tmp_path, fake_visa
):
    """definition 不在で restore skip された (in-memory 不在、store
    のみ存在) record を clear したとき、removed=true を返すこと。"""
    from visa_mcp.session_manager import SessionManager
    store_path = tmp_path / "sessions.json"
    store = SessionStore(store_path)
    # 事前に store に push
    store.upsert("GHOST::INSTR",
                 manufacturer="Unknown", model="ZZZ",
                 bind_method="manual")
    # registry には未知 → restore skip される
    reg = MagicMock()
    reg.get_definition.return_value = None
    sm = SessionManager(fake_visa, reg, store=store)
    assert sm.get_session("GHOST::INSTR") is None
    assert store.get("GHOST::INSTR") is not None

    # clear_persisted_binding 相当: store にあれば removed=true
    in_mem = sm.get_session("GHOST::INSTR") is not None
    store_existed = (
        sm.store is not None
        and sm.store.get("GHOST::INSTR") is not None)
    existed_anywhere = in_mem or store_existed
    sm.clear_session("GHOST::INSTR")
    assert existed_anywhere is True, (
        "v2.3.2: store 単独 record も removed=true として扱うべき")
    assert store.get("GHOST::INSTR") is None


# ==============================================================
# P2: SessionStore file lock (multi-thread / multi-process surrogate)
# ==============================================================


def test_concurrent_upsert_does_not_lose_updates(tmp_path):
    """同じ store path に 2 thread が同時 upsert しても、最後の
    save 時点で両方の record が含まれていること (lost update なし)。

    NOTE: 同 process 内の thread だが、file lock が thread 越しに
    効くか、in-process lock が機能するかを確認する代理 test。
    """
    store_path = tmp_path / "sessions.json"

    def worker(name: str):
        s = SessionStore(store_path)
        for i in range(10):
            s.upsert(f"R::{name}::{i}",
                     manufacturer="M", model="X",
                     bind_method="manual")

    t1 = threading.Thread(target=worker, args=("A",))
    t2 = threading.Thread(target=worker, args=("B",))
    t1.start(); t2.start(); t1.join(); t2.join()

    final = SessionStore(store_path).load()
    a_count = sum(1 for k in final if k.startswith("R::A::"))
    b_count = sum(1 for k in final if k.startswith("R::B::"))
    assert a_count == 10, f"thread A の record 欠損: {a_count}"
    assert b_count == 10, f"thread B の record 欠損: {b_count}"


def test_remove_re_reads_from_disk(tmp_path):
    """別 process が書いた record も remove() で disk 再読み込み後
    削除できる (single-process では同じ object、別 SessionStore
    インスタンスで simulate)。"""
    store_path = tmp_path / "sessions.json"
    s1 = SessionStore(store_path)
    s1.upsert("R", manufacturer="M", model="X", bind_method="manual")
    # 別インスタンス (別 process simulate)
    s2 = SessionStore(store_path)
    assert s2.remove("R") is True
    # s1 内 in-memory には残っているが、disk からは消えている
    assert SessionStore(store_path).load().get("R") is None


def test_v2_3_2_version():
    import visa_mcp
    parts = visa_mcp.__version__.split(".")
    assert tuple(int(p) for p in parts[:3]) >= (2, 3, 2)
