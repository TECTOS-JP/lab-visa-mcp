"""v2.3.3: Codex v2.3.2 レビュー対応テスト.

P1: file lock timeout 時は SessionStoreLockTimeout を上げ、
    mutating ops は write をスキップして False を返すこと
P2: clear_persisted_binding の removed_from_store は
    store.remove() の戻り値ベースであること
"""
from __future__ import annotations
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lab_visa_mcp.session_store import (
    SessionStore, SessionStoreLockTimeout, _file_lock,
)


# ==============================================================
# P1: lock timeout → SessionStoreLockTimeout, mutating ops False
# ==============================================================


def test_file_lock_raises_timeout_when_busy(tmp_path):
    """既に lock 取得済みの状態で別 thread が短い timeout で待つと
    SessionStoreLockTimeout が上がること。"""
    lock_path = tmp_path / "s.json.lock"

    holder_ready = threading.Event()
    release_holder = threading.Event()

    def holder():
        with _file_lock(lock_path, timeout_s=5.0):
            holder_ready.set()
            release_holder.wait(timeout=10.0)

    t = threading.Thread(target=holder)
    t.start()
    try:
        holder_ready.wait(timeout=5.0)
        # 別の取得試行は短い timeout で必ず失敗する
        with pytest.raises(SessionStoreLockTimeout):
            with _file_lock(lock_path, timeout_s=0.2):
                pass
    finally:
        release_holder.set()
        t.join(timeout=5.0)


def test_upsert_raises_on_lock_timeout(tmp_path, monkeypatch):
    """v2.3.4: upsert は lock 取れないと SessionStoreLockTimeout を
    raise する (v2.3.3 の bool 戻り値 → 例外伝播へ変更)。
    """
    s = SessionStore(tmp_path / "s.json")
    import lab_visa_mcp.session_store as mod
    import contextlib

    @contextlib.contextmanager
    def fake_lock(*args, **kwargs):
        raise SessionStoreLockTimeout("simulated timeout")
        yield

    monkeypatch.setattr(mod, "_file_lock", fake_lock)
    with pytest.raises(SessionStoreLockTimeout):
        s.upsert("R", manufacturer="M", model="X", bind_method="manual")
    # in-memory も disk も変化なし
    assert s.get("R") is None
    assert not (tmp_path / "s.json").exists()


def test_remove_raises_on_lock_timeout(tmp_path, monkeypatch):
    """v2.3.4: remove も lock timeout で raise。"""
    s = SessionStore(tmp_path / "s.json")
    s.upsert("R", manufacturer="M", model="X", bind_method="manual")
    import lab_visa_mcp.session_store as mod
    import contextlib

    @contextlib.contextmanager
    def fake_lock(*args, **kwargs):
        raise SessionStoreLockTimeout("simulated timeout")
        yield

    monkeypatch.setattr(mod, "_file_lock", fake_lock)
    with pytest.raises(SessionStoreLockTimeout):
        s.remove("R")
    # disk には残っている
    assert "R" in SessionStore(tmp_path / "s.json").load()


# ==============================================================
# P2: removed_from_store は store.remove() の戻り値ベース
# ==============================================================


@pytest.fixture
def fake_registry():
    reg = MagicMock()
    def_mock = MagicMock()
    def_mock.metadata.manufacturer = "Yokogawa"
    def_mock.metadata.model = "7563"
    reg.get_definition.return_value = def_mock
    reg.match_idn.return_value = None
    return reg


@pytest.fixture
def fake_visa():
    return MagicMock()


def test_clear_session_returns_store_remove_result(
    tmp_path, fake_registry, fake_visa
):
    """clear_session の戻り値の removed_from_store は store.remove()
    の戻り値そのまま (disk 再読込後の実際の削除結果)。"""
    from lab_visa_mcp.session_manager import SessionManager
    store_path = tmp_path / "s.json"

    # SessionManager 経由で 1 件 bind してから、別 process simulate で
    # 同じ store に追加 record を書く
    store_a = SessionStore(store_path)
    sm = SessionManager(fake_visa, fake_registry, store=store_a)
    sm.bind_manually("GPIB0::2::INSTR", "Yokogawa", "7563")

    # 別 process simulate: 別 SessionStore インスタンスから書く
    store_b = SessionStore(store_path)
    store_b.upsert("GPIB0::3::INSTR",
                   manufacturer="Yokogawa", model="7563",
                   bind_method="manual")

    # in-memory には GPIB0::3::INSTR は無い (別 process 経由なので)
    assert sm.get_session("GPIB0::3::INSTR") is None
    # この時点で store_a.get() は古い in-memory cache を見るので
    # GPIB0::3 を知らない (P2 の根本問題)
    assert store_a.get("GPIB0::3::INSTR") is None

    # clear_session を呼ぶと store.remove() が disk 再読込してから
    # 削除する。removed_from_store は True を返すべき。
    outcome = sm.clear_session("GPIB0::3::INSTR")
    assert outcome["removed_from_in_memory"] is False
    assert outcome["removed_from_store"] is True, (
        "v2.3.3: store.remove() の戻り値ベースで判定すべき "
        f"(別 process が追加した record): {outcome}")
    # 実際に disk からは消えている
    assert SessionStore(store_path).load().get("GPIB0::3::INSTR") is None


def test_clear_session_dict_shape(
    tmp_path, fake_registry, fake_visa
):
    """clear_session の戻り値が辞書型で必須キーを持つこと。"""
    from lab_visa_mcp.session_manager import SessionManager
    store = SessionStore(tmp_path / "s.json")
    sm = SessionManager(fake_visa, fake_registry, store=store)
    out = sm.clear_session("nonexistent")
    assert isinstance(out, dict)
    assert "removed_from_in_memory" in out
    assert "removed_from_store" in out
    assert out["removed_from_in_memory"] is False
    assert out["removed_from_store"] is False


def test_v2_3_3_version():
    import lab_visa_mcp
    parts = lab_visa_mcp.__version__.split(".")
    assert tuple(int(p) for p in parts[:3]) >= (2, 3, 3)
