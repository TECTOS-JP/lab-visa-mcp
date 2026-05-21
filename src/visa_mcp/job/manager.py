"""
Job Manager + Executor (v0.5.0)

- JobManager: recipe を非同期 Job として登録・追跡・キャンセル
- バックグラウンドは asyncio.create_task で実行
- 状態は SQLite (JobStore) に同期
- cancel_mode は immediate / after_current_step / safe_shutdown の 3 種類
- recipe Step 単位で cancel チェック (各 step 開始前に確認)
- WaitStep 実行中は短いインターバルで cancel チェックして即時停止可能
- job_timeout_s で TIMEOUT 自動遷移 (v0.5.0 追加)
"""
from __future__ import annotations
import asyncio
import logging
import time
import uuid
from typing import Any

from visa_mcp.experiment_ir import CommandStep, Plan, WaitStep
from visa_mcp.job.state_machine import (
    CancelMode,
    JobStatus,
    is_terminal,
)
from visa_mcp.job.store import JobStore, JobRecord
from visa_mcp.job.scheduler import (
    ResourceScheduler,
    ResourceBusyError,
    QueuePolicy,
)
from visa_mcp.recipe_executor import recipe_to_plan
from visa_mcp.step_executor import execute_command_step, execute_wait_step
from visa_mcp.session_manager import SessionManager
from visa_mcp.visa_manager import VisaManager

logger = logging.getLogger(__name__)


# wait step を細かいスライスに分割して cancel/timeout に即応するためのインターバル
_WAIT_SLICE_S = 0.2

# job_timeout_s デフォルト (24時間)
DEFAULT_JOB_TIMEOUT_S: float = 86400.0


class JobNotFoundError(Exception):
    pass


class JobAlreadyTerminalError(Exception):
    pass


class _JobRuntime:
    """asyncio.Task と cancel 要求フラグ / 期限の組"""

    def __init__(self, task: asyncio.Task, deadline: float | None) -> None:
        self.task = task
        self.cancel_mode: CancelMode | None = None  # 設定されたら cancel 要求中
        self.deadline = deadline                     # time.monotonic() 基準。None なら無期限
        # v0.5.0.3: queue 待ちから起動可能になった通知用。
        # 旧コード (v0.5.0.2) は _wait_until_scheduled で遅延生成していたが、
        # start_recipe_job 直後・task 実行前に on_terminal 経由の _wake_queued_job が
        # 走ると runtime._start_event が None で wake が失われる lost-wake-up が
        # 発生していた。eager 生成で解消する。
        # immediate=True の Job では event.set() しても誰も待たないので影響なし。
        self._start_event: asyncio.Event = asyncio.Event()

    def is_timed_out(self) -> bool:
        return self.deadline is not None and time.monotonic() >= self.deadline

    def remaining_s(self) -> float | None:
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - time.monotonic())


class JobManager:
    """
    バックグラウンド Job の管理。

    使い方:
        manager = JobManager(visa, session_mgr, store)
        job_id = await manager.start_recipe_job("GPIB0::1::INSTR", "safe_output_on", {"target_v": 5})
        await manager.cancel(job_id, CancelMode.SAFE_SHUTDOWN)
    """

    def __init__(
        self,
        visa: VisaManager,
        session_mgr: SessionManager,
        store: JobStore | None = None,
        scheduler: ResourceScheduler | None = None,
    ) -> None:
        self._visa = visa
        self._sessions = session_mgr
        self._store = store or JobStore()
        self._runtimes: dict[str, _JobRuntime] = {}
        # v0.5.0.2: Job 単位排他のための ResourceScheduler
        self._scheduler = scheduler or ResourceScheduler()
        # 起動時に running/waiting/cancelling/queued を interrupted に遷移
        self._store.mark_interrupted_on_startup()

    @property
    def store(self) -> JobStore:
        return self._store

    @property
    def scheduler(self) -> ResourceScheduler:
        return self._scheduler

    # ---------- public API ----------

    async def start_recipe_job(
        self,
        resource_name: str,
        recipe_name: str,
        parameters: dict[str, Any] | None,
        *,
        owner: str = "",
        override_safety: bool = False,
        override_reason: str = "",
        job_timeout_s: float | None = None,
        queue_policy: QueuePolicy = "queue",
    ) -> JobRecord:
        """
        recipe を Job として登録し、scheduler 経由で bg 実行を開始する。

        v0.5.0.2 変更: 同一 resource への Job が既に running の場合、
        新しい Job は **queued** 状態で待機する (queue_policy="queue" デフォルト)。
        queue_policy="reject_if_busy" を指定すれば busy 時に即 failed を返す。

        起動失敗 (定義なし / 必須パラメータ欠落) は SQLite 上で failed として記録した上で返す。

        job_timeout_s: 全体の実行制限秒数 (None なら DEFAULT_JOB_TIMEOUT_S = 24h)。
                       経過すると Job は自動で TIMEOUT 状態に遷移する。
                       wait 中も含む全実行時間が対象。
        queue_policy: "queue" (デフォルト、busy 時は queued で順番待ち) /
                      "reject_if_busy" (busy 時は failed)
        """
        parameters = parameters or {}
        session = self._sessions.get_session(resource_name)
        if session is None or session.definition is None:
            return self._record_immediate_failure(
                resource_name, recipe_name, parameters,
                error_class="not_found",
                summary=f"{resource_name} は未識別、または YAML 定義がありません。",
            )
        recipe = session.definition.recipes.get(recipe_name)
        if recipe is None:
            return self._record_immediate_failure(
                resource_name, recipe_name, parameters,
                error_class="not_found",
                summary=f"recipe '{recipe_name}' は定義されていません",
            )
        # 必須パラメータチェック
        for p in recipe.parameters:
            if p.required and p.name not in parameters and p.default is None:
                return self._record_immediate_failure(
                    resource_name, recipe_name, parameters,
                    error_class="validation",
                    summary=f"必須パラメータ '{p.name}' が指定されていません",
                )

        # JOB 登録 (queued 状態)
        job_id = self._new_job_id()
        rec = self._store.create_job(
            job_id=job_id,
            owner=owner,
            resource_name=resource_name,
            recipe=recipe_name,
            parameters=parameters,
        )

        # 内部表現: 将来の Group/Map で複数 resource に拡張可能なよう list 化
        required_resources = [resource_name]

        # scheduler に投入
        try:
            immediate, blocking = await self._scheduler.enqueue(
                job_id, required_resources, queue_policy=queue_policy,
            )
        except ResourceBusyError as e:
            return self._store.transition_status(
                job_id, JobStatus.FAILED,
                error_class="blocked",
                last_step_summary=f"resource busy (blocked by {e.blocking_job_id})",
                result={
                    "success": False,
                    "error": "ResourceBusy",
                    "message": str(e),
                    "blocking_job_id": e.blocking_job_id,
                    "queue_policy": queue_policy,
                },
            )

        # タイムアウト計算 (queued 期間も含む = ユーザー視点での "始めてから")
        effective_timeout = (
            DEFAULT_JOB_TIMEOUT_S if job_timeout_s is None else float(job_timeout_s)
        )
        deadline = time.monotonic() + effective_timeout if effective_timeout > 0 else None

        # バックグラウンドタスクとして起動 (queue 待ちから running まで _run_job 内で管理)
        task = asyncio.create_task(
            self._run_job(
                rec,
                required_resources=required_resources,
                override_safety=override_safety,
                override_reason=override_reason,
                start_immediately=immediate,
            ),
            name=f"job-{job_id}",
        )
        self._runtimes[job_id] = _JobRuntime(task, deadline)

        if not immediate:
            # queue 待ち情報を last_step_summary に
            self._store.update_step(
                job_id, -1,
                last_step_summary=f"queued, blocked_by={blocking}",
            )

        return rec

    def get(self, job_id: str) -> JobRecord:
        rec = self._store.get(job_id)
        if rec is None:
            raise JobNotFoundError(f"job not found: {job_id}")
        return rec

    def list_jobs(
        self,
        status_filter: list[str] | None = None,
        limit: int = 50,
        owner: str | None = None,
    ) -> list[JobRecord]:
        return self._store.list_jobs(status_filter, limit, owner)

    async def cancel(
        self,
        job_id: str,
        cancel_mode: CancelMode = CancelMode.AFTER_CURRENT_STEP,
        timeout_s: float | None = 30.0,
    ) -> JobRecord:
        """
        Job のキャンセルを要求し、終端状態に遷移するまで待機する。

        immediate           : asyncio.Task をキャンセル (asyncio.CancelledError)
        after_current_step  : 次の step 開始前にキャンセル
        safe_shutdown       : YAML safe_shutdown を実行してからキャンセル

        v0.5.0.2: queued 状態の Job は scheduler から取り除き、直接 cancelled へ遷移。
        """
        rec = self.get(job_id)
        if is_terminal(rec.status):
            return rec

        runtime = self._runtimes.get(job_id)
        if runtime is None:
            # ランタイムが消失 (再起動後等) → interrupted として返す
            return self._store.transition_status(
                job_id, JobStatus.INTERRUPTED,
                error_class="interrupted",
                last_step_summary="runtime missing",
            )

        runtime.cancel_mode = cancel_mode

        # v0.5.0.2: queued の場合は scheduler から取り除き、直接 cancelled へ遷移
        if rec.status == JobStatus.QUEUED:
            await self._scheduler.cancel_queued(job_id)
            # 待機中の _wait_until_scheduled を抜けさせる (v0.5.0.3: event は常に存在)
            runtime._start_event.set()
            try:
                self._store.transition_status(
                    job_id, JobStatus.CANCELLED,
                    error_class="cancelled",
                    last_step_summary=f"cancelled from queued ({cancel_mode.value})",
                    result={
                        "success": False, "recipe": rec.recipe,
                        "cancelled": True, "cancel_mode": cancel_mode.value,
                    },
                )
            except Exception:
                pass
            # Task の cleanup (scheduler.on_terminal + _runtimes.pop が finally で走る)
            runtime.task.cancel()
        else:
            # 通常 (running/waiting) のキャンセル経路
            # cancelling 状態に遷移
            try:
                self._store.transition_status(
                    job_id, JobStatus.CANCELLING,
                    last_step_summary=f"cancel_mode={cancel_mode.value}",
                )
            except Exception:
                # 既に終端なら無視
                pass

            if cancel_mode is CancelMode.IMMEDIATE:
                runtime.task.cancel()

        # 終端まで待機
        try:
            await asyncio.wait_for(runtime.task, timeout=timeout_s)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        except Exception:
            pass

        return self.get(job_id)

    # ---------- internal ----------

    def _new_job_id(self) -> str:
        return f"job_{uuid.uuid4().hex[:12]}"

    def _record_immediate_failure(
        self,
        resource_name: str,
        recipe_name: str,
        parameters: dict[str, Any],
        *,
        error_class: str,
        summary: str,
    ) -> JobRecord:
        job_id = self._new_job_id()
        rec = self._store.create_job(
            job_id=job_id,
            owner="",
            resource_name=resource_name,
            recipe=recipe_name,
            parameters=parameters,
        )
        return self._store.transition_status(
            job_id, JobStatus.FAILED,
            error_class=error_class,
            last_step_summary=summary,
            result={"success": False, "error": error_class, "message": summary},
        )

    async def _run_job(
        self,
        rec: JobRecord,
        *,
        required_resources: list[str],
        override_safety: bool,
        override_reason: str,
        start_immediately: bool,
    ) -> None:
        """Job のバックグラウンド実行本体。

        - queue で待っている場合は、scheduler から起動指示が来るまで待機イベントを待つ
        - 終端遷移後、self._runtimes と scheduler から自分のエントリを必ず削除する
        """
        job_id = rec.job_id
        try:
            if not start_immediately:
                # queue 待ち。scheduler 側が次起動可能と判断したら _start_event をセットする
                await self._wait_until_scheduled(job_id)

            # 起動可能 → scheduler に running 通知
            await self._scheduler.on_running(job_id)

            await self._run_job_inner(
                rec,
                override_safety=override_safety,
                override_reason=override_reason,
            )
        finally:
            # 終端 Job の resource 解放と次 Job 起動
            try:
                next_jobs = await self._scheduler.on_terminal(job_id, required_resources)
                for nj_id in next_jobs:
                    self._wake_queued_job(nj_id)
            except Exception as e:
                logger.warning("scheduler on_terminal で例外: %s", e)
            # v0.5.0.1 fix: 終端 Job の Task 参照を解放してメモリリークを防ぐ
            self._runtimes.pop(job_id, None)

    async def _wait_until_scheduled(self, job_id: str) -> None:
        """queue 待ち中の Job を、起動可能になるまで sleep で待たせる。
        cancel が来たら CancelledError が伝播するので、それで抜ける。
        v0.5.0.3: _start_event は _JobRuntime.__init__ で eagerly 生成済み。
        """
        runtime = self._runtimes.get(job_id)
        if runtime is None:
            return
        await runtime._start_event.wait()

    def _wake_queued_job(self, job_id: str) -> None:
        """queue 先頭になった Job のイベントをセットして実行を開始させる。
        v0.5.0.3: event は常に存在するため None チェック不要。
        """
        runtime = self._runtimes.get(job_id)
        if runtime is None:
            return
        runtime._start_event.set()

    async def _run_job_inner(
        self,
        rec: JobRecord,
        *,
        override_safety: bool,
        override_reason: str,
    ) -> None:
        """Job のバックグラウンド実行本体 (内部)。終端遷移を含む全状態管理。

        v0.5.0.3: 入口で終端ガードを追加。
        immediate=True で task 起動前に cancel された場合等、
        ステータスが既に CANCELLED 等になっていた場合は何もせずに return する
        (state machine 違反による不要なログ出力を防止)。
        """
        job_id = rec.job_id
        runtime = self._runtimes[job_id]

        # v0.5.0.3: 既に終端状態 (cancelled / failed / interrupted / timeout) なら何もしない
        current = self._store.get(job_id)
        if current is not None and is_terminal(current.status):
            logger.debug(
                "_run_job_inner: job %s は既に終端 (%s) のため処理スキップ",
                job_id, current.status.value,
            )
            return

        session = self._sessions.get_session(rec.resource_name)

        # session 検証 (start_recipe_job で確認済みだが double check)
        if session is None or session.definition is None:
            self._store.transition_status(
                job_id, JobStatus.FAILED,
                error_class="not_found",
                last_step_summary="session lost",
                result={"success": False, "error": "SessionNotFound"},
            )
            return

        recipe = session.definition.recipes.get(rec.recipe)
        if recipe is None:
            self._store.transition_status(
                job_id, JobStatus.FAILED,
                error_class="not_found",
                last_step_summary="recipe not found",
                result={"success": False, "error": "RecipeNotFound"},
            )
            return

        # デフォルト適用
        variables = dict(rec.parameters)
        for p in recipe.parameters:
            if p.name not in variables and p.default is not None:
                variables[p.name] = p.default

        # Recipe → IR Plan 変換
        try:
            plan: Plan = recipe_to_plan(recipe, variables)
        except Exception as e:
            self._store.transition_status(
                job_id, JobStatus.FAILED,
                error_class="validation",
                last_step_summary=f"plan build failed: {e}",
                result={"success": False, "error": "ExpressionError", "message": str(e)},
            )
            return

        # running へ
        self._store.transition_status(job_id, JobStatus.RUNNING, current_step_index=0)

        step_results: list[dict] = []

        try:
            for idx, step in enumerate(plan.steps):
                # 各 step 開始前の timeout / cancel チェック。
                # ループ末尾でも同様のチェックを行うが、ここでのチェックは
                # 「最初の step 前」と「直近 step 完了後の cancel が次イテレーションで
                # 検出される」用。重複に見えるが、最後の step 完了直後の cancel を
                # 救うためにループ末尾チェックも必要。
                if runtime.is_timed_out():
                    self._record_timeout(rec, idx, step_results)
                    return
                if runtime.cancel_mode is not None:
                    await self._handle_cancel(
                        rec, session, runtime.cancel_mode, step_results,
                    )
                    return

                self._store.update_step(
                    job_id, idx,
                    last_step_summary=self._step_summary(step),
                )

                # WaitStep は専用パス (cancel/timeout に即応)
                if isinstance(step, WaitStep):
                    # waiting 状態へ
                    self._safe_transition(job_id, JobStatus.WAITING)
                    result = await self._run_wait_with_cancel_check(step, runtime)
                    self._safe_transition(job_id, JobStatus.RUNNING)
                elif isinstance(step, CommandStep):
                    result = await execute_command_step(
                        self._visa, session, step,
                        override_safety=override_safety,
                        override_reason=override_reason,
                    )
                else:
                    result = {
                        "success": False,
                        "error": "UnsupportedStepType",
                        "step_type": getattr(step, "type", "?"),
                    }

                step_results.append({"step": idx, **result})

                if not result.get("success", False):
                    # wait の timeout 中断 → TIMEOUT 終端へ
                    if result.get("interrupted_by_timeout"):
                        self._record_timeout(rec, idx, step_results)
                        return
                    # cancel 要求による wait 中断は failed ではなく cancel 経路へ
                    if result.get("interrupted_by_cancel"):
                        await self._handle_cancel(
                            rec, session,
                            runtime.cancel_mode or CancelMode.AFTER_CURRENT_STEP,
                            step_results,
                        )
                        return
                    err_class = result.get("error", "internal")
                    if result.get("blocked_by_safety"):
                        err_class = "safety"
                    self._store.transition_status(
                        job_id, JobStatus.FAILED,
                        current_step_index=idx,
                        error_class=err_class,
                        last_step_summary=f"step {idx} failed: {result.get('message', result.get('error', '?'))[:80]}",
                        result={
                            "success": False, "recipe": rec.recipe,
                            "steps_executed": step_results,
                            "halted_at_step": idx,
                        },
                    )
                    return

                # ループ末尾の cancel チェック。
                # 「最後の step 完了直後に cancel された」ケースを救うため必要。
                # 中間 step の場合は次イテレーション先頭のチェックと等価。
                if runtime.cancel_mode is not None:
                    if runtime.cancel_mode in (
                        CancelMode.AFTER_CURRENT_STEP, CancelMode.SAFE_SHUTDOWN,
                    ):
                        await self._handle_cancel(
                            rec, session, runtime.cancel_mode, step_results,
                        )
                        return

            # 全 step 成功
            self._store.transition_status(
                job_id, JobStatus.COMPLETED,
                current_step_index=len(plan.steps) - 1,
                last_step_summary="completed",
                result={
                    "success": True, "recipe": rec.recipe,
                    "steps_executed": step_results,
                    "step_count": len(step_results),
                },
            )

        except asyncio.CancelledError:
            # immediate cancel または asyncio runtime teardown による cancel
            # state machine: WAITING/RUNNING/CANCELLING のいずれからも CANCELLED へ向かう。
            # CANCELLED への直接遷移は CANCELLING からのみ許可されているので、
            # まず CANCELLING を経由する。既に CANCELLING / 終端なら _safe_transition でスキップ。
            self._safe_transition(job_id, JobStatus.CANCELLING)
            try:
                self._store.transition_status(
                    job_id, JobStatus.CANCELLED,
                    error_class="cancelled",
                    last_step_summary="cancelled (immediate)",
                    result={
                        "success": False, "recipe": rec.recipe,
                        "steps_executed": step_results,
                        "cancelled": True, "cancel_mode": "immediate",
                    },
                )
            except Exception:
                # 既に終端なら無視 (queued path で既に CANCELLED 等)
                pass
            # CancelledError を再 raise しないと teardown 時に warning が出る
            raise
        except Exception as e:
            logger.exception("Job %s で予期しないエラー", job_id)
            self._store.transition_status(
                job_id, JobStatus.FAILED,
                error_class="internal",
                last_step_summary=f"unexpected: {e}",
                result={"success": False, "error": "InternalError", "message": str(e)},
            )

    async def _run_wait_with_cancel_check(
        self,
        step: WaitStep,
        runtime: _JobRuntime,
    ) -> dict:
        """wait を _WAIT_SLICE_S 刻みで sleep し、間に cancel/timeout チェックを挟む。"""
        remaining = float(step.seconds)
        while remaining > 0:
            # timeout チェック
            if runtime.is_timed_out():
                return {
                    "step_type": "wait",
                    "seconds": float(step.seconds) - remaining,
                    "interrupted_by_timeout": True,
                    "success": False,
                    "error": "timeout",
                    "message": "wait interrupted by job_timeout_s",
                }
            if runtime.cancel_mode is CancelMode.IMMEDIATE:
                # asyncio.Task.cancel() で別途処理されるが、念のため
                raise asyncio.CancelledError("immediate cancel during wait")
            if runtime.cancel_mode in (
                CancelMode.AFTER_CURRENT_STEP, CancelMode.SAFE_SHUTDOWN,
            ):
                # wait は「現在 step」として扱い、即時中断する (ユーザー観点では妥当)
                return {
                    "step_type": "wait",
                    "seconds": float(step.seconds) - remaining,
                    "interrupted_by_cancel": True,
                    "success": False,
                    "error": "cancelled",
                    "message": "wait interrupted by cancel request",
                }
            chunk = min(remaining, _WAIT_SLICE_S)
            await asyncio.sleep(chunk)
            remaining -= chunk
        return {
            "step_type": "wait",
            "seconds": step.seconds,
            "success": True,
        }

    async def _handle_cancel(
        self,
        rec: JobRecord,
        session,
        mode: CancelMode,
        step_results: list[dict],
    ) -> JobStatus:
        """cancel 要求を実際に実行 (safe_shutdown なら shutdown シーケンス)"""
        job_id = rec.job_id

        # v0.5.0.4: safe_shutdown は構造化結果 (dict) を返す
        shutdown_info: dict | None = None
        if mode is CancelMode.SAFE_SHUTDOWN:
            shutdown_info = await self._best_effort_safe_shutdown(session)
            step_results.append({
                "step": -1, "step_type": "safe_shutdown",
                "shutdown": shutdown_info,
                "success": bool(shutdown_info.get("success") or not shutdown_info.get("attempted")),
            })

        # CANCELLED への遷移は CANCELLING 経由が必要。途中で cancel 検出された場合は
        # まず CANCELLING に遷移してから CANCELLED へ。
        current = self._store.get(job_id)
        if current and current.status not in (JobStatus.CANCELLING, JobStatus.CANCELLED):
            self._safe_transition(job_id, JobStatus.CANCELLING)

        self._store.transition_status(
            job_id, JobStatus.CANCELLED,
            error_class="cancelled",
            last_step_summary=(
                f"cancelled ({mode.value})"
                + (f" shutdown_success={shutdown_info.get('success')}" if shutdown_info else "")
            ),
            result={
                "success": False, "recipe": rec.recipe,
                "steps_executed": step_results,
                "cancelled": True, "cancel_mode": mode.value,
                # v0.5.0.4: safe_shutdown サマリを構造化付与
                "safe_shutdown": shutdown_info if shutdown_info is not None else None,
            },
        )
        return JobStatus.CANCELLED

    def _record_timeout(
        self,
        rec: JobRecord,
        step_idx: int,
        step_results: list[dict],
    ) -> None:
        """job_timeout_s 経過時の TIMEOUT 終端遷移を記録"""
        self._store.transition_status(
            rec.job_id, JobStatus.TIMEOUT,
            current_step_index=step_idx,
            error_class="timeout",
            last_step_summary=f"job_timeout_s exceeded at step {step_idx}",
            result={
                "success": False, "recipe": rec.recipe,
                "steps_executed": step_results,
                "timed_out_at_step": step_idx,
            },
        )

    # safe_shutdown YAML が無いとき fallback を適用するカテゴリ (v0.5.0.4)
    # その他 (温調器・モータ・ポンプ・電子負荷・リレー等) は YAML 定義必須。
    _SAFE_SHUTDOWN_FALLBACK_CATEGORIES = frozenset({
        "power_supply",
        "source_measure_unit",
    })

    # YAML safe_shutdown 内の各 wait step に許容する最大秒数 (v0.5.0.4)
    _SAFE_SHUTDOWN_WAIT_MAX_S = 10.0

    async def _best_effort_safe_shutdown(self, session) -> dict:
        """安全停止を実行する。

        v0.5.0.4 で返り値を構造化 dict に変更:
          {
            "attempted": bool,
            "source": "yaml" | "fallback_power_supply" | "none",
            "success": bool,                    # 全 step が success なら True
            "steps": [{"step": i, "kind": "command"|"wait", "command": ..., "success": bool, ...}],
            "skipped_reason": str | None,       # source="none" の場合の理由
          }

        優先順位:
          1. YAML の `safe_shutdown` セクションに定義されたシーケンス
          2. 上記が無くかつ metadata.category in {power_supply, source_measure_unit} の場合のみ、
             fallback (set_output OFF + set_voltage 0)
          3. その他のカテゴリ (温調器・モータ等) は fallback 無効 → no-op
        """
        if session is None or session.definition is None:
            return {
                "attempted": False, "source": "none", "success": False,
                "steps": [], "skipped_reason": "no session",
            }

        # 1. YAML 定義された safe_shutdown を優先
        if session.definition.safe_shutdown:
            return await self._run_yaml_shutdown(
                session, session.definition.safe_shutdown,
            )

        # 2. Fallback: 電源系のみ
        category = session.definition.metadata.category
        if category not in self._SAFE_SHUTDOWN_FALLBACK_CATEGORIES:
            return {
                "attempted": False, "source": "none", "success": False,
                "steps": [],
                "skipped_reason": (
                    f"no YAML safe_shutdown; fallback disabled for "
                    f"category='{category}' (allowed: power_supply, source_measure_unit)"
                ),
            }

        # fallback 実行
        steps_result: list[dict] = []
        all_ok = True
        for idx, (cmd_name, args) in enumerate([
            ("set_output", {"state": "OFF"}),
            ("set_voltage", {"voltage": 0}),
        ]):
            cmd_def = session.definition.commands.get(cmd_name)
            if cmd_def is None:
                steps_result.append({
                    "step": idx, "kind": "command", "command": cmd_name,
                    "success": False, "error": "CommandNotFound",
                })
                all_ok = False
                continue
            try:
                step = CommandStep(command=cmd_name, args=args)
                r = await execute_command_step(
                    self._visa, session, step,
                    override_safety=True,
                    override_reason="safe_shutdown by cancel (fallback)",
                )
                ok = bool(r.get("success"))
                all_ok = all_ok and ok
                steps_result.append({
                    "step": idx, "kind": "command", "command": cmd_name,
                    "success": ok,
                    "scpi_sent": r.get("scpi_sent"),
                    "error": r.get("error") if not ok else None,
                })
            except Exception as e:
                all_ok = False
                steps_result.append({
                    "step": idx, "kind": "command", "command": cmd_name,
                    "success": False, "error": type(e).__name__,
                    "message": str(e),
                })

        return {
            "attempted": True,
            "source": "fallback_power_supply",
            "success": all_ok,
            "steps": steps_result,
        }

    async def _run_yaml_shutdown(self, session, steps: list) -> dict:
        """YAML 定義の safe_shutdown ステップを順次実行 (override_safety=True)

        v0.5.0.4 で:
        - 構造化結果 dict を返す
        - wait step は slice 方式 (cancel/timeout を阻害しない、上限 _SAFE_SHUTDOWN_WAIT_MAX_S)
        - 文字列式 ("$var") は受け付けない (数値リテラルのみ、安全停止の予測可能性のため)
        """
        steps_result: list[dict] = []
        all_ok = True

        for idx, rs in enumerate(steps):
            try:
                if rs.step_type == "wait":
                    seconds_raw = rs.wait.get("seconds", 0)
                    # 数値リテラルのみ許可
                    if isinstance(seconds_raw, str):
                        steps_result.append({
                            "step": idx, "kind": "wait",
                            "success": False, "error": "ExpressionNotAllowed",
                            "message": "safe_shutdown wait は数値リテラルのみ許可",
                        })
                        all_ok = False
                        continue
                    seconds = min(float(seconds_raw), self._SAFE_SHUTDOWN_WAIT_MAX_S)
                    # slice 方式 (上限到達・kernel cancel に応答可能)
                    remaining = seconds
                    while remaining > 0:
                        chunk = min(remaining, _WAIT_SLICE_S)
                        await asyncio.sleep(chunk)
                        remaining -= chunk
                    steps_result.append({
                        "step": idx, "kind": "wait",
                        "seconds": seconds,
                        "success": True,
                    })
                else:
                    step = CommandStep(command=rs.command or "", args=rs.args)
                    r = await execute_command_step(
                        self._visa, session, step,
                        override_safety=True,
                        override_reason="safe_shutdown by cancel (YAML)",
                    )
                    ok = bool(r.get("success"))
                    all_ok = all_ok and ok
                    steps_result.append({
                        "step": idx, "kind": "command", "command": rs.command,
                        "success": ok,
                        "scpi_sent": r.get("scpi_sent"),
                        "error": r.get("error") if not ok else None,
                    })
            except Exception as e:
                all_ok = False
                steps_result.append({
                    "step": idx, "kind": getattr(rs, "step_type", "?"),
                    "success": False, "error": type(e).__name__,
                    "message": str(e),
                })

        return {
            "attempted": True,
            "source": "yaml",
            "success": all_ok,
            "steps": steps_result,
        }

    def _safe_transition(self, job_id: str, to: JobStatus) -> None:
        """遷移ルール違反は黙って無視 (cancelling 中の状態変更等)"""
        try:
            self._store.transition_status(job_id, to)
        except Exception:
            pass

    @staticmethod
    def _step_summary(step) -> str:
        if isinstance(step, WaitStep):
            return f"wait {step.seconds}s"
        if isinstance(step, CommandStep):
            return f"command {step.command}"
        return f"step type={getattr(step, 'type', '?')}"
