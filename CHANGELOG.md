# 変更履歴

## v0.5.0.4 — 外部レビュー対応 (API 露出 + ドキュメント整合 + safe_shutdown 構造化)

v0.5.0.2/v0.5.0.3 公開後の外部レビューで指摘された P0 三件 + P1 三件への対応。
コード本体の機能は v0.5.0.2 で完成しているが、**API 露出とドキュメント整合**が
不十分だったため、それを整える。

### API / docs 整合

- **P0: `docs/jobs.md` を `ResourceScheduler` 前提に更新**
  - 旧: 「同一機器への並列 Job は `VisaManager` の resource-level lock で直列化」
  - 新: 「同一 resource への並列 Job は `ResourceScheduler` により Job 単位で直列化。
    running/waiting 中は Job 終了まで resource 占有」
  - `queued` も再起動時 `interrupted` 対象であることを明記
  - `queue_policy` の説明追加 (queue / reject_if_busy)
- **P0: MCP `start_recipe_job` に `queue_policy` 引数を追加**
  - 既に `JobManager.start_recipe_job` で実装されていたが MCP ツールに未露出だったため
    LLM はデフォルトの `queue` しか使えなかった
  - `queue_policy: str = "queue"` を MCP ツール引数に追加、バリデーション付き
  - `reject_if_busy` は busy 時に `error_class='blocked'` を返す
- **P0: `start_recipe_job` レスポンスに `data.scheduling` 追加**
  - `immediate_start` / `blocked_by_job` / `queue_position` / `queue_policy` を含む構造化情報
  - LLM が「今すぐ走るのか、待ち行列に入ったのか」を即座に判断可能
  - `ResourceScheduler.get_scheduling_info()` メソッドを新設

### safe_shutdown 改善

- **P1: fallback を `metadata.category` で制限**
  - 旧: 全機器で `set_output OFF + set_voltage 0` を試行 (温調器・モータでは危険)
  - 新: `power_supply` / `source_measure_unit` カテゴリのみ fallback 適用
  - その他のカテゴリで YAML `safe_shutdown` 未定義の場合は **no-op** + 構造化された理由
    (`skipped_reason: "fallback disabled for category=..."`)
- **P1: 構造化結果を返す**
  - 旧: 文字列 (`"source=yaml,set_output:ok,set_voltage:ok"`)
  - 新: dict
    ```python
    {
      "attempted": bool,
      "source": "yaml" | "fallback_power_supply" | "none",
      "success": bool,
      "steps": [{"step": i, "kind": "command"|"wait", ...}],
      "skipped_reason": str | None,
    }
    ```
  - `cancel_job` の result に `safe_shutdown` キーで埋め込み、LLM が成否を機械可読に判定可能
- **P1: YAML safe_shutdown 内 wait の slice 化 + 上限**
  - 旧: `asyncio.sleep(seconds)` 一括 (cancel_job timeout と整合しない)
  - 新: `_WAIT_SLICE_S=0.2` 単位で slice、`_SAFE_SHUTDOWN_WAIT_MAX_S=10` 秒で上限
  - YAML 内 wait は数値リテラルのみ許可 (式 `$var` は拒否、予測可能性のため)

### テスト追加

- `tests/test_safe_shutdown_v0504.py` (7 件)
  - power_supply での fallback 動作
  - multimeter での fallback 抑止 (skipped_reason 確認)
  - YAML 定義時の YAML 優先
  - YAML wait の上限切り (100s 指定 → 10s で打ち切り)
  - no session 時の no-op
  - scheduling info: immediate / queued

合計 235 件パス (v0.5.0.3 の 220 件から +15)。

### 後方互換

- 既存 YAML / Recipe / Safety はすべて変更なしで動作
- `start_recipe_job` MCP の `queue_policy` 引数は省略可 (default "queue")
- **挙動変化が一件**: 非 power_supply 系機器で `safe_shutdown` を YAML 定義していない場合、
  従来は最低限の `set_output OFF + set_voltage 0` を試行していたが、v0.5.0.4 では
  no-op (skipped) になる。**該当機器の YAML に明示的に `safe_shutdown` を追加すること**

### 残課題 (v0.5.1 で対応予定)

- `recommended_next_actions` 内の `retry_with_override` を別カテゴリ (`dangerous_actions_available`)
  に分離 (現状は `requires_human_confirmation: True` で警告強化済み)
- wait 中の `step_remaining_s` を `get_job_status` に追加
- `job_events` 軽量テーブル

---

## v0.5.0.3 — 内部レビュー (Job queue のレース条件修正)

v0.5.0.2 公開後の内部コードレビューで検出された 2 件のレース条件への対処パッチ。
機能追加なし、API 変化なし。

### バグ修正

- **Lost wake-up race の修正** (High)
  - 旧コード: `_JobRuntime._start_event` を `_wait_until_scheduled` で遅延生成
  - 問題: `start_recipe_job` 直後・task 実行前に別 Job の `on_terminal` が
    `_wake_queued_job` を呼んでも、`_start_event` が `None` のため wake が失われる。
    キューに並んだ Job が**永久に queued のまま起動しない**。
  - 修正: `_JobRuntime.__init__` で `asyncio.Event()` を eagerly 生成。
    `_wait_until_scheduled` / `_wake_queued_job` / `cancel()` の None チェックを削除。
- **Cancel-immediate レースの state machine 違反修正** (Medium)
  - 旧コード: immediate=True で active 登録後・task 実行前に `cancel` 呼び出し →
    ステータスを QUEUED → CANCELLED に遷移後、task が `_run_job_inner` を実行 →
    `transition_status(RUNNING)` で **CANCELLED → RUNNING の不正遷移** を試行 →
    ログにエラーが出力される (最終的には finally で resource は解放)
  - 修正: `_run_job_inner` 入口で `is_terminal(current.status)` をチェック、
    既に終端なら何もせずに return。state machine 違反ログを抑制。

### テスト追加

- `tests/test_job_race_conditions.py` (5 件)
  - `test_event_eagerly_created`: `_JobRuntime.__init__` で event 生成確認
  - `test_no_lost_wake_when_predecessor_terminates_fast`: 連続 Job 投入で 2 番目が
    永久 queued にならないこと
  - `test_cancel_immediate_after_start_no_state_violation`: 即 cancel で
    state 違反ログが出ないこと
  - `test_cancel_queued_no_state_violation`: queued Job の cancel 経路で同上
  - `test_three_jobs_serialized`: 3 連続 Job が全て完走

合計 208 件パス (v0.5.0.2 の 203 件から +5)。

### 後方互換

- API 変化なし、既存 Job / Recipe / YAML はすべて変更なしで動作
- 動作上の変化は「永久 queued バグの解消」と「不要なログの抑制」のみ

---

## v0.5.0.2 — 外部レビュー対応 (Job 単位排他 + YAML safe_shutdown ほか)

v0.5.0.1 公開後の外部レビューで指摘された P0 二件 + P1 三件 + P2 二件への対処。
**実験実行基盤として最も重要な「Job 単位での resource 排他」を実装**。

### 重要修正 (P0)

- **Job 単位の resource 排他 (queue 機構)** ── 同一 resource への複数 Job は queued で順番待ち
  - 旧コード: `VisaManager` の lock は VISA 通信単位のみ。2 Job が同じ電源に対して
    `set_voltage` → `wait` → `measure_current` を走らせると wait 中に interleave し、
    測定条件が取り違わる重大バグ
  - 新コード: `src/visa_mcp/job/scheduler.py` (`ResourceScheduler`) を新設し、Job 単位の
    queue + active を管理。`_run_job_inner` 全体が resource を占有する
  - `start_recipe_job(..., queue_policy="queue" | "reject_if_busy")` 引数追加
  - `get_job_status` の data に `queue.queue_position` / `queue.blocking_job_id` を追加
  - 内部表現は将来の Group / Map に向けて `required_resources: list[str]` で持つ
- **`queued` も再起動時に `interrupted` へ遷移** (v0.5.0.1 では running/waiting/cancelling のみ対象)

### 重要修正 (P1)

- **YAML `safe_shutdown` フィールド追加** ── 機器ごとの安全停止シーケンスを YAML で宣言可能
  - `InstrumentDefinition.safe_shutdown: list[RecipeStep] = []`
  - `_best_effort_safe_shutdown` は YAML 定義を優先、未定義時のみ既存 fallback
    (`set_output OFF` + `set_voltage 0`、power_supply 系のみ妥当)
  - PMX35-3A YAML に明示的に追加
- **`retry_with_override` 警告強化** ── 危険操作の語気を強める
  - `requires_human_confirmation: True` フラグ追加
  - reason に「**LLM が単独で判断・実行することは禁止**」を明記
  - `ask_human_for_decision` action を retry より前に挿入
- **server.py instructions に Job 利用導線追加** ── LLM が `execute_recipe` と `start_recipe_job`
  を使い分けやすいよう、「長時間 / wait を含む / 数十秒以上 → Job を使え」を明示

### バグ修正

- **`asyncio.CancelledError` の state machine 遷移を修正** ── 旧コードは WAITING → CANCELLED を
  直接遷移していたが state machine では CANCELLING 経由必須。`_safe_transition(CANCELLING)`
  を挟む形に修正、CancelledError は再 raise してテスト teardown 時の warning を抑制

### その他 (P2)

- **`pyproject.toml` 形式確認** ── `tomllib` で正常 parse 確認済み (raw view の表示問題のみ)

### 新規モジュール / ファイル

- `src/visa_mcp/job/scheduler.py` ── `ResourceScheduler` / `ResourceBusyError` / `QueuePolicy`
- `tests/test_resource_scheduler.py` (10 件)
- `tests/test_job_queue_interleave.py` (6 件、再起動 interrupted 含む)

### テスト

- 230 件全パス (v0.5.0.1 の 215 件から +15 件)
- 統合テストで「同一 resource で 2 Job → 1 つは queued」「異 resource で並列実行」
  「queued Job のキャンセル」「reject_if_busy で busy 時 failed」などをカバー

### 後方互換

- 既存 MCP ツールのシグネチャ・既存 YAML はすべて変更なしで動作
- `start_recipe_job` の `queue_policy` 引数は省略可 (default "queue")
- 「同一 resource Job が直列化」は**意図的な挙動変化**: 旧 v0.5.0.1 では interleave が起きうるバグだった

### 注意事項 (移行ガイド)

- 同一機器に対する Job を **意図的に並列実行していた**場合、v0.5.0.2 では 2 Job 目以降が
  queued になる。**並列実行に依存していたコードはない想定**だが、もしあれば異なる
  resource 名 (機器) に分けるか queue_policy="reject_if_busy" で明示的にエラー化を選ぶ
- `_best_effort_safe_shutdown` は power_supply 系のみ fallback 妥当。**温調器・モータ等は
  YAML safe_shutdown を明示定義する**こと

---

## v0.5.0.1 — コードレビュー対応パッチ

v0.5.0 公開後の内部コードレビューで指摘された Bug 2 件と品質改善 3 件への対処。
機能追加なし、既存 API と挙動は不変 (Bug 修正は隠れていた負数受理問題のみ動作変化)。

### バグ修正

- **`WaitStep` の負数検証が動作していなかった問題を修正** (High)
  - 旧コード: `__post_init_post_parse__` を使用 → Pydantic v2 では呼ばれず、負の seconds が silently 受理されていた
  - 修正: `@field_validator("seconds")` に置き換え、ValidationError を確実に発生
  - 影響: `WaitStep(seconds=-5)` 等の不正値が今後は登録時にエラー
- **`JobManager._runtimes` のメモリリークを修正** (High)
  - 旧コード: Job が終端 (completed / failed / cancelled / timeout / interrupted) に達しても `_runtimes` dict から削除されなかった
  - 修正: `_run_job` を `try/finally` で包み、終端時に `self._runtimes.pop(job_id, None)` を実行
  - 影響: 長期運用時のメモリ使用量が安定

### リファクタリング (挙動変化なし)

- **`step_executor.py` モジュール新設** (Medium)
  - `_execute_command_step` / `_execute_wait_step` を `recipe_executor.py` から切り出し、`execute_command_step` / `execute_wait_step` として public 化
  - 旧コードは prefix `_` で命名されつつ `job/manager.py` から外部 import されており、命名規約と実態が乖離していた
  - import 経路: `from visa_mcp.step_executor import execute_command_step, execute_wait_step`
- **死コード削除**: `_run_job` 内の未使用 `last_terminal: JobStatus` 変数を削除
- **コメント追加**: `_run_job` ループ先頭・末尾の cancel チェック重複箇所に、「最後の step 完了直後の cancel を救うため」という意図を明記

### テスト追加

- `test_wait_step_negative_rejected` (test_experiment_ir.py): 負の seconds が ValidationError
- `test_runtimes_cleaned_after_terminal` (test_job_manager.py): 終端後に `_runtimes` から消える
- `test_runtimes_cleaned_after_immediate_failure` (test_job_manager.py): validation 失敗時は `_runtimes` に入らない

合計 215 件 (v0.5.0 の 212 件から +3 件)。

### 後方互換

- 既存 MCP ツール / Recipe / YAML / Safety / Response Format は完全に不変
- `WaitStep(seconds=-N)` を意図的に使っていた利用者はいないはず (機能的に意味がない)

---

## v0.5.0 — Job MVP 正式版

実験実行基盤の "Job MVP" を正式リリース。rc1/rc2 で導入した基盤に **timeout 自動遷移** と
**recommended_next_actions** を加え、長時間 Recipe を AI エージェントに安全に委譲できる状態に到達。

### v0.5.0 で追加 (rc2 → 正式)

- **`job_timeout_s` パラメータ** ── `start_recipe_job` に追加。経過すると Job は自動で
  `timeout` 状態に遷移。step 境界 + wait 200ms スライス毎にチェック
- **`recommended_next_actions`** ── 終端状態 (failed / timeout / cancelled / interrupted) の
  `get_job_result` レスポンスに、エラー種別に応じた次手候補を構造化付与
  - timeout: retry (より大きな job_timeout_s で) / inspect_state / safe_shutdown
  - safety failed: review_safety_constraints / retry_with_override
  - validation failed: fix_parameters
  - not_found failed: list_recipes / list_resources
  - interrupted: inspect_state / safe_shutdown / resume_from_step (v0.9.0+ 予定)
- **`docs/jobs.md`** 新規 ── Job モデル全体のリファレンス
- **README 更新** ── 20 ツールを Identification / Execution / Job / Import に分類

### v0.5.0 全体の累積機能

#### MCP ツール (20 個 + opt-in 2 個 = 最大 22 個)

| カテゴリ | ツール | 概要 |
|---------|-------|------|
| 識別・情報 (10) | `list_resources`, `identify_*`, `bind_definition`, `list_available_definitions`, `list_commands`, `get_instrument_info`, `list_safety_constraints`, `reload_definitions` | 機器の発見と情報 |
| 同期実行 (4) | `execute_named_command`, `validate_operation`, `list_recipes`, `execute_recipe` | コマンド・recipe の即時実行 |
| **Job (5) 新規** | `start_recipe_job`, `get_job_status`, `get_job_result`, `list_jobs`, `cancel_job` | バックグラウンド非同期実行 |
| 取り込み (1) | `extract_pdf_commands` | PDF → YAML 草案 |
| opt-in (2) | `unsafe_send_command`, `unsafe_query_instrument` | 任意 SCPI (危険) |

#### Job 状態機械

```
queued → running → waiting → completed
                 → failed       (safety / validation / hardware / protocol / internal)
                 → cancelling → cancelled
                 → timeout      (job_timeout_s 経過)
                 → interrupted  (サーバ再起動)
```

#### CancelMode

| モード | 動作 |
|-------|------|
| `immediate` | `asyncio.Task.cancel()` |
| `after_current_step` | 現在 step 完了後 / wait 中断で停止 |
| `safe_shutdown` | `set_output OFF` + `set_voltage 0` を試みてから停止 |

#### 永続化

- `~/.visa-mcp/state.sqlite` (環境変数 `VISA_MCP_STATE_DB` で変更可)
- WAL モード、スレッドセーフ
- 起動時に running/waiting/cancelling な Job を `interrupted` に自動遷移

#### 内部 IR

- `visa_mcp.experiment_ir.Step` (CommandStep / WaitStep の discriminated union)
- `visa_mcp.experiment_ir.Plan`
- Recipe / Job / (将来の Group / DSL) executor が共有
- v0.8.0 のリポジトリ分割時に `experiment_mcp/ir/` へそのまま移動できる疎結合設計

#### 標準レスポンス形式

v0.5.0+ 新規ツール (15 個中 5 個の Job ツール) は `response_envelope` 形式で返す:

```json
{
  "status": "ok" | "error" | "partial_failure" | "running",
  "data": { ... },
  "errors": [{
    "error_class": "...",
    "message": "...",
    "recoverable": true,
    "recommended_next_actions": [...]
  }],
  "metadata": { "timestamp": "...", "elapsed_s": ..., "job_id": "..." }
}
```

### テスト

- **212 件全パス** (v0.4.1 の 115 件から +97 件)
  - `test_experiment_ir.py` (10): IR 型
  - `test_response_envelope.py` (12): envelope / error 生成
  - `test_recipe_wait_step.py` (11): RecipeStep + recipe_to_plan + 実行
  - `test_job_state_machine.py` (25): 遷移ルール
  - `test_job_store.py` (10): SQLite CRUD
  - `test_job_manager.py` (9): start/wait/cancel/list
  - `test_job_timeout.py` (4): job_timeout_s 経路
  - `test_recommended_next_actions.py` (10): 次手候補生成

### 実機検証 (Kikusui PMX35-3A)

- 9-step recipe (wait 含む) を Job として `queued → waiting → completed` で完走
- `cancel_job(safe_shutdown)` 後の `OUTP?` = 0 (安全停止後の出力 OFF を確認)
- `job_timeout_s=1.5` で 10 秒 wait を含む job が **step 6 (wait) で TIMEOUT に自動遷移**

### 後方互換

- 既存 17 ツール + recipe / safety / response_format すべて変更なし
- v0.4.1 までの YAML 定義はすべて変更なしで動作

### 次のリリース (v0.5.1) で予定

- 条件待機 step (`wait_until` / `wait_for_condition` / `wait_for_stable`)
- `start_wait_job` MCP ツール

---

## v0.5.0-rc2 — Job 基盤 (state machine + SQLite + 5 MCP ツール)

実験実行基盤 "Job MVP" の中核。Recipe を非同期 Job として登録・追跡・キャンセルできる。

### 新規モジュール

- **`visa_mcp.job`** ── Job 実行基盤
  - `state_machine`: `JobStatus` (queued/running/waiting/completed/failed/cancelling/cancelled/timeout/interrupted) + `CancelMode` + 遷移ルール検証
  - `store.JobStore`: SQLite 永続化 (スキーマ最小版: jobs テーブルのみ)
  - `manager.JobManager`: バックグラウンド Job 実行 + キャンセル + interrupted 自動遷移

### 新規 MCP ツール (5 個)

| ツール | 用途 |
|-------|------|
| `start_recipe_job(resource, recipe, parameters, owner, override_safety, override_reason)` | recipe を Job 化、即 job_id 返却 |
| `get_job_status(job_id)` | 状態 + current_step + 簡易サマリ |
| `get_job_result(job_id)` | 完了/失敗/中断時の steps_executed を含む完全結果 |
| `list_jobs(status_filter, owner, limit)` | Job 一覧 (新しい順、安定ソート) |
| `cancel_job(job_id, cancel_mode, timeout_s)` | キャンセル要求 (immediate / after_current_step / safe_shutdown) |

すべて v0.5.0+ の標準 envelope 形式 (response_envelope) で返す。

### Job 状態機械

```
queued → running → waiting → completed
                 → failed
                 → cancelling → cancelled
                 → timeout
                 → interrupted (サーバ再起動)
```

### 再起動セマンティクス

サーバ起動時、SQLite 上の `running` / `waiting` / `cancelling` Job を `interrupted` に遷移させる。
LLM は `list_jobs` で過去ジョブの履歴と中断状態を確認可能 (自動復帰は v0.9.0 以降)。

### CancelMode

| モード | 動作 |
|-------|------|
| `immediate` | asyncio.Task を直ちにキャンセル (CancelledError) |
| `after_current_step` | 現在の step 完了後 or wait 中断で停止 |
| `safe_shutdown` | YAML/汎用安全停止 (set_output OFF, set_voltage 0) を実行してから停止 |

WaitStep 実行中も 200ms 刻みで cancel チェック → 長い待機中も即時応答可能。

### 永続化

`~/.visa-mcp/state.sqlite` (環境変数 `VISA_MCP_STATE_DB` で変更可) に jobs テーブルを保持。
WAL モード、スレッドセーフ。

### テスト

- 199 件全パス (rc1 の 149 件から +50 件)
  - `test_job_state_machine.py` (25 件): 遷移ルール / Terminal/Active 判定 / CancelMode
  - `test_job_store.py` (10 件): create/get/list/transition/update_step/mark_interrupted_on_startup
  - `test_job_manager.py` (9 件): start/wait/cancel/list (モック VISA)
- 実機検証 (PMX35-3A):
  - 9-step recipe を Job として完走 (queued → waiting → completed)
  - safe_shutdown による cancel で OUTP? = 0 を確認 (出力 OFF が走った)
  - list_jobs で複数 Job の状態取得

### 制約事項 (v0.5.0-rc2 時点)

- SQLite スキーマは最小版 (jobs のみ)。`job_steps` / `measurement_cache` / `locks` / `monitor_data` は v0.7.0 で追加
- `verify` / `state_query` / `describe_instrument` / `get_state` は v0.7.0
- Group / Map / Bus 単位並列制御は v0.6.0
- wait の polling 系 (`wait_for_condition` / `wait_for_stable`) は v0.5.1

### 後方互換

- 既存 17 ツール + recipe / safety / response_format / experiment_ir すべて変更なし
- 既存テスト (149 件) もすべてパス

---

## v0.5.0-rc1 — 内部 IR + wait step + 標準レスポンス形式

実験実行基盤 (v0.5.0 "Job MVP") に向けた最初の rc。後方互換を維持しながら基礎レイヤーを導入する。

### 新規モジュール

- **`visa_mcp.experiment_ir`** ── 内部 Intermediate Representation
  - `CommandStep` / `WaitStep` (Pydantic discriminated union)
  - `Plan` (Step のシーケンス + parameters + metadata)
  - v0.8.0 のリポジトリ分割時に `experiment_mcp/ir/` へそのまま移動できるよう疎結合設計
- **`visa_mcp.response_envelope`** ── v0.5.0+ 新規ツール用の標準レスポンス形式
  - `make_envelope(status, data, errors, ...)`、`make_error(error_class, ...)`
  - top-level `status`: `ok / error / partial_failure / running`

### 追加機能

- **Recipe に `wait` step タイプを追加** (後方互換)
  ```yaml
  recipes:
    set_and_settle:
      steps:
        - { command: "set_voltage", args: { voltage: "$v" } }
        - wait: { seconds: "$settle_s" }       # 新規
        - { command: "measure_voltage" }
  ```
  `wait.seconds` には数値リテラルまたは `$var` 形式の式が指定可能。
- **`recipe_executor` を内部 IR ベースに refactor**
  - `recipe_to_plan(recipe, variables)` で RecipeDefinition → IR Plan に変換
  - `execute_plan(visa, session, plan)` で IR Plan を実行
  - 既存 `execute_recipe` API の戻り値形式は v0.3.0/v0.4.x と同一 (後方互換)

### サンプル

- `examples/instruments/kikusui_pmx35_3a.yaml` に `set_voltage_and_measure_after_settling` recipe 追加 (wait step 使用例)

### テスト

- 149 件全パス (v0.4.1 の 115 件から +34 件)
  - `test_experiment_ir.py` (10 件): Step / Plan の作成・シリアライズ
  - `test_response_envelope.py` (12 件): envelope / error 生成
  - `test_recipe_wait_step.py` (11 件): RecipeStep スキーマ + recipe_to_plan + 実行
- 実機検証: PMX35-3A で wait 含む 9 ステップ recipe が 1.5 秒待機を含めて 1.57 秒で完走、実測 5.003V

### 移行ノート

- 既存 v0.4.1 の YAML / API はすべて変更なしで動作 (後方互換)
- 新規ツールはまだ追加されていない (v0.5.0-rc2 で Job manager + MCP ツール 5 個を追加予定)

---

## v0.4.1 — 危険キーワード検出の堅牢化

外部レビュー指摘の残課題を対処したパッチリリース。

### セキュリティ・安全性

- **SCPI ロングフォーム対応**: `VOLT` だけでなく `VOLTAGE`、`CURR` だけでなく `CURRENT`、
  `OUTP` だけでなく `OUTPUT` など、短縮形・正式表記の両方を検出するよう修正。
  正規表現を `VOLT(?:AGE)?` 形式に変更し、単語境界による見逃しを解消。
- **複合コマンドの `?` バイパス修正**: `CONF:VOLT;READ?` や `INIT;*OPC?` のように
  `;` を含む複合コマンドは `?` があっても危険キーワード検査の対象とするよう修正。
  `?` のみ含み `;` を含まない pure query のみスキップ対象とした。

### ドキュメント

- `server.py` の MCP instructions から削除済みの `query_instrument / send_command` 記述を削除。
  `unsafe_send_command / unsafe_query_instrument` は opt-in かつ non-strict 時のみ登録されることを明記。

### テスト

- ロングフォーム検出 12 ケース、複合コマンド 4 ケース、pure query 安全扱い 7 ケースを追加。
- ユニットテスト 90 件パス (v0.4.0 の 71 件から +19 件)。

---

## v0.4.0 — 安全性の強化

外部レビューで指摘された安全制約バイパスと並列実行リスクへの対応リリース。レビュー指摘の P0 項目すべてを対処しています。

### 破壊的変更

- **既定の安全モードを `advisory` から `strict` に変更しました。**
  LLM が操作主体になる MCP では保守的な初期値が望ましいため、変更しました。
  従来の挙動に依存していた利用者は、明示的に `VISA_MCP_SAFETY_MODE=advisory` を指定してください。
- **`send_command` / `query_instrument` をデフォルトで無効化しました。**
  生 SCPI のパススルーは `VISA_MCP_ENABLE_RAW_COMMANDS=1` でオプトイン、
  名称を `unsafe_send_command` / `unsafe_query_instrument` に変更しました。
  `strict` モードでは、環境変数の有無にかかわらず登録されません。

### セキュリティ・安全性

- **リソース単位の `asyncio.Lock`** を `VisaManager` に追加。
  同一 VISA リソースへの並列呼び出しは直列化され、異なるリソースは並列維持されます。
  LLM が複数ツールを並列起動した際のパケット混在・応答取り違えを防止します。
- **危険キーワード検出** を raw SCPI コマンドに追加。
  `VOLT` / `CURR` / `OUTP` / `SOUR` / `CONF` / `FUNC` / `RANG` /
  `*RST` / `*CLS` / `*SAV` / `INIT` / `TRIG` / `MEM` / `STOR` / `RECALL`
  を含み、`?` を含まないコマンドは検出され、`override_safety=True` と
  `override_reason` の指定が必要になります。
- **起動時警告** — `VISA_MCP_SAFETY_MODE` が未設定の場合に警告ログを出力します。

### ドキュメント

- バージョン整合性の修正: `pyproject.toml` を `0.1.0` から `0.4.0` に更新。
- README のツール数記載を 12 から 17 (+ オプトイン 2 個) に修正。
- `docs/safety.md` を更新し、新しいデフォルトと raw コマンドの方針を反映。

### テスト

- ユニットテスト 71 件パス (v0.3.0 の 63 件から +8 件)。
- 危険キーワード検出と排他ロックの動作を追加テストでカバー。

---

## v0.3.0 — Recipe / 応答パース / 動作状態

- **Recipes**: 複数コマンドの安全な順序を YAML で宣言的に定義。
  `$var * 1.1` のような安全な算術式評価をサポート。
- **応答パーサ**: ベンダ独自フォーマット (例: Yokogawa 7563 の
  `NTKC+00027.0E+0`) を正規表現で構造化辞書に変換。
- **動作状態 / 物理インタフェース**: 起動シーケンス・動作モード・
  端子情報を YAML に記述可能に。
- 新規 MCP ツール: `list_recipes`, `execute_recipe` (合計 17 個、v0.2.0 の 15 個から増加)。
- テスト 63 件パス (v0.2.0 の 43 件から増加)。

## v0.2.0 — 安全制約システム

- YAML に `safety` セクション追加: `ratings` / `preconditions` /
  `cautions` / `hardware_protections`。
- 環境変数 `VISA_MCP_SAFETY_MODE` で 3 段階の安全モード切替:
  `strict` / `advisory` / `permissive` (本バージョンの既定は `advisory`)。
- `execute_named_command` に `override_safety` + `override_reason` 引数を追加。
- 監査ログ (JSON Lines 形式) を `~/.visa-mcp/audit.log` に出力。
- 新規 MCP ツール: `get_instrument_info`, `list_safety_constraints`,
  `validate_operation` (合計 15 個)。

## v0.1.0 — 初回公開リリース

- 12 個の MCP ツール (機器検出・識別・実行・PDF 抽出)。
- YAML ベースの機器コマンド定義。
- `*IDN?` 自動識別 + 旧世代非 SCPI 機器向け手動バインディング。
- 型・範囲・列挙値のパラメータ検証。
- FastMCP + asyncio による非同期実装。
- 実機検証: Kikusui PMX35-3A (USB / SCPI) と Yokogawa 7563 (GPIB / 独自プロトコル)。
