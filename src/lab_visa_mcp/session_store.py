"""v2.3.0: bindings / identified state の永続化 (process 再起動耐性).

長時間運用 / クラッシュ復旧 / 複数エージェント運用で
`bind_definition` / `identify_instrument` の結果を毎回やり直す
コストが問題になっていた。特に *IDN? 非対応機器
(例: Yokogawa 7563) は手動 bind がほぼ毎回必要だった。

このモジュールは:
- JSON ファイル (`~/.visa-mcp/sessions.json` または
  `$VISA_MCP_SESSION_STORE`) に bindings を保存
- SessionManager 起動時に auto-restore (definition を YAML registry
  から再 lookup; 見つからなければ warning して skip)
- bind / identify / clear のたびに persist

JSON schema (v1):

    {
      "version": 1,
      "bindings": {
        "GPIB0::2::INSTR": {
          "manufacturer": "Yokogawa",
          "model": "7563",
          "bind_method": "manual",         // "manual" | "identify"
          "idn_response": "",              // identify 時のみ
          "bound_at": "2026-05-29T12:34:56+00:00",
          "last_seen_at": "2026-05-29T12:34:56+00:00"
        }
      }
    }

API 設計指針:
- store=None でも SessionManager は従来通り動く (in-memory)
- 復元時に definition 解決失敗しても record 自体は残し、後で
  registry が更新されたら正常に解決できるようにする
- file 書き込み失敗は warn して落ちない (運用継続性優先)
"""
from __future__ import annotations
import contextlib
import json
import logging
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
DEFAULT_PATH_REL = ".visa-mcp/sessions.json"

# v2.3.2: cross-platform file lock (multi-process safety)。
# Codex v2.3.1 レビュー P2 への対応。
if sys.platform == "win32":
    try:
        import msvcrt
        _HAS_MSVCRT = True
    except ImportError:
        _HAS_MSVCRT = False
    _HAS_FCNTL = False
else:
    try:
        import fcntl
        _HAS_FCNTL = True
    except ImportError:
        _HAS_FCNTL = False
    _HAS_MSVCRT = False


class SessionStoreLockTimeout(RuntimeError):
    """v2.3.3: file lock acquisition timed out.

    Codex v2.3.2 レビュー P1: 旧実装は timeout 後も warning だけ出して
    write を続行していたため multi-process safety が壊れていた。
    v2.3.3 では明示例外を上げ、mutating ops 側で write をスキップする
    (in-memory のみ更新 / log warning) ように変更。
    """


@contextlib.contextmanager
def _file_lock(lock_path: Path, timeout_s: float = 5.0):
    """exclusive advisory lock on `lock_path`. Cross-process safe.

    Windows: msvcrt.locking on a sidecar `.lock` file (LK_NBLCK +
             busy-wait retry until timeout).
    POSIX:   fcntl.flock with LOCK_EX | LOCK_NB + busy-wait retry.
    fallback: lock 機構が無い環境では yield する (in-process thread
              lock のみで best-effort)。

    v2.3.3: timeout 時は `SessionStoreLockTimeout` を raise する。
    `_HAS_MSVCRT` / `_HAS_FCNTL` どちらも無い環境では timeout 判定を
    行わず、そのまま yield する (in-process lock のみ)。
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s
    fh = None
    acquired = False
    try:
        fh = open(lock_path, "ab+")
        if _HAS_MSVCRT:
            while True:
                try:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise SessionStoreLockTimeout(
                            f"session store lock timeout ({timeout_s:.1f}s): "
                            f"{lock_path}")
                    time.sleep(0.05)
        elif _HAS_FCNTL:
            while True:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except (BlockingIOError, OSError):
                    if time.monotonic() >= deadline:
                        raise SessionStoreLockTimeout(
                            f"session store lock timeout ({timeout_s:.1f}s): "
                            f"{lock_path}")
                    time.sleep(0.05)
        # else: no cross-process lock available; proceed with in-process
        # lock only (acquired=False)
        yield
    finally:
        if fh is not None:
            try:
                if acquired:
                    if _HAS_MSVCRT:
                        fh.seek(0)
                        try:
                            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                        except OSError:
                            pass
                    elif _HAS_FCNTL:
                        try:
                            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                        except Exception:
                            pass
            finally:
                try:
                    fh.close()
                except Exception:
                    pass


def default_session_store_path() -> Path:
    """env override or ~/.visa-mcp/sessions.json"""
    raw = os.environ.get("VISA_MCP_SESSION_STORE", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / DEFAULT_PATH_REL


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    """JSON file に bindings を永続化するシンプルな key-value store.

    Thread/process safety: 単一 process 内で SessionManager から
    sequential に使われる前提。multi-process 排他は将来課題
    (lockfile or SQLite に置き換え)。
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else default_session_store_path()
        self._bindings: dict[str, dict[str, Any]] = {}
        # v2.3.2: in-process thread lock + cross-process file lock
        # (multi-agent / multi-server で同じ sessions.json を更新する
        # ケースでも lost update を防ぐ。Codex v2.3.1 レビュー P2)。
        self._thread_lock = threading.RLock()
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    # ---------- file I/O ----------

    def load(self) -> dict[str, dict[str, Any]]:
        """Load from disk. 不正・欠損ファイルは空辞書扱い (warning)."""
        if not self.path.is_file():
            self._bindings = {}
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(
                "session store の読み込み失敗 (path=%s): %s。"
                "空の bindings として継続します", self.path, e)
            self._bindings = {}
            return {}
        if not isinstance(data, dict):
            logger.warning(
                "session store の形式が不正 (path=%s, 型=%s)。"
                "空扱いで継続", self.path, type(data).__name__)
            self._bindings = {}
            return {}
        version = data.get("version")
        if version != SCHEMA_VERSION:
            logger.warning(
                "session store の schema version=%r (期待 %d)。"
                "互換 best-effort で読み込みます", version, SCHEMA_VERSION)
        bindings = data.get("bindings") or {}
        if not isinstance(bindings, dict):
            logger.warning(
                "session store の bindings が dict でない: %r。"
                "空扱いで継続", type(bindings).__name__)
            self._bindings = {}
            return {}
        # 値の型を最低限 sanity check (str-key, dict-value)
        cleaned: dict[str, dict[str, Any]] = {}
        for k, v in bindings.items():
            if not isinstance(k, str) or not isinstance(v, dict):
                continue
            cleaned[k] = v
        self._bindings = cleaned
        return dict(self._bindings)

    def save(self) -> bool:
        """Atomic write (tmpfile + replace).

        v2.3.4: lock timeout は SessionStoreLockTimeout で伝播。
        IO エラーのみ catch して False を返す。
        """
        with self._thread_lock, _file_lock(self._lock_path):
            try:
                self._save_locked()
                return True
            except Exception as e:
                logger.warning(
                    "session store の保存失敗 (path=%s): %s。"
                    "in-memory のみで継続", self.path, e)
                return False

    # ---------- mutating ops ----------

    def _reload_from_disk_locked(self) -> None:
        """ロック内で disk から最新の bindings を再読み込みする。
        他 process が書いた変更を取り込んでから書き戻すことで lost
        update を防ぐ。失敗時は in-memory bindings をそのまま使う
        (best-effort)。"""
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                bindings = data.get("bindings") or {}
                if isinstance(bindings, dict):
                    cleaned = {
                        k: v for k, v in bindings.items()
                        if isinstance(k, str) and isinstance(v, dict)
                    }
                    self._bindings = cleaned
        except Exception as e:
            logger.warning(
                "lock 中の再読み込み失敗 (path=%s): %s。"
                "in-memory bindings を維持", self.path, e)

    def upsert(
        self, resource: str, *,
        manufacturer: str, model: str,
        bind_method: str,
        idn_response: str = "",
        bound_at: str | None = None,
    ) -> bool:
        """add or update. 既存 record があれば bound_at は保持。

        v2.3.4: lock 取得失敗時は `SessionStoreLockTimeout` を上位に
        伝播させる (Codex v2.3.3 レビュー P1)。これにより
        SessionManager.bind_manually / identify が `persist_error` を
        正確に検出できる。成功時は `True` を返す
        (将来 partial failure を区別する余地のための bool 維持)。
        """
        with self._thread_lock, _file_lock(self._lock_path):
            self._reload_from_disk_locked()
            existing = self._bindings.get(resource) or {}
            rec = {
                "manufacturer": manufacturer,
                "model": model,
                "bind_method": bind_method,
                "idn_response": idn_response,
                "bound_at": existing.get("bound_at") or bound_at or _now_iso(),
                "last_seen_at": _now_iso(),
            }
            self._bindings[resource] = rec
            self._save_locked()
            return True

    def touch(self, resource: str) -> bool:
        with self._thread_lock, _file_lock(self._lock_path):
            self._reload_from_disk_locked()
            if resource not in self._bindings:
                return False
            self._bindings[resource]["last_seen_at"] = _now_iso()
            self._save_locked()
            return True

    def remove(self, resource: str) -> bool:
        """resource を削除し、disk から実際に削除できたら True を返す。

        v2.3.4: lock 取得失敗時は `SessionStoreLockTimeout` を伝播
        させる (Codex v2.3.3 レビュー P2)。これで「もともと無かった
        (False)」と「lock 失敗 (例外)」を呼び出し側で区別できる。
        """
        with self._thread_lock, _file_lock(self._lock_path):
            self._reload_from_disk_locked()
            if resource in self._bindings:
                del self._bindings[resource]
                self._save_locked()
                return True
            return False

    def clear_all(self) -> bool:
        with self._thread_lock, _file_lock(self._lock_path):
            self._bindings = {}
            self._save_locked()
            return True

    def _save_locked(self) -> None:
        """ロックを既に取得している前提の save 実装 (内部)。"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": SCHEMA_VERSION,
                "bindings": self._bindings,
            }
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8",
                dir=str(self.path.parent),
                prefix=".sessions_", suffix=".tmp", delete=False,
            ) as tf:
                json.dump(payload, tf, ensure_ascii=False, indent=2)
                tmp_path = Path(tf.name)
            os.replace(tmp_path, self.path)
        except Exception as e:
            logger.warning(
                "session store の保存失敗 (path=%s): %s", self.path, e)

    # ---------- read ops ----------

    def get(self, resource: str) -> dict[str, Any] | None:
        rec = self._bindings.get(resource)
        return dict(rec) if rec else None

    def list_all(self) -> dict[str, dict[str, Any]]:
        return {k: dict(v) for k, v in self._bindings.items()}
