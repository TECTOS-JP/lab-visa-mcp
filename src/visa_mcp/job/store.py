"""
Job メタデータの SQLite 永続化 (v0.5.0-rc2, 最小実装)

v0.5.0-rc2 では `jobs` テーブルのみ。完全永続化 (measurement_cache / locks /
monitor_data / audit / job_steps) は v0.7.0 で拡張する。

スキーマ:
  jobs:
    job_id              TEXT PRIMARY KEY
    owner               TEXT
    resource_name       TEXT
    recipe              TEXT      -- recipe 名
    parameters_json     TEXT      -- JSON dump
    status              TEXT      -- JobStatus value
    current_step_index  INTEGER
    error_class         TEXT      -- failed/timeout 時のエラー分類
    last_step_summary   TEXT      -- 直近 step の人間向け 1 行サマリ
    result_json         TEXT      -- 完了/失敗時の steps_executed JSON
    created_at          TEXT      -- ISO 8601
    updated_at          TEXT
"""
from __future__ import annotations
import json
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from visa_mcp.job.state_machine import JobStatus, validate_transition

logger = logging.getLogger(__name__)


def default_store_path() -> Path:
    """環境変数 VISA_MCP_STATE_DB または ~/.visa-mcp/state.sqlite"""
    raw = os.environ.get("VISA_MCP_STATE_DB")
    if raw:
        return Path(raw)
    return Path.home() / ".visa-mcp" / "state.sqlite"


@dataclass
class JobRecord:
    """SQLite 1 行に対応する Job のメタデータ"""
    job_id: str
    owner: str = ""
    resource_name: str = ""
    recipe: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    status: JobStatus = JobStatus.QUEUED
    current_step_index: int = -1
    error_class: str = ""
    last_step_summary: str = ""
    result: dict[str, Any] | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "owner": self.owner,
            "resource_name": self.resource_name,
            "recipe": self.recipe,
            "parameters": self.parameters,
            "status": self.status.value,
            "current_step_index": self.current_step_index,
            "error_class": self.error_class,
            "last_step_summary": self.last_step_summary,
            "result": self.result,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id              TEXT PRIMARY KEY,
    owner               TEXT NOT NULL DEFAULT '',
    resource_name       TEXT NOT NULL DEFAULT '',
    recipe              TEXT NOT NULL DEFAULT '',
    parameters_json     TEXT NOT NULL DEFAULT '{}',
    status              TEXT NOT NULL,
    current_step_index  INTEGER NOT NULL DEFAULT -1,
    error_class         TEXT NOT NULL DEFAULT '',
    last_step_summary   TEXT NOT NULL DEFAULT '',
    result_json         TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_resource ON jobs(resource_name);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobStore:
    """
    Job メタデータの SQLite 永続化 (スレッドセーフ)。

    SQLite 接続は per-thread で持つ。書き込みは Lock で排他化。
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = Path(db_path) if db_path else default_store_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._local = threading.local()
        # 初期化時に schema を作成
        with self._connect() as conn:
            conn.executescript(_SCHEMA_V1)
            conn.commit()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                str(self._db_path),
                isolation_level=None,  # autocommit; we control transactions
                detect_types=sqlite3.PARSE_DECLTYPES,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            self._local.conn = conn
        return conn

    # ---------- create / update / get ----------

    def create_job(
        self,
        job_id: str,
        owner: str,
        resource_name: str,
        recipe: str,
        parameters: dict[str, Any],
    ) -> JobRecord:
        now = _now_iso()
        rec = JobRecord(
            job_id=job_id,
            owner=owner,
            resource_name=resource_name,
            recipe=recipe,
            parameters=parameters,
            status=JobStatus.QUEUED,
            created_at=now,
            updated_at=now,
        )
        with self._write_lock:
            self._connect().execute(
                """
                INSERT INTO jobs
                (job_id, owner, resource_name, recipe, parameters_json,
                 status, current_step_index, error_class, last_step_summary,
                 result_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id, owner, resource_name, recipe,
                    json.dumps(parameters, ensure_ascii=False),
                    rec.status.value, -1, "", "",
                    None, now, now,
                ),
            )
        return rec

    def get(self, job_id: str) -> JobRecord | None:
        row = self._connect().execute(
            "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        return self._row_to_record(row) if row else None

    def list_jobs(
        self,
        status_filter: list[str] | None = None,
        limit: int = 50,
        owner: str | None = None,
    ) -> list[JobRecord]:
        q = "SELECT * FROM jobs"
        clauses: list[str] = []
        params: list[Any] = []
        if status_filter:
            placeholders = ",".join("?" * len(status_filter))
            clauses.append(f"status IN ({placeholders})")
            params.extend(status_filter)
        if owner is not None:
            clauses.append("owner = ?")
            params.append(owner)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        # rowid を二次ソートに使うことで、同秒に複数 INSERT されても安定順序を得る
        q += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        params.append(int(limit))

        rows = self._connect().execute(q, tuple(params)).fetchall()
        return [self._row_to_record(r) for r in rows]

    # ---------- state transition ----------

    def transition_status(
        self,
        job_id: str,
        to_status: JobStatus,
        *,
        current_step_index: int | None = None,
        error_class: str | None = None,
        last_step_summary: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> JobRecord:
        """状態遷移 + 関連フィールドを atomic に更新"""
        with self._write_lock:
            rec = self.get(job_id)
            if rec is None:
                raise KeyError(f"job not found: {job_id}")
            # state machine による遷移検証
            validate_transition(rec.status, to_status)

            now = _now_iso()
            new_step = rec.current_step_index if current_step_index is None else current_step_index
            new_err = rec.error_class if error_class is None else error_class
            new_summary = rec.last_step_summary if last_step_summary is None else last_step_summary
            new_result_json = (
                json.dumps(result, ensure_ascii=False, default=str)
                if result is not None else None
            )

            self._connect().execute(
                """
                UPDATE jobs
                SET status = ?, current_step_index = ?, error_class = ?,
                    last_step_summary = ?, result_json = COALESCE(?, result_json),
                    updated_at = ?
                WHERE job_id = ?
                """,
                (to_status.value, new_step, new_err, new_summary,
                 new_result_json, now, job_id),
            )
            return self.get(job_id)  # type: ignore[return-value]

    def update_step(
        self,
        job_id: str,
        current_step_index: int,
        last_step_summary: str = "",
    ) -> None:
        """状態は変えず、現在ステップだけ更新 (頻繁な呼び出し用)"""
        now = _now_iso()
        with self._write_lock:
            self._connect().execute(
                "UPDATE jobs SET current_step_index = ?, last_step_summary = ?, updated_at = ? "
                "WHERE job_id = ?",
                (current_step_index, last_step_summary, now, job_id),
            )

    # ---------- restart semantics ----------

    def mark_interrupted_on_startup(self) -> int:
        """
        起動時に queued/running/waiting/cancelling だった Job を interrupted に遷移させる。
        返り値: 遷移させた Job 数。

        v0.5.0.2: queued も対象に追加。queued Job がプロセス落ち直前に登録されたケースを救う。
        """
        active = [
            JobStatus.QUEUED.value,
            JobStatus.RUNNING.value,
            JobStatus.WAITING.value,
            JobStatus.CANCELLING.value,
        ]
        now = _now_iso()
        with self._write_lock:
            cur = self._connect().execute(
                f"SELECT COUNT(*) AS n FROM jobs WHERE status IN ({','.join('?' * len(active))})",
                tuple(active),
            ).fetchone()
            count = int(cur["n"]) if cur else 0
            if count > 0:
                self._connect().execute(
                    f"""
                    UPDATE jobs
                    SET status = ?, error_class = 'interrupted',
                        last_step_summary = 'server_restarted', updated_at = ?
                    WHERE status IN ({','.join('?' * len(active))})
                    """,
                    (JobStatus.INTERRUPTED.value, now, *active),
                )
                logger.warning(
                    "起動時に %d 件の実行中 Job を interrupted に遷移させました。", count
                )
            return count

    # ---------- helpers ----------

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            job_id=row["job_id"],
            owner=row["owner"] or "",
            resource_name=row["resource_name"] or "",
            recipe=row["recipe"] or "",
            parameters=json.loads(row["parameters_json"] or "{}"),
            status=JobStatus(row["status"]),
            current_step_index=int(row["current_step_index"]),
            error_class=row["error_class"] or "",
            last_step_summary=row["last_step_summary"] or "",
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
        )

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None
