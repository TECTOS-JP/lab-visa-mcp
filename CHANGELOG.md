# 変更履歴

## v0.8.3.1 — DSL usability refinement レビュー対応 (P1/P2)

v0.8.3 外部レビューの P1/P2 指摘に対応。新規 MCP ツールなし、互換維持
(experimental スコープ内の動作変更のみ)。

### P0 確認

- raw 改行問題: ローカル該当ファイル (`schema.py` / `compiler.py` /
  `template.py` / `tools/dsl.py` / `test_dsl_v083.py` / 例 README /
  `dsl.schema.json`) は **すべて LF only / CR=0 / 複数行** で正常確認
  (GitHub raw 表示の見え方は viewer 側の問題で、リポジトリ実体は正常)。
- `schemas/dsl.schema.json` は既に `indent=2` で pretty-print 済 (721 行)。

### P1 改修

- **`apply_template_override` を deepcopy 化**: 従来は `dict(template_plan)`
  での top-level shallow copy だったため、`steps` / `variables` / `bindings`
  内側の dict / list を expanded と template が共有していた。
  `from copy import deepcopy` で完全コピー化、template への副作用を排除。
- **`start_experiment_job_from_template` レスポンスに `owner` を明示**:
  data 直下に「実際に Job に反映された owner」を返却。
- **`override.owner` を関数引数 `owner` より優先** (動作変更):
  v0.8.3 では `override.owner` が `apply_template_override` の summary に
  入るだけで Job 起動には未反映 (黙って捨てられていた) だった。
  v0.8.3.1 から `effective_owner = override.owner or 関数引数 owner` で
  Job 起動。experimental スコープのため即時反映。
- **`docs/dsl/examples/template_override/README.md` に名称対応表追記**:
  `override.parameters` ↔ `expanded_plan.variables`, `override.owner` ↔
  `jobs.owner` (Plan には埋め込まれない) など 5 行の対応関係を明文化。
- **`$role` 推奨ルールを docs に明記**: unit / bindings を使う場合は
  `instrument: "$psu"` のように `$` prefix 必須。`"psu"` だと alias / resource
  fallback パスに乗るため、unit role 解決がスキップされる可能性がある。
- **`template_version` ≠ `template_revision` 注記**: 現状 template の
  `dsl_version` を `template_source.template_version` に流用しているが、
  本来別概念。v0.9.x で template 改訂番号を独立フィールドとして導入する
  余地を残す旨を docs に明記。

### P2 改修

- **`raw_resource_used_with_unit` を v1.0 候補メモに追加**:
  v0.8.3 では warning だが、v1.0 candidate 検討時に safety_mode=strict で
  error 昇格を保留候補として `docs/compatibility.md` に記録。

### テスト

`tests/test_dsl_v0831.py` 5 件追加 (deepcopy / steps not shared / owner 明示 /
override.owner 優先 / dry_run で DB 上 template 不変)。
**合計 465 件 passing** (v0.8.3: 460 → v0.8.3.1: 465)。

### 互換性

- 純粋追加 (`data.owner`) + experimental スコープ内の動作変更
  (`override.owner` 優先) のみ。
- Stable API は不変。

---

## v0.8.3 — DSL usability refinement (unit + template override)

合言葉:「DSL の能力を増やすのではなく、LLM が少ない指定で正しい Plan を書ける
ようにする」。新 step type / branch / loop は追加せず、**unit 直接参照** と
**template override 経由の再利用** を整備。

### 新規 MCP ツール (1 個、合計 41 → 42)

| ツール | 役割 |
|--------|------|
| `start_experiment_job_from_template` | 保存済み template に override を適用して実行 (experimental, v0.8.3) |

### `ExperimentPlan.unit` 直接対応

`bindings` を毎回書き下す代わりに、`_system.yaml` の `experiment_units` を
**unit name 一発で参照**できる。

```json
{
  "dsl_version": "0.8",
  "unit": "unit001",
  "steps": [
    { "type": "command", "instrument": "$psu",
      "command": "set_voltage", "args": {"voltage": 3.0} }
  ]
}
```

- 解決順序: **unit_bindings → explicit bindings override → alias → raw resource**
- `bindings` で同 role を上書き可能 (例: `dmm` だけ別個体に差し替え)
- `unit` 未指定の既存 Plan は完全後方互換 (動作変更なし)

### `unit_resolution` を dry-run / validate summary に常時公開

```json
{
  "unit_resolution": {
    "unit": "unit001",
    "unit_bindings": {"psu": "psu001", "dmm": "dmm001"},
    "explicit_bindings": {"dmm": "dmm_backup"},
    "effective_bindings": {"psu": "psu001", "dmm": "dmm_backup"},
    "overridden_roles": ["dmm"]
  }
}
```

- AI / 人間が「どの role がどの resource に解決されたか」をブラックボックスに
  しない設計
- v0.9.0 benchmark や v0.9.1 self-repair でそのまま使える透明性

### 新 validation error / warning

| 種別 | error_class / warning_class | 発生条件 |
|------|----------------------------|---------|
| error | `unknown_unit` | `unit` が `experiment_units` に未登録 |
| error | `unit_role_missing` | `$role` が unit / explicit のいずれにも無い |
| warning | `raw_resource_used_with_unit` | unit 指定 Plan で raw VISA resource を直接使った |

### Template override (experimental)

`save_experiment_template` で保存した template に **限定された override** を
適用して実行:

- **許可 override キー**: `name` / `unit` / `bindings` / `parameters` / `owner`
- **拒否**: `steps` / `dsl_version` / `description` / `variables` 直接上書き
  (`error_class=validation`, `details.sub_class=template_override_invalid`)
- `dry_run=True` で Job を始めずに validate + rendered_steps を返す
- `include_expanded_plan=True` で override 適用後の Plan を data に同梱

### Job metadata に `template_source` を記録

Template 経由で起動した Job は以下の両方に `template_source` が永続化される
(将来の bundle export / benchmark で重要):

- `jobs.parameters_json.template_source`
- `experiment_plans.compiled_summary_json.template_source`

```json
{
  "template_source": {
    "template_name": "voltage_sweep_basic",
    "template_version": "0.8",
    "override_json": {...},
    "override_keys": ["unit", "parameters.voltage"]
  }
}
```

### JSON Schema preview + examples

- `schemas/dsl.schema.json` を `ExperimentPlan.unit` 追加で再生成
  (preview status 維持)
- `docs/dsl/examples/unit_based_voltage_sweep/`
- `docs/dsl/examples/template_override/` (template.json / override.json /
  expected expanded plan / README)

### テスト

`tests/test_dsl_v083.py` 24 件追加 (unit 解決 / explicit override /
unknown_unit / unit_role_missing / dry-run summary / raw_resource_used_with_unit
/ template_override allowed&rejected キー / template dry_run /
template_source 永続化 / schema preview)。**合計 460 件 passing**
(v0.8.2.1: 436 → v0.8.3: 460)。

### 互換性

- `unit` は optional field、既存 Plan は無変更で通る
- `unit_resolution` は summary に純粋追加 (`unit=None` の場合も出る)
- `start_experiment_job_from_template` は **experimental** スコープ
  (`docs/compatibility.md` 参照)
- DSL schema は `dsl_version="0.8"` のまま (新フィールド追加のみ)

---

## v0.8.2.1 — Observation API レビュー対応 (P1 中心)

v0.8.2 外部レビュー指摘事項のうち P1 6 件 + P2 3 件を対応。新規 MCP ツール追加なし
(41 ツール変わらず)。互換維持 (純粋追加 / experimental スコープ内 rename のみ)。

### P1 改修

- **timeline pagination を複合 cursor に変更**: `get_experiment_timeline` の
  `pagination.next_since` (timestamp 単独) を **`next_cursor:
  {timestamp, event_id}`** に変更。同一 timestamp の複数 event 取りこぼし対策。
  v0.8.2 の `next_since` は v0.8.2.1 で削除 (preview API のため告知済み)。
- **since/until を ISO8601 datetime 比較に**: 文字列比較は timezone / ミリ秒桁
  違いで誤動作する可能性があったため、`datetime.fromisoformat` 経由で比較。
  末尾 `Z` (UTC) も `+00:00` に正規化。
- **不正 since/until は validation error**: `error_class="validation"` +
  `details.sub_class="invalid_since_timestamp"` / `invalid_until_timestamp` で
  即時拒否 (実機ノータッチ)。`docs/error_taxonomy.md` に sub_class として登録。
- **`JobManager.session_manager` public プロパティ追加**: `tools/observation.py`
  からの `job_mgr._sessions` private 依存を解消。Observation API は将来的に
  `JobStore` / `SessionManager` の public interface だけに依存する方針。
- **`latest_measurements` の resource 範囲拡張**: Map / DSL Job で
  `experiment_plans.compiled_summary.required_resources` / `used_resources` と
  `target_runs.required_resources` / `bindings` を辿り、関連 resource 全体を
  最大 32 件まで列挙。単一 Job の `rec.resource_name` のみだった v0.8.2 を改善。
- **`partial_failure` を `job_outcome` に分離** (重要): Job state machine の
  `job_status` には `partial_failure` を追加せず、Observation API の **派生値
  `job_outcome`** として算出。`compute_job_outcome(job_status, target_runs)` が
  `success / partial_failure / failure / cancelled / interrupted / null` を返し、
  `get_job_live_view` / `get_job_summary` レスポンスに `job_outcome` フィールド
  を追加。`current_phase` も `job_outcome="partial_failure"` の場合のみ
  `"partial_failure"` を返すように整理。

### P2 改修

- **`monitor_stop_condition_met` severity を info に**: 正常終了条件と安全停止
  条件を区別する payload 情報が無いため、控えめな `info` をデフォルトに変更。
- **`inspect_state` → `inspect_job_result` rename**: `recommended_next_actions`
  内の action 名と案内する tool 名 (`get_job_result`) の整合を取った。
  experimental API のため即時 rename。
- **`docs/compatibility.md` に enum 補足追加**: `job_steps.status` /
  `target_runs.status` / `job_outcome` の正式 enum を v1.0 凍結候補として明記。

### テスト

`tests/test_observation_v0821.py` 新規 14 件追加 (cursor / datetime 比較 /
session_manager / job_outcome / inspect_job_result 等)。既存テストは破壊せず。
**合計 436 件 passing** (v0.8.2: 394 → v0.8.2.1: 436)。

### 互換性

- 純粋追加 (`job_outcome` フィールド) と experimental スコープ内 rename のみ。
- `next_since` の削除は preview API としての告知通り。v0.8.2 で外部利用が始まる
  前に v0.8.2.1 で固定する判断。
- Stable API は不変。

---

## v0.8.2 — Observation API + 後方互換ポリシー草案

v0.7.0 以降で蓄積された **job_events / job_steps / target_runs / monitor_data** を、
AI エージェントと人間が「実験の流れとして読める」構造化ビューへ変換する 3 つの
read API を追加。実装方針合言葉:「低レベルログを、そのまま返さない。実験の流れ
として読める構造に変換する」。

加えて、v1.0 で API 凍結対象を明示する `docs/compatibility.md` 草案と
`error_class` taxonomy 整理 (`docs/error_taxonomy.md`) を導入。

### 新規 MCP ツール (3 個、合計 38 → 41)

| ツール | 役割 |
|--------|------|
| `get_experiment_timeline` | **何がいつ起きたか** (時系列) |
| `get_job_live_view` | **いま何が起きているか** (実行中 Job) |
| `get_job_summary` | **終了後に何が分かったか** (完了 Job) |

3 ツールは目的が異なり、既存 `get_job_status` / `get_job_result` を置き換えず
補助 read API として位置付け。

### `get_experiment_timeline(job_id, since, until, limit, kinds, include_raw)`

`job_events` を **内部 event_type → 外部 timeline kind** に正規化して返す:

- **kind enum**: `job / step / target / barrier / stagger / verify / failure /
  monitor_sample / safe_shutdown`
- **severity enum**: `info / warning / error / critical`
- 各 item に `title` / `summary` (短い 1 行説明、LLM/人間両対応)
- 任意フィールド: `target_id` / `step_index` / `step_path` / `instrument` /
  `command` / `error_class` / `recoverable` / `measurement` / `value` / `unit`
- **`monitor_sample` はデフォルト除外** (`kinds=["monitor_sample"]` 明示時のみ含む)
- pagination: `limit` (default 200, max 5000) + `next_since` (timestamp cursor)
- `include_raw=True` で元 `job_events` 行を `raw_event` に保持

### `get_job_live_view(job_id)`

実行中 Job の集約ビュー (1 tool call で「いま何が起きているか」が把握できる):

```json
{
  "current_phase": "waiting_for_stable",
  "current_activity": {
    "kind": "waiting_for_stable",
    "description": "wait_for_stable psu0.measure_voltage tol=0.1",
    "step_index": 5
  },
  "progress": {"type": "group_or_map", "total_targets": 100,
               "completed_targets": 37, ...},
  "latest_measurements": [
    {"instrument": "psu0", "measurement": "voltage", "value": 5.001,
     "unit": "V", "age_s": 0.2, "source": "measurement_cache"}
  ],
  "active_waits": [{"type": "wait_for_stable", "elapsed_s": 12.3,
                    "timeout_remaining_s": 287.7, "last_value": 25.31}],
  "active_barriers": [],
  "recent_errors": [...],
  "recent_warnings": [...]
}
```

**`current_phase` enum** (v1.0 互換保証候補):
`queued / starting / running_step / waiting / polling / waiting_for_stable /
barrier_wait / stagger_wait / monitoring / safe_shutdown / cancelling /
completed / failed / partial_failure / interrupted / unknown`

`active_waits` / `active_barriers` は `runtime.current_progress` から派生
(v0.5.1 polling 進捗 + v0.6.1 barrier 進捗の再利用)。
`latest_measurements` は `measurement_cache` を読み (実機 query を発生させない)。

### `get_job_summary(job_id)`

完了 Job の構造化要約 (`get_job_result` の補完、LLM の次判断材料):

```json
{
  "job_status": "partial_failure",
  "summary": {
    "total_steps": 120, "completed_steps": 118, "failed_steps": 2,
    "total_targets": 100, "successful_targets": 98, "failed_targets": 2,
    "duration_s": 1830.4
  },
  "verify_summary": {"total": 50, "passed": 49, "failed": 1},
  "failures": [
    {"target_id": "s057", "error_class": "timeout", "recoverable": true}
  ],
  "key_results": [...],
  "recommended_next_actions": [
    {"action": "retry_failed_targets",
     "target_ids": ["s057", ...],
     "reason": "recoverable timeout のみ"}
  ]
}
```

`recommended_next_actions` は **客観的に導ける action のみ** 提示
(「次の電圧条件を 4.5V に」のような実験条件提案は MCP 側で行わない)。

### Event normalizer (`src/visa_mcp/observation.py`)

3 つの MCP ツールが共有する内部ユーティリティ:

- `normalize_event(row)`: job_events 1 行 → timeline item
- `event_kind(event_type)` / `event_severity(event_type)`: 内部 → 外部マッピング
- `compute_current_phase(...)`: phase enum 決定ロジック
- `filter_kinds(items, kinds, default_exclude_monitor=True)`: 絞り込み
- `build_run_summary(...)`: summary 構築

### 後方互換ポリシー草案 (`docs/compatibility.md`)

v1.0 で API 凍結対象を **stable / experimental の 2 段階**で整理:

- **Stable (v1.x 互換保証)**: 中核 MCP ツール (validate/dry_run/start_experiment_job /
  get_job_status / list_resources 等) + response envelope + Job status enum +
  `error_class` taxonomy + DSL schema `dsl_version=0.8` + 機器 YAML schema +
  current_phase enum + timeline kind enum
- **Experimental (v1.x 内で変更可)**: Monitor / Observation / Template / Benchmark
  (v0.9.0) / Resume / Export / Bundle / Plugin など

### `error_class` taxonomy 整理 (`docs/error_taxonomy.md`)

5 カテゴリで `error_class` 一覧を整理 (v1.0 凍結候補):

1. **validation** (15 件): `unknown_command` / `parameter_invalid` /
   `safety_violation` / `parallel_placement` / `safe_shutdown_targets_empty` 等
2. **execution** (10+ 件): `timeout` / `verify_mismatch` / `cancelled` /
   `interrupted` / `WaitConditionTimeout` / `AsyncStepRequiresJob` 等
3. **group / map**: `partial_failure` / `target_failed` / `barrier_timeout` /
   `policy_stop`
4. **persistence**: `persistence_warning` / `persistence_error`
5. **system**: `internal` / `not_found` / `configuration_error`

各 error_class に `recoverable` 判定基準を明記。v1.0 以降は新規追加 OK、
既存意味変更・rename NG。

### スコープ外 (実装しない)

- human_intent / approval / agent_decision_log (v1.3+)
- start_experiment_job_from_template (v0.8.3)
- ExperimentPlan root の unit 直接対応 (v0.8.3)
- WebSocket / SSE push (v2.x)
- benchmark runner (v0.9.0)
- export / bundle / resume (v0.9.x / v1.0)

### テスト (19 件追加、合計 394 passed)

`tests/test_observation_v082.py`:

**必須 3 件**:
- `test_get_experiment_timeline_excludes_monitor_samples_by_default`
- `test_get_job_live_view_running_wait_for_stable` (current_phase /
  active_waits 確認)
- `test_get_job_summary_partial_failure`
  (successful_targets / failed_targets / recommended_next_actions 確認)

その他:
- normalizer 単体 (event_kind / severity / normalize_event / include_raw): 4 件
- compute_current_phase 各 enum: 5 件
- timeline kinds フィルタ / pagination: 3 件
- summary (completed_success / verify_failures): 2 件
- PHASE_ENUM / docs 存在: 3 件

### 後方互換

既存 38 MCP ツール / v0.8.1.1 DSL schema / SQLite DB / event_type 内部名は不変。
追加は新規 3 ツール + `observation.py` モジュール + 2 docs のみ。

---

## v0.8.1.1 — 外部レビュー対応 (P0/P1)

v0.8.1 公開後の外部レビューで指摘された P0 一件 + P1 三件への対応。
最重要は **`safe_shutdown.targets` 省略時の実行動作が summary と一致していなかった
不整合の修正**。

### P0

- **`safe_shutdown.targets` 省略時、実行側で `used_resources` 全件 shutdown**
  - 旧 v0.8.1: dry-run summary 上は `safe_shutdown_scope = "all_used_resources"` /
    `safe_shutdown_targets = [psu1, psu2, ...]` と読めるが、実行側は
    `required_resources[0]` の **1 台のみ** shutdown する暫定動作だった
  - 新: `compiled.safe_shutdown_targets is None and compiled.has_safe_shutdown`
    の場合に `compiled.used_resources` 全件に対して個別に
    `_best_effort_safe_shutdown` を実行
  - `safe_shutdown` 結果に `source="all_used_resources"`, 解決済み `targets[]`,
    `per_resource[]` を含める (明示 `targets` 時の構造と完全に一致)
  - **summary / rendered_steps / 実行時 result の 4 者一致** が保証される

### P1

- **`dry_run_plan` の errors[] で `recommended_next_actions` を top-level に**
  - 旧: `validate_experiment_plan` は top-level に渡していたが `dry_run_plan` は
    details に埋もれていた (AI エージェントから見て一貫性が崩れていた)
  - 新: `make_error(...)` 呼び出しに `recommended_next_actions=e.get("recommended_next_actions")`
    を明示渡し、details からは除外
- **polling 系 (`wait_for_condition` / `wait_for_stable`) の rendered_steps に
  `instrument_ref` / `args_raw` を保持**
  - 旧: 内部 query の `_validate_command` 呼び出しで `instrument_ref` / `args_raw`
    が渡されておらず、polling 系の rendered_steps では元 DSL の `$ref` が消えていた
  - 新: command/query と同様に `instrument_ref=s.instrument` /
    `args_raw=dict(s.args)` を渡す
- **`safe_shutdown` rendered step に `step_path` 追加**
  - 旧: `path` のみで、他 step 型と一貫性がなかった
  - 新: `step_path` を併記 (Observation API / Timeline で活用)

### 改行コード確認 (P2)

- ローカルファイル: 全 `.py` / `README.md` が LF only / CR=0 で正常
- GitHub raw 表示問題は外部キャッシュ起因 (実体は問題なし)

### テスト (6 件追加、合計 375 passed)

`tests/test_dsl_v0811.py`:

- `test_safe_shutdown_omitted_targets_shutdowns_all_used_resources`
  (**P0 必須**: 3 resource すべてが per_resource に含まれる)
- `test_safe_shutdown_omitted_targets_matches_summary_targets`
  (**P0 必須**: dry-run summary == used_resources == 実行時 targets の 3 者一致)
- `test_dry_run_plan_errors_have_top_level_recommended_next_actions`
- `test_wait_for_stable_rendered_includes_instrument_ref_and_args_raw`
- `test_wait_for_condition_rendered_includes_instrument_ref_and_args_raw`
- `test_safe_shutdown_rendered_step_includes_step_path`

### 後方互換

- 既存 38 MCP ツール / v0.8.1 DSL schema / SQLite DB はすべて不変
- 動作変更は **`safe_shutdown.targets` 省略時の挙動のみ** (1 台 → 全 used_resources)
- これは v0.8.1 の dry-run summary が示していた挙動への "整合化" であり、
  実害のある破壊的変更ではない (旧動作に依存していたユーザーは想定なし)

---

## v0.8.1 — DSL 安定化

v0.8.0 系 DSL を後続 (Observation / Benchmark / Export / Reproducibility) の
**土台として信用できる状態**にするリリース。新機能追加ではなく、解釈・dry-run・
実行結果の **一致性を潰す** ことに集中。実装方針 (visa_mcp_v0.8.1の実装方針.md) の
合言葉:「DSL を増やすのではなく、DSL の解釈を信用できるものにする」。

### P0: rendered_steps 構造強化 (LLM 可読性)

`CompiledPlan.rendered_steps` の各エントリに以下を追加:

| キー | 内容 |
|------|------|
| `step_path` | 階層構造内位置 (例: `steps[0].sweep[1].body[2]`) |
| `instrument_ref` | 元 DSL の参照 (`$psu` 等) |
| `resolved_resource` | 解決後 resource (`psu001`) |
| `args_raw` | テンプレ展開前 (例: `{"voltage": "{voltage}"}`) |
| `args_resolved` | 展開後 (例: `{"voltage": 3.0}`) |
| `command_type` | `"write"` / `"query"` |
| `rendered` | rendered_scpi の alias |
| `safety.mode` | 検証時の safety_mode |
| `verify.retry` | retry 回数 |

LLM が「どの DSL 行を直すか」を判断しやすくなる。既存 `path` / `instrument` /
`args` / `rendered_scpi` キーは後方互換で残す。

### P0: warnings / errors の位置情報強化

`_Context.add_error` / `add_warning` を拡張:

```python
{
  "error_class": "unknown_command",
  "message": "...",
  "step_path": "steps[3]",        # 新: 階層構造位置
  "field_path": "steps[3].targets", # 新: 該当フィールド
  "step_index": 3,
  "recommended_next_actions": [...] # 新: warnings にも対応
}
```

`safe_shutdown_targets_empty` / `parallel_inside_sweep` / `nested_parallel` /
`expanded_too_large` 等の各エラーに `recommended_next_actions` を付与。

### P0: safe_shutdown semantics 強化

- **`targets=[]` (空配列) を validation error**: 曖昧 (「全対象」「何もしない」の
  どちらか不明) なため reject、`recommended_next_actions` で
  「全対象なら targets を省略」「何もしないなら step ごと削除」を提示
- **`CompiledPlan.used_resources` を新フィールドとして導入**: Plan で使用された
  全 resource (parallel branches 含む完全集合) を別途保持。
  `safe_shutdown_targets` (= shutdown 対象) と概念上区別
- **`summary.safe_shutdown_scope`** = `"explicit"` (`targets` 指定あり) /
  `"all_used_resources"` (`targets` 省略 + safe_shutdown あり) / `"none"`
- **`summary.safe_shutdown_targets`**: scope に応じて解決済み resource list を
  必ず返す (実行時の動作を summary から完全予測可能に)

### P0: parallel 追加 validation

v0.8.0.1 で導入した「top-level 末尾 1 回のみ」に加え:

- **nested parallel 禁止** (`parallel.branches` 内の parallel を reject、
  `error_class="nested_parallel"`)
- **sweep 内 parallel 禁止** (`sweep.body` 内の parallel を reject、
  `error_class="parallel_inside_sweep"`、v1.x で再検討予定)
- **共有 resource warning** (`parallel.branches[i]` と `branches[k]` が同じ
  resource を使うと `parallel_shared_resource` warning、`merge_branches` /
  `use_distinct_resources` の recommended_next_actions を提示)

`parallel_groups[*].branch_resources` フィールドに各 branch の使用 resource list を保持。

### DSL examples (5 件、`docs/dsl/examples/`)

各 example は `plan.json` + `expected_dry_run.json` + `README.md` の構成。
LLM が「どの tool で使うか / 必要な bindings / 期待動作 / 失敗時の error_class」を
読みやすい形式:

1. `basic_voltage_set_and_measure` (command + query の最小例)
2. `voltage_sweep_with_wait` (sweep + wait + query)
3. `voltage_sweep_with_wait_for_stable` (sweep + 安定待ち)
4. `partial_failure_group_measurement` (parallel 3 branch)
5. `safe_shutdown_explicit_targets` (`targets` 明示指定)

将来 v0.9.0 の benchmark seed として再利用予定。

### JSON Schema preview 生成 (`schemas/*.schema.json`)

`scripts/generate_schemas.py` を新設、Pydantic モデルから自動生成:

- `schemas/dsl.schema.json` (ExperimentPlan)
- `schemas/instrument.schema.json` (InstrumentDefinition)
- `schemas/system_config.schema.json` (SystemConfig)

各 schema に preview status を付与:

```json
{
  "$id": "https://tectos-jp.github.io/visa-mcp/schemas/dsl.schema.preview.json",
  "x-visa-mcp-status": "preview",
  "x-compatibility": "subject-to-change-before-v1.0"
}
```

v1.0 で正式公開。preview 段階では VS Code 補完等の参考用途に限定する旨を明記。

### テスト (18 件追加、合計 369 passed)

`tests/test_dsl_v081.py`:

**実装方針必須 3 件**:
- `test_dry_run_uses_compiled_rendered_steps_without_recompile` (CompiledPlan
  から取得、再 compile 不要)
- `test_safe_shutdown_targets_match_execution_targets` (DSL ↔ CompiledPlan ↔
  rendered_steps ↔ 実行時 result の **4 者一致**)
- `test_parallel_only_allowed_at_top_level_tail` (v0.8.0.1 互換確認)

その他:
- `test_rendered_steps_include_step_path_and_resolved_args`
- `test_rendered_steps_include_safety_and_verify_summary`
- `test_warning_contains_field_path_and_recommended_next_actions`
- `test_safe_shutdown_empty_targets_rejected`
- `test_safe_shutdown_targets_default_all_used_resources`
- `test_used_resources_field_present`
- `test_nested_parallel_rejected`
- `test_parallel_inside_sweep_rejected`
- `test_parallel_shared_resource_warns`
- `test_dsl_examples_parse_as_schema` (5 examples が schema を通る)
- `test_schema_files_generated`

### 後方互換

既存 38 MCP ツール / v0.8.0.1 DSL schema / SQLite DB はすべて不変。
rendered_steps の旧キー (`path` / `instrument` / `args` / `rendered_scpi`) は維持。
新規追加は CompiledPlan の `used_resources` フィールド + 各 dict のキー追加のみ。

破壊的変更は `safe_shutdown.targets=[]` の reject のみ (v0.8.0 でも実害なく
ignored 同等扱いだったため、影響範囲は限定的)。

---

## v0.8.0.1 — 外部レビュー対応 (P0/P1)

v0.8.0 公開後の外部レビューで指摘された P0 三件 + P1 二件への対応。
DSL の設計内部整合性 (rendered_steps 正式化 / parallel placement /
safe_shutdown.targets 実行時反映) を中心に整える。

### P0

- **`CompiledPlan.rendered_steps` を正式フィールド化**
  - 旧 v0.8.0: `tools/dsl.py` の `dry_run_plan` が `_extract_rendered_from_compile`
    という private helper で `validate_and_compile` を再実行し rendered_steps を
    取得していた。tool 層が compiler の private (`_Context` / `_convert_step`)
    に依存する弱い設計
  - 新: `CompiledPlan` に `rendered_steps: list[dict]` を正式追加。
    `validate_and_compile()` が `ctx.rendered_steps` をコピーして返す。
    `dry_run_plan` は `compiled.rendered_steps` を直接利用 (再 compile なし)。
    `_extract_rendered_from_compile` は削除
- **`parallel` placement 制約を validation に追加**
  - 旧: `parallel` は plan のどこにでも置けたが、`ctx.parallel_groups` に
    分離されるため、parallel 前後の step との実行順序が曖昧になり得た
  - 新: top-level steps の **末尾に 1 つだけ許可**。それ以外なら
    `error_class="parallel_placement"` で reject。`recommended_next_actions` で
    `move_parallel_to_end` または `split_plan` を提示
  - 「parallel の前段で逐次実行したい step は branches の各 branch 先頭に複製」と
    エラーメッセージで誘導
- **`safe_shutdown.targets` を実行時に反映**
  - 旧: schema には `targets` フィールドがあったが、compile 時に `has_safe_shutdown: bool`
    のみ立てて、実行時には常に「`required_resources` の最初の resource」を shutdown
    していた。`targets` 指定が無視されていた
  - 新: `_Context.safe_shutdown_targets` で resolve_resource 経由の resource list を
    集計、`CompiledPlan.safe_shutdown_targets: list[str] | None` として保持。
    `JobManager._run_experiment_plan_job` が指定 targets ごとに個別
    `_best_effort_safe_shutdown` を呼ぶ。`safe_shutdown` 結果に
    `source="explicit_targets"`, `targets`, `per_resource` を含める
  - `targets` 未指定なら従来動作 (required_resources の最初)

### P1

- **server.py instructions に DSL 導線を追記**
  - 「複数機器・sweep・parallel を含む実験計画は `validate_experiment_plan` →
    `dry_run_plan` → `start_experiment_job` の順」を明記
  - 「いきなり `start_experiment_job` を呼ばず (a)(b) を通すことを強く推奨」
  - 「`save_experiment_template` で再利用可能」も追記
- **template 取得/list MCP ツール 2 個追加**
  - `list_experiment_templates`: name / dsl_version / description / timestamps
    のみを返す (plan JSON 本体は含まない、軽量)
  - `get_experiment_template(name)`: plan JSON 含む詳細
  - 既存 `save_experiment_template` と合わせてテンプレート再利用ループが完結

### P2 (確認のみ)

- README の改行 (ローカルは LF 正常、GitHub raw の表示問題は外部キャッシュ起因)

### 見送り (v0.8.1+)

- `ExperimentPlan` rootに `unit` 直接指定
- raw VISA resource を strict mode で禁止
- `DSLCommandStep` / `DSLQueryStep` の意味差明確化
- `delete_experiment_template` MCP ツール

### 新規 MCP ツール (2 個、合計 36 → 38)

| ツール | 用途 |
|--------|------|
| `list_experiment_templates` | テンプレート一覧 (軽量) |
| `get_experiment_template` | 指定 name の詳細 (plan JSON 含む) |

### テスト (9 件追加、合計 351 passed)

`tests/test_dsl_v0801.py`:

- `test_compiled_plan_includes_rendered_steps` (rendered_steps が
  CompiledPlan の正式フィールドに含まれる)
- `test_rendered_steps_includes_safety_and_verify`
- `test_parallel_in_middle_rejected` (中間 parallel は error)
- `test_multiple_parallel_rejected`
- `test_parallel_at_end_accepted`
- `test_safe_shutdown_targets_resolved_to_resources` (DSL targets が
  CompiledPlan.safe_shutdown_targets に resolve される)
- `test_safe_shutdown_default_is_all_used_resources` (未指定なら None)
- `test_safe_shutdown_targets_respected_at_execution` (**必須**:
  実行時に targets で指定した resource にだけ shutdown が走る)
- `test_template_get_list_through_store`

### 後方互換

- 既存 36 MCP ツール / v0.8.0 DSL schema / SQLite DB はすべて不変
- `parallel` placement の reject は曖昧パターンの reject のみ
  (top-level 末尾の単独 parallel は従来通り通る)
- `safe_shutdown.targets` 未指定時の挙動は v0.8.0 と同一

---

## v0.8.0 — Plan / Execute DSL

これまで積み上げた Job / Polling / Group / Map / Barrier / Stagger / Verify /
Persistence を、**AI エージェントが生成する実験計画 (DSL plan)** として受け取り、
**検証**し、**dry-run** し、**Job として実行**できる段階へ。

### 実装方針 (visa_mcp_v0.8.0の実装方針.md) 採用 5 点

1. **既存 IR 再利用**: DSL → validate → 既存 Plan/Step IR → 既存 Job 実行経路
2. **新 executor を作らない**: sweep は compile 時展開、parallel は GroupExecutor へ転送
3. **dry-run は実機 I/O ゼロ**: `*IDN?` / state_query / verify readback も呼ばない
4. **JSON 限定**: Python 式 / 関数呼び出し / 任意属性アクセス禁止
5. **DSL schema version**: Plan 内 `dsl_version: "0.8"`、テンプレート table にも version

### DSL 命令 10 種 (`src/visa_mcp/dsl/schema.py`)

| 命令 | 用途 |
|------|------|
| `command` / `query` | 機器命令 1 回実行 (既存 CommandStep に compile) |
| `wait` / `wait_until` | 単純秒待機 / 絶対時刻待機 |
| `wait_for_condition` / `wait_for_stable` | 条件 / 安定待機 (polling) |
| `barrier` | parallel 間または target 間同期 |
| `sweep` | 変数 sweep (compile 時に body を value 数だけ展開) |
| `parallel` | 複数 branch 並列実行 (GroupExecutor に転送) |
| `safe_shutdown` | Plan 使用 resource を best_effort 安全停止 |

**実装しない (v0.8.1+)**: branch / loop / nested parallel / quorum / resume /
emergency_stop / safe_shutdown_all / recipe step

### 上限値 (誤入力防止)

```python
MAX_SWEEP_POINTS = 200          # 1 sweep の最大展開点数
MAX_PARALLEL_CONCURRENCY = 10
MAX_PARALLEL_BRANCHES = 100
MAX_PLAN_STEPS = 500
```

例: `{"start": 0, "stop": 10, "step": 0.001}` (10001 点) は schema validation で reject。

### 新規 MCP ツール (4 個、合計 32 → 36)

| ツール | 用途 |
|--------|------|
| `validate_experiment_plan` | 構文 + resource + command + safety + verify + sweep 上限 等 15 項目検証 |
| `dry_run_plan` | rendered SCPI + safety + verify 予定 (実機 I/O 一切なし) |
| `start_experiment_job` | validate → compile → persist → Job 実行 (Group/Map 経路自動振分け) |
| `save_experiment_template` | DSL テンプレート保存 (`experiment_templates` テーブル) |

### Validator + Compiler (`src/visa_mcp/dsl/compiler.py`)

15 項目検証:

1. JSON schema (Pydantic)
2. step type (discriminated union)
3. instrument / `$role` binding / alias / resource 解決 (4 段階)
4. command 存在 + type (`query` / `write`)
5. parameter type / range 検証 (`validate_and_build_scpi`)
6. safety_mode 連携 (strict で違反 → error、advisory / permissive で warning)
7. verify 定義 (`readback_command` が query 型か確認)
8. polling_safe ヒント (polling/monitor で `polling_safe=False` の command 使用時 warning)
9. required_resources 抽出 (canonical sorted)
10. sweep 展開サイズ (>200 で reject)
11. parallel concurrency / branches 上限
12. rendered SCPI 生成 (dry-run 用)
13. resolved_instruments map ($psu → resource_name)
14. estimated_duration_s 算出
15. recommended_next_actions を errors[] に含める

返り値 `CompiledPlan`:

```python
@dataclass
class CompiledPlan:
    valid: bool
    errors: list[dict]
    warnings: list[dict]
    summary: dict        # step_count_dsl / step_count_expanded / required_resources /
                         # resolved_instruments / estimated_duration_s / uses_verify /
                         # uses_polling / has_safe_shutdown / has_parallel
    main_plan: Plan      # 既存 IR Plan に compile された結果
    parallel_groups: list[dict]   # parallel ブロックを GroupExecutor 用に分離
    has_safe_shutdown: bool
    resolved_instruments: dict[str, str]
```

### resource 解決順序

1. `plan.bindings` (`$role` 形式: `bindings: {"psu": "psu001"}`)
2. `_system.yaml` instruments alias
3. raw VISA resource ("::"を含む文字列) ← 許可するが警告

### 永続化 (SQLite `user_version=2` へ migration)

新規 2 テーブル:

```sql
experiment_plans
  plan_id / job_id / name / dsl_version /
  original_plan_json / compiled_summary_json / validation_result_json / created_at

experiment_templates
  name (PK) / dsl_version / plan_json / description / created_at / updated_at
```

`start_experiment_job` 実行時に必ず `experiment_plans` へ保存 (validation 失敗
時も含めて、何が来たか追跡可能)。

### 実行経路

```text
LLM JSON plan
  → validate_experiment_plan (dry)
  → start_experiment_job
     → validate_and_compile
     → save_experiment_plan (DB persist)
     → if parallel: GroupExecutor 経路 (既存 Map と同じ実行基盤)
       else:        recipe Plan 経路 (既存単一 Job と同じ実行基盤)
     → safe_shutdown step あれば終端時に best_effort_safe_shutdown
```

新しい executor は作らず、既存 `JobManager._run_job_inner` / `GroupExecutor` を
そのまま呼ぶ「薄い層」として実装。verify / state_query / barrier / stagger /
polling 等の既存機能はそのまま利用可能。

### スコープ外 (v0.8.1+)

- `branch` 命令 (条件分岐)
- `loop` 命令 (無制限 loop の危険)
- `recipe` step (既存 Recipe を DSL から呼び出し)
- nested parallel / quorum barrier / resume_from_step
- `safe_shutdown_all` / `emergency_stop` (中途半端な実装が危険なため v0.9.0 で慎重に)

### テスト (20 件追加、合計 342 passed)

`tests/test_dsl_v080.py`:

**必須 3 件**:
- `test_dry_run_plan_no_visa_io` (validate_and_compile で visa.write/query が 0 回)
- `test_sweep_rejects_too_many_points` (10001 点を schema_invalid で reject)
- `test_start_experiment_job_persists_original_plan` (experiment_plans テーブルに
  original_plan + compiled_summary が保存される)

その他:
- schema: dsl_version default / unknown version reject / sweep values 展開 / 排他
- validator: basic / unknown step type / unknown instrument / unknown command /
  parameter out of range (recommended_next_actions 含む)
- sweep: values list 展開 / range 展開 / 上限超過 reject
- parallel: concurrency 上限超過 reject
- safe_shutdown: marker only (IR には落ちず has_safe_shutdown=True)
- start_experiment_job: 成功時 persist / 失敗時 validation_errors persist
- experiment_templates: save / get / 上書き / list
- compiled_summary に resolved_instruments / uses_verify 反映

### 後方互換

- 既存 32 MCP ツール / v0.7.x YAML / v0.7.x SQLite DB はすべて不変
- 新規 4 MCP ツール / 2 SQLite テーブル追加のみ
- 既存 DB は自動 migration (`user_version 1 → 2`、既存テーブルに触れない)

---

## v0.7.0.1 — 外部レビュー対応 (P0/P1)

v0.7.0 公開後の外部レビューで指摘された P0 三件 + P1 二件への対応。
critical event 永続化失敗の可視化、`get_last_measurement` の副作用回避、
`monitor_data` の prune API 追加、verify の曖昧パターン拒否、`get_monitor_data`
limit 上限。

### P0

- **critical event 永続化失敗を Job result.persistence_warnings に注入**
  - 旧: `record_event` の DB 書き込み失敗は debug ログのみで握りつぶし。
    実験安全に関わる `verify_failed` / `safe_shutdown_failed` / `step_failed` /
    `job_failed` / `barrier_timeout` 等が記録できなくても Job 実行は継続するが、
    あとから「記録できなかった事実」を追えなかった
  - 新: `_CRITICAL_EVENT_TYPES` に登録された event の DB 書き込み失敗時、
    runtime に保持し最終 Job result に `persistence_warnings: [{event_type,
    target_id, step_index, error}, ...]` として注入。warn ログも出力
  - Job 実行を止める設計ではなく、**可視化のみ** (実験は止めない)
- **`get_last_measurement` の `refresh_if_stale: bool = False` 追加 (default 安全側)**
  - 旧: cache が古い or なし の時、自動的に state_query の command を実行
    (`polling_safe=False` の query を意図せず発動するリスク)
  - 新 default 動作 (`refresh_if_stale=False`): cache 古い / なし なら
    `value=None`, `stale=True` を返し、**実機 query を発生させない**。
    `refresh_if_stale=True` を明示した場合のみ再取得
  - LLM の意図しない副作用を防ぐ安全側変更
- **`monitor_data` の prune / delete API + MCP ツール `prune_monitor_data`**
  - `JobStore.delete_monitor_data(monitor_id)`: 指定 monitor の全データ削除
  - `JobStore.prune_monitor_data(older_than_days)`: 古いデータ削除
  - `JobStore.total_monitor_data_count()`: 運用監視用全行数
  - MCP ツール `prune_monitor_data` 追加 (`monitor_id` 指定 or
    `older_than_days` 指定の排他)。長期運用での DB 肥大化対策

### P1

- **verify で複数数値 args の場合 `arg_key` 必須化**
  - 旧: `set_limit(voltage, current)` のような複数数値 args + `arg_key` 未指定
    だと「最初の数値 args」を自動推定していた (dict 順序依存、曖昧)
  - 新: 数値 args が 2 以上で `arg_key` 未指定の場合、verify を
    `status=readback_failed` で拒否し、`arg_key` 明示を要求するメッセージ
  - 単一数値 args なら従来通り自動推定 (後方互換)
- **`get_monitor_data` の `limit` 上限 (10000) クランプ**
  - 旧: 大きな `limit` でも全件返す可能性
  - 新: `limit > 10000` は `10000` にクランプし、`clamp_warning` /
    `has_more` / `limit_used` フィールドを返す。`offset` と組み合わせて
    複数呼び出しで全件取得可能

### docs

- `get_last_measurement` の docstring に副作用注意 (refresh_if_stale=True
  かつ `polling_safe=False` の command で意図せず実機 query する旨) を明記

### 後回し (v0.7.1 以降)

- `state_query.map` の数値変換境界 (`1.5` のような中間値) を明文化
- `state_query` 全項目直列実行 → 並列化検討
- `verify.on_failure` フィールド (fail_step / safe_shutdown / warning)
- `JobStore` のモジュール分割 (event_store / history_store / measurement_store)
- 文字列 verify
- `locks` テーブル / audit SQLite 統合

### 新規 MCP ツール (1 個、合計 31 → 32)

| ツール | 用途 |
|--------|------|
| `prune_monitor_data` | Monitor data 削除 (monitor_id 指定 / older_than_days 指定) |

### テスト (9 件追加、合計 322 passed)

`tests/test_persistence_verify_v0701.py`:

- `test_persistence_warnings_recorded_when_critical_event_fails`
  (step_failed の DB 書き込み失敗が `result.persistence_warnings` に残る)
- `test_get_last_measurement_no_implicit_refresh` (default で実機 query が
  呼ばれない)
- `test_get_last_measurement_refresh_if_stale_true`
- `test_delete_monitor_data` / `test_prune_monitor_data_by_age`
- `test_verify_rejects_ambiguous_multi_numeric_args`
  (set_limit(voltage, current) + arg_key 未指定で reject)
- `test_verify_accepts_explicit_arg_key_with_multi_args`
- `test_verify_single_numeric_arg_auto_works`
- `test_verify_readback_must_be_query`

### 後方互換

- `get_last_measurement` の default 動作が変わるため、v0.7.0 で
  cache が古い場合に再取得を期待していた呼び出しは `refresh_if_stale=True`
  を明示する必要がある (安全側変更)
- 上記以外のすべての挙動 / 32 MCP ツール / YAML / SQLite schema は不変

---

## v0.7.0 — Persistence + self-awareness + verify

v0.5 / v0.6 系で固めた「動く」実装の上に、**追跡できる・検証できる・状態を
見られる**層を追加。実験実行基盤としての観察性・検証性・永続性が一段上がる。

### 実装方針 (visa_mcp_v0.7.0の実装方針.md) 採用 3 本柱

1. **Persistence**: 実験実行履歴を SQLite に残す
2. **Self-awareness**: 機器の現在状態を構造化して取得する
3. **Verify**: write 後に read-back して実機反映を検証する

### SQLite schema 拡張 (PRAGMA user_version=1)

既存 v0.5.x の `jobs` テーブルは保持し、新規 5 テーブルを **非破壊的に追加**:

```sql
job_steps           -- step 単位の実行履歴 (target_id 対応)
target_runs         -- Group/Map の target 単位集約
job_events          -- 時系列イベント (barrier/stagger/poll/cancel/verify/...)
measurement_cache   -- 最新測定値キャッシュ (上書き、instrument+measurement 主キー)
monitor_data        -- monitor jobs の時系列データ
```

migration は `_apply_migrations()` が `PRAGMA user_version` を見て自動実行。
既存 DB は `user_version=0` から `1` に上がり、jobs テーブルのデータは保持される。

### 新規 MCP ツール (6 個、合計 25 → 31)

| ツール | 用途 |
|--------|------|
| `describe_instrument` | 機器の能力サマリ (capabilities / state_keys / recommended_usage) を構造化 JSON で |
| `get_state` | `state_query` 定義に従い機器状態取得。`keys` 絞り込み / `max_age_s` cache 受容 |
| `get_last_measurement` | `measurement_cache` から最新値、age > max_age_s なら自動再取得 |
| `start_monitor` | 定期測定 Monitor Job (`monitor_data` 保存、`stop_condition_expr` 対応) |
| `stop_monitor` | Monitor Job 停止 (cancel_job alias) |
| `get_monitor_data` | 時系列データ取得 (limit/offset、`get_job_result` から分離して大量データ対応) |

### YAML 拡張

**`commands.<name>.verify:`** (write 系 command の read-back 検証):

```yaml
commands:
  set_voltage:
    scpi: "VOLT {voltage}"
    type: write
    verify:
      readback_command: measure_voltage
      tolerance: 0.05
      retry: 1
      delay_s: 0.2
```

**`state_query:`** (機器状態 query 定義):

```yaml
state_query:
  voltage:
    command: measure_voltage
    unit: V
  output:
    command: query_output
    map:
      "1": "ON"
      "0": "OFF"
```

### verify (write 後 read-back) ロジック

write 直後に `readback_command` を実行し、write の `args[*]` (または
`arg_key`) と比較。`abs(actual - expected) <= tolerance` で verified。
不一致なら `retry` 回まで再 read-back。

**safety_mode との連携**:
- `strict`: verify 失敗 → step failed (`error=VerifyMismatch`)
- `advisory`: step success だが `verified=False` を結果に含める
- `permissive`: log のみ

step result には常に `verify` フィールドが追加される:

```json
{
  "command": "set_voltage", "success": true, "verified": true,
  "verify": {
    "readback_command": "measure_voltage",
    "expected": 5.0, "actual": 5.001, "tolerance": 0.05,
    "attempts": 1, "status": "ok"
  }
}
```

### state_query 実行 (`src/visa_mcp/state_query.py`)

`query_state_item` / `query_all_state` を新設。`get_state` / `get_last_measurement` /
`describe_instrument` 内で共通利用。`value_path` / `map` 対応。

### executor hook (job_events 記録)

Recipe / Group / Map Job 実行で下記イベントを `job_events` に時系列保存:

- `job_started` / `job_completed` / `job_failed` (既に transition_status で記録、
  追加で payload を保存)
- `step_started` / `step_completed` / `step_failed` (job_steps テーブルにも
  詳細レコード)
- `target_started` / `target_completed` / `target_failed`
  (`target_runs` テーブル UPSERT、Group/Map で targets 全てに対し)
- `monitor_stop_condition_met`

`record_event` / `record_step_started` / `record_step_completed` /
`upsert_target_run` API を `JobStore` に追加。例外は握りつぶす (永続化失敗で
Job 実行を止めない設計)。

### Monitor Job (`start_monitor_job`)

`JobManager.start_monitor_job` を新設。polling core (`_do_one_poll`) を再利用
しつつ、`MonitorJobExecutor` 経路で:

- `interval_s >= 1.0` / `duration_s <= 86400` (24h) / 最大 10 万サンプル制限
- 各 poll の値を `monitor_data` に時系列保存
- `stop_condition_expr` で条件成立による早期終了
- cancel_job 対応 (slice sleep で即応)
- `runtime.current_progress` に `samples / elapsed_s / remaining_s / last_value`

Monitor Job も Job 系として扱われるため、`get_job_status` / `cancel_job` /
`list_jobs` がそのまま使える。時系列データは `get_monitor_data` で別取得。

### スコープ外 (v0.7.1 以降)

- 文字列 verify (現状は数値のみ)
- audit log の完全 SQLite 統合 (現状は既存 `audit.log` に並行記録)
- `locks` テーブル
- target 単位 resource acquire/release への移行
  (引き続き親 Map Job 全体一括 lock、v0.7.0 では現行設計を維持)
- backend-independent コード分離 (docs 明記のみ)
- monitor の retention policy (現状は手動で削除)
- `cancel_running_on_policy_stop` / `retry_safe_shutdown_before_retry`
  (v0.6.0.1 から引き続き予約)

### テスト (16 件追加、合計 313 passed)

`tests/test_persistence_verify_v070.py`:

**必須 3 件**:
- `test_schema_migration_from_v050` (v0.5.x DB を v0.7.0 起動で migration、
  既存 jobs データ保持 + 新規 5 テーブル作成)
- `test_verify_numeric_mismatch_strict_fails_step` (strict mode で verify 失敗
  時に `error=VerifyMismatch` で step failed)
- `test_job_events_record_step_and_target` (Recipe Job で job_events に
  step_started / step_completed が記録される、job_steps テーブルにも entry)

その他:
- `test_schema_init_fresh_db`
- `test_target_runs_recorded_for_map_job`
- `test_verify_numeric_success` / `test_verify_numeric_mismatch_advisory_warns_only`
- `test_query_state_item_basic` / `test_query_state_item_with_map` /
  `test_query_all_state`
- `test_measurement_cache_upsert_and_get`
- `test_monitor_records_data` / `test_monitor_stop_condition` /
  `test_monitor_cancel`
- `test_monitor_interval_validation` / `test_monitor_duration_validation`

### 後方互換

既存 25 MCP ツール / v0.6.1.1 YAML / v0.5.x SQLite DB はすべて不変。
新規追加は YAML の **オプション** (`verify` / `state_query`) と新規ツールのみ。
v0.5.x の DB を持つユーザーは初回起動時に自動 migration される (jobs データ
は保持)。

---

## v0.6.1.1 — 外部レビュー対応 (P0/P1)

v0.6.1 公開後の外部レビューで指摘された P0 二件 + P1 数件への対応。
コード本体の機能は v0.6.1 で完成しているが、**barrier abort 後の振る舞いの
明示化** と **stagger progress の公開** を追加。

### P0

- **abort 済み barrier への late arrival を即失敗化 (実装)**
  - 旧 v0.6.1: `BarrierCoordinator.arrive()` は `state.aborted_reason` の事前
    チェックを行わず、abort 後でも一度 `mark_arrived` してから wait に入る
    実装だった。実害は無いが冗長で、最悪のケースで余分な slice wait が走る
    可能性
  - 新: `arrive()` 入口で `state.aborted_reason` をチェックし、既に abort 済みなら
    新たな wait に入らず即座に
    `{"success": False, "error": aborted_reason, "late_arrival": True}`
    で return
- **barrier timeout 後に後続 step が実行されないことの明示テスト追加**
  - 既存実装で挙動は正しかったが、テストとして明示化していなかった
  - `test_barrier_timeout_prevents_later_steps`: t0 が barrier で timeout failed
    した後、その target の後続 `set_output` step が **visa.write に届かない**
    ことを確認

### P1

- **stagger 中の progress 公開**
  - GroupExecutor 内に `stagger_tracker: dict[target_id, dict]` を追加
  - 各 target が stagger sleep 中に登録 / 完了時に削除
  - `_emit_progress` で集約し、`data.progress.stagger` に
    `step_index / command / stagger_ms / in_stagger_count / next_target_id /
    next_start_in_s` を含める
  - 100 台 × 100ms stagger 等の長い stagger 中にエージェントが「順次起動中」と
    判断できるようになる
- **BarrierStep の `timeout_s` docstring 修正**
  - 旧: 「timeout_s 必須、無限待ち禁止」(省略可能だが省略時 default 60s なので
    表現が曖昧)
  - 新: 「**必ず有限値を持つ**。省略時 default=60s で無限待ちは禁止」
- **BarrierCoordinator docstring に barrier 対象 target 決定規則を明文化**
  - participants は arrive 時点の non-excluded set スナップショット
  - barrier を持たない target が混在すると waiting_for に残り続け timeout で
    abort される
  - 推奨: 同一 Map Job 内の全 target が同じ barrier_key 集合を持つ
  - v0.6.1.1 では validation を行わない (運用ルール)
- **MCP tool schema に予約フィールド明記**
  - `start_map_recipe_job` の `failure_policy` docstring に
    `cancel_running_on_policy_stop` / `retry_safe_shutdown_before_retry`
    が `reserved` であることを明示 (入力されても無視される)

### スコープ外 (v0.7.0+)

- `cancel_running_on_policy_stop` の実装 (v0.6.0.1 から引き続き予約)
- `retry_safe_shutdown_before_retry` の実装
- barrier/stagger イベントの SQLite 永続化 (v0.7.0 で `barrier_events` / 等を追加予定)
- Map Job 全体一括 lock → target 単位 acquire/release への移行 (v0.7.0)

### テスト (4 件追加、合計 297 passed)

`tests/test_barrier_stagger_v0611.py`:

- `test_barrier_timeout_prevents_later_steps` (P0: visa.write 未呼出確認)
- `test_late_arrival_after_barrier_timeout_fails_immediately`
  (P0: timeout 後の late arrival が 0.2s 以内に return)
- `test_late_arrival_after_barrier_cancel` (cancel 後の late arrival)
- `test_stagger_progress_includes_next_target_id`
  (P1: progress.stagger に next_target_id / next_start_in_s 含む)

### 後方互換

既存 25 MCP ツール / v0.6.1 YAML / v0.4.x recipe はすべて不変。
abort 後の late arrival は **元々 wait 開始してから即 abort され同じ結果を
返していた**ため、エージェント視点では `late_arrival: True` フィールドが
追加されただけの差分。

---

## v0.6.1 — Barrier / Stagger

v0.6.0 / v0.6.0.1 で固めた Group/Map MVP の上に、**target 間同期点 (barrier)** と
**意図的な順次起動 (stagger)** を追加。100 台の電源を `OUTP ON` するときの
突入電流を避ける、複数 target が同じ設定完了を待ち合わせてから次に進む、
といった実験パターンが表現できるようになる。

### 実装方針 (visa_mcp_v0.6.1の実装方針.md) 採用 5 点

1. Barrier は **Group/Map executor の同期機構** (target-local Plan step ではなく)
2. Barrier 待ち中は **target-level resource lock を解放** (deadlock 回避)
3. `failure_policy=continue` では失敗 target を **barrier 対象から自動除外**
4. Stagger は **特定 step 開始** に適用 (`CommandStep.stagger_ms`)
5. progress に `barrier_name / arrived / waiting_for / total_expected` 等

### 新規 IR

**`BarrierStep`** (`src/visa_mcp/experiment_ir/step.py`):

```python
class BarrierStep(BaseModel):
    type: Literal["barrier"] = "barrier"
    name: str
    timeout_s: float = 60.0
    description: str = ""
```

`barrier_key = (name, step_index)` で識別。同 name でも step_index 違いは別物。
timeout_s 必須 (無限待ち禁止)。

**`CommandStep.stagger_ms`** (`int | None`):

```yaml
steps:
  - command: set_output
    args: { state: "ON" }
    stagger_ms: 100      # target_index × 100ms ずつずらして起動
```

target_index は GroupExecutor が入力順 (0-indexed) で割り当て。
0 〜 600,000ms (10 分) の範囲。

### YAML 拡張 (RecipeStep)

```yaml
recipes:
  synchronized_output_on:
    parameters: []
    steps:
      - command: set_voltage
        instrument: "$psu"
        args: { voltage: 5 }
      - barrier:
          name: all_voltage_set
          timeout_s: 60
      - command: set_output
        instrument: "$psu"
        args: { state: "ON" }
        stagger_ms: 100
```

`barrier` フィールドは必須 `name` と任意 `timeout_s` を持つ。
既存 5 種 step フィールド (`command/wait/wait_until/wait_for_condition/wait_for_stable`)
に加えて `barrier` が 6 番目の排他オプションになる。

### BarrierCoordinator (新規)

`src/visa_mcp/group/barrier.py`:

```python
coord = BarrierCoordinator()
coord.register_targets(["t1", "t2", "t3"])
# target 失敗時に呼ぶ → 残り participants で barrier 成立可能に
coord.exclude_target("t2")
# 各 target が arrive で待機
result = await coord.arrive("b1", step_index=2, target_id="t1",
                              timeout_s=60.0, cancel_check=...)
```

- `(name, step_index)` で barrier を識別
- arrive 時点で対象 target 数を確定 (excluded を除く)
- slice 方式 wait (cancel/timeout に即応)
- `current_barrier_progress()` で active barrier 状態を返す

### GroupExecutor 統合

barrier 待ち中の deadlock 回避処理:

```python
# barrier 到達直前
for lk in acquired_locks:
    lk.release()
try:
    await barrier_coord.arrive(...)
finally:
    # canonical sorted 順で再取得
    for lk in acquired_locks:
        await lk.acquire()
```

これにより `target1 が lock を持ったまま target2 を待つ` deadlock が起きない。
親 Job 全体 lock があるので外部 Job からは触られない (現状 v0.6.0 設計と整合)。

target が失敗 / cancelled になると、自動的に
`barrier_coord.exclude_target(target_id)` が呼ばれ、残り target で barrier 成立可能に。

### Stagger 実装

CommandStep 実行直前に:

```python
stagger_s = step.stagger_ms / 1000.0 * target.target_index
if stagger_s > 0:
    # slice 方式 sleep (POLL_SLEEP_SLICE_S=0.2s で cancel 即応)
    while remaining > 0:
        if cancel_check(): return cancelled
        await asyncio.sleep(min(remaining, 0.2))
        remaining -= 0.2
```

target_index は GroupExecutor.run() 内で `enumerate(targets)` により入力順で
0..N-1 に確定。`asyncio.as_completed()` の完了順ではなく必ず入力順で stagger される。

### `execute_recipe` で barrier を含む recipe を reject

v0.5.1.1 で polling 系を拒否したのと同様、`barrier` を含む recipe を同期
`execute_recipe` で実行しようとすると `AsyncStepRequiresJob` を返す。
誘導先は **`start_map_recipe_job`** (barrier は target 間同期なので Map Job 必須)。

### progress 公開

GroupExecutor の `on_progress` callback が、active barrier がある間は
`data.progress` に barrier 情報を含める:

```json
{
  "type": "group_or_map",
  "total": 100, "completed": 18, "running": 10, ...,
  "barrier": {
    "type": "barrier",
    "barrier_name": "all_voltage_set",
    "step_index": 1,
    "arrived": 97,
    "total_expected": 98,
    "waiting_for": ["t023", "t057"],
    "elapsed_s": 12.4
  }
}
```

エージェントは「barrier all_voltage_set で 97/98 到達、t023 と t057 を待ち中」と判断可能。

### スコープ外 (将来バージョン)

- quorum barrier (`% 以上到達で進む`)
- nested barrier / branch/loop 内 barrier
- distributed multi-server barrier
- target 永続化 / barrier resume
- dynamic stagger 調整
- `cancel_running_on_policy_stop` の実装 (v0.6.0.1 から引き続き予約フィールド)
- Map Job 全体一括 lock → target 単位 acquire/release (v0.7.0)

### テスト (15 件追加、合計 293 passed)

`tests/test_barrier_stagger_v061.py`:

- IR validation (name 非空、timeout_s 正、stagger_ms 範囲)
- BarrierCoordinator (全到達 / timeout / exclude / cancel)
- **必須 3 件**:
  - `test_barrier_does_not_hold_target_resource_lock_deadlock` (同 resource を共有する
    2 target が barrier で deadlock しないこと)
  - `test_stagger_starts_targets_in_input_order` (resource 完了順ではなく入力順)
  - `test_partial_failure_with_barrier_continue` (失敗 target が barrier 除外され
    残り target で成立)
- `test_stagger_zero_means_no_delay`
- `test_barrier_timeout_in_executor`
- `test_stagger_respects_cancel`
- `test_get_job_status_reports_barrier_progress`
- `test_execute_recipe_rejects_barrier_step`

### 後方互換

既存 25 MCP ツール / v0.6.0.1 YAML / v0.4.x recipe はすべて不変。
v0.6.0.1 で動いていたパターンは v0.6.1 でも完全に動く。
新規追加は `RecipeStep.barrier` / `RecipeStep.stagger_ms` / `BarrierStep` /
`CommandStep.stagger_ms` の **オプション追加** のみ。

---

## v0.6.0.1 — 外部レビュー対応 (P0/P1)

v0.6.0 公開後の外部レビューで指摘された **同一 Map Job 内部の target 間 resource 競合**
を含む P0 三件 + P1 数件への対応。

### P0

- **同一 Map Job 内部の target 間 resource lock を追加 (最重要)**
  - 旧 v0.6.0: 親 Map Job が全 resource を `ResourceScheduler` で一括占有していたが、
    Job 内部の target 同士で同じ resource を共有するケース (例: 設定ミスで
    sample001/sample002 が両方とも psu001 を使う) では、`wait` 中に target2 が
    target1 の電圧設定を上書きする恐れがあった。VisaManager の resource lock は
    I/O 単位の逐次化のみで、target 全体としての条件保持は守れていなかった。
  - 新: `GroupExecutor` 内に `_target_locks: dict[str, asyncio.Lock]` を新設。
    target 実行直前に `required_resources` を canonical sorted 順で acquire し、
    target 終了時に release。これにより:
    - Job 間競合 = ResourceScheduler が担う
    - Job 内 target 間競合 = GroupExecutor の target-level lock が担う
    の二段構成が完成。canonical sorted 取得で deadlock 回避。
- **テスト名のズレを修正**
  - 旧 `test_resource_lock_prevents_shared_resource_targets_from_overlapping` →
    新 `test_bus_semaphore_serializes_io_on_same_bus` (実態は BusManager の I/O 逐次化)
  - 新規 `test_shared_resource_targets_serialized_during_wait` を追加:
    psu001 を共有する 2 target で `set_voltage → wait 0.15s → measure_voltage` を
    concurrency=2 で実行し、target ごとの開始-終了区間が overlap しないことを確認
  - 対照: `test_disjoint_resource_targets_run_in_parallel` で異なる resource は
    並列実行されることを確認 (lock が壊れていない確認)

### P1

- **`primary_role` を複数 bindings 時は必須化**
  - 旧: bindings が複数 role を持つ場合、最初の binding を primary と推定
    していたが、エージェントが渡す JSON の dict 順序は曖昧で、温度計が
    primary になる等の事故の恐れがあった
  - 新: bindings が 2 以上の role を持つ場合は `primary_role` 未指定で
    validation error。単一 role なら従来通り自動推定
- **`start_group_query_job` で write 系 command を拒否**
  - 旧: command type が write でも素通り (名前と挙動がずれる)
  - 新: target 構築時に各 member の `command.type` を確認し、`query` 以外は
    validation error。メッセージで `start_map_recipe_job` への誘導も含む。
    member 機器が未識別ならその時点で `not_found` エラー (より早い検出)
- **`cancel_running_on_policy_stop` を予約フィールドとして明記**
  - `FailurePolicy` の docstring で「v0.6.0.1 では未実装。stop_requested は
    未開始 target を skipped にするのみ。実行中 target の強制 cancel は
    v0.6.1 で `policy_cancel_requested` 経路として追加予定」と明記
- **BusManager.set_system_config の reload 仕様明記**
  - 既存 semaphore は保持されるため、reload 後の `max_concurrency` 変更は
    既存 bus 名のセマフォには反映されない (要サーバ再起動)。docstring で明示

### P0/P1 で見送った項目 (v0.7.0 以降)

- **Map Job 全体の resource 一括 lock → target 単位 lock への移行**
  - 現状 v0.6.0.1 は親 Job が全 target の全 resource を ResourceScheduler に
    渡す保守的設計。`concurrency=10` で 100 targets でも 100 resource を確保
  - スケール性は劣るが安全性は高い。target_runs テーブル等の永続化と合わせて
    v0.7.0 で target 単位 acquire/release に変更予定
- **`stop_on_first_error` で全 task 先起動 → worker pool 化**: v0.6.0.1 では
  100 targets 程度なら問題なし。1000+ で worker pool 検討
- **safe_eval_condition の `**` 禁止 / AST 上限**: v0.5.1.1 から引き続き保留

### テスト (6 件追加、合計 278 passed)

`tests/test_group_map_v0601.py`:
- `test_shared_resource_targets_serialized_during_wait` (**P0 最重要**)
- `test_disjoint_resource_targets_run_in_parallel`
- `test_map_recipe_requires_primary_role_when_multiple_bindings`
- `test_map_recipe_single_binding_auto_primary`
- `test_start_group_query_job_rejects_write_command`
- `test_start_group_query_job_accepts_query_command`

### 後方互換

既存 25 MCP ツール / v0.6.0 YAML はすべて不変。v0.6.0 で「動いていた」
パターン (target が disjoint resource で primary_role 明示 or 単一 binding) は
v0.6.0.1 でも動く。primary_role 必須化は明らかな曖昧パターンの reject のみ。

---

## v0.6.0 — Group / Map MVP

v0.5 系の「単一 Job を安全に長時間実行する」段階から、
**複数 resource を含む Job を並列にスケジューリングする** 段階へ移行。

100 台規模の機器を 1 ツール呼び出しで操作できる Group / Map 基盤を導入。
LLM が `start_map_recipe_job` 1 回で 100 サンプルの実験を投入できるようになる。

### 新規 YAML 設定 (`instruments/_system.yaml`)

per-instrument YAML とは独立した、システム全体のトポロジ定義ファイル。
ファイルが存在しなくてもサーバは起動する (v0.5 系完全互換)。

```yaml
instruments:               # alias ↔ VISA resource_name + bus 帰属
  psu001:
    resource: "GPIB0::6::INSTR"
    bus: "GPIB0"
  temp001:
    resource: "GPIB0::1::INSTR"

buses:                     # バス単位の同時アクセス制限
  GPIB0:
    max_concurrency: 1     # GPIB は default 1

instrument_groups:         # 同種機器の集合 (query_group 対象)
  temp_meters:
    members: [temp001, temp002, ...]

experiment_units:          # 1 実験対象の機器セット (map_recipe 対象)
  unit001:
    psu: psu001
    temp: temp001
```

`SystemConfig` Pydantic モデル + `from_yaml()` ローダーで読み込む
(`src/visa_mcp/system_config.py`)。サンプル `instruments/_system.example.yaml` 同梱。

### 新規 MCP ツール (4 個、合計 21 → 25)

| ツール | 用途 |
|--------|------|
| `list_groups` | `instrument_groups` 一覧 |
| `list_experiment_units` | `experiment_units` 一覧 |
| `start_group_query_job` | グループ全機器に同じ query を並列 |
| `start_map_recipe_job` | 異なる条件で各 unit に recipe 並列実行 |

`get_group_status` / `execute_group_recipe` は新設せず、それぞれ
`get_job_status.data.progress` / `start_map_recipe_job (同 parameters)` で代用。

### `start_map_recipe_job` の入力仕様

```json
{
  "recipe": "iv_point",
  "targets": [
    {
      "target_id": "sample001",
      "unit": "unit001",
      "bindings": {"psu": "psu001_alt"},
      "parameters": {"voltage": 1.0}
    },
    {
      "target_id": "sample002",
      "unit": "unit002",
      "parameters": {"voltage": 1.5}
    }
  ],
  "concurrency": 10,
  "failure_policy": {"mode": "continue", "retry": 2},
  "primary_role": "psu"
}
```

`target_id` / `unit` / `bindings` / `parameters` の 4 フィールド分離は
v0.8.0 Experiment DSL への自然な拡張を見据えた設計。

### `CommandStep.instrument` 再導入 (logical ref)

```yaml
recipes:
  iv_point:
    steps:
      - instrument: "$psu"
        command: "set_voltage"
        args: { voltage: "$voltage" }
      - wait: { seconds: "$wait_s" }
      - instrument: "$temp"
        command: "measure_temperature"
```

`$psu` は `target.bindings["psu"]` 経由で実 resource_name に解決される。
省略時は target の単一 resource (legacy 動作) を使う。
v0.5.1.1 で削除した dead field を、map_recipe の bindings 機構と
組み合わせて意味のあるフィールドとして復活。

### `Plan.required_resources` の集約

map_recipe の各 target は、`plan.required_resources + bindings 全 resources` を
canonical sorted で持つ。ResourceScheduler に渡すことで、同じ resource を共有する
複数 target が同時実行されないことを保証。

### partial_failure を正常系として扱う

100 台中 2 台 timeout でも、98 台分の成功結果と 2 台の `errors[]` を両方返す。

```json
{
  "status": "partial_failure",
  "summary": {"total": 100, "success": 98, "failed": 2, "skipped": 0, "retried": 3},
  "results": [
    {"target_id": "sample001", "status": "ok", "data": {...}},
    ...
  ],
  "errors": [
    {"target_id": "sample057", "error_class": "timeout", "recoverable": true}
  ]
}
```

エージェントが「失敗した 2 台だけ retry」を判断できる。

### `failure_policy`

```yaml
failure_policy:
  mode: "continue" | "stop_on_first_error" | "stop_if_failure_rate_exceeds"
  retry: 2                                  # target 全体 retry (step 部分 retry なし)
  stop_if_failure_rate_exceeds: 0.5
```

- `continue`: 失敗を記録、他 target は継続
- `stop_on_first_error`: 最初の失敗で未開始 target を skipped に
- `stop_if_failure_rate_exceeds`: 失敗率閾値超過で未開始 target を skipped

### BusManager (新規)

`src/visa_mcp/bus_manager.py`:

- bus 単位 `asyncio.Semaphore` (lazy 生成)
- VisaManager の query/write で **VISA 通信中のみ** acquire
  (Job 全体ではなく、GPIB を 60 秒 wait で塞がない設計)
- GPIB は default `max_concurrency=1` (resource_name から自動推定)
- ResourceScheduler とは独立
  (deadlock 回避: Job lock → bus semaphore → resource lock の固定順序)

### GroupExecutor (新規、共通 executor)

`src/visa_mcp/group/executor.py`:

`query_group` と `map_recipe` を内部で同一 IR (`TargetExecution`) に集約。
concurrency / failure_policy / partial_failure aggregation / retry / cancel /
stable result order を共通実装。

### get_job_status に group/map 進捗

```json
{
  "data": {
    "status": "running",
    "progress": {
      "type": "group_or_map",
      "total": 100, "queued": 70, "running": 10, "completed": 18,
      "failed": 2, "skipped": 0, "retrying": 0
    }
  }
}
```

v0.5.1 の `data.polling` と同じ `runtime.current_progress` 経由で公開。
type で振り分けて `data.progress` か `data.polling` のどちらかに格納。

### 実装方針 (visa_mcp_v0.6.0の実装方針.md) で採用した核心 5 点

1. Group / Map 系は全て **Job として実行** (同期ツール無し)
2. `experiment_units` を `map_recipe` の中心概念
3. resource lock = Job/target 全体、**bus semaphore = VISA 通信中のみ**
4. `partial_failure` は正常系
5. Map Job = **親 Job 1 つ** (子 Job 作らず、案 A 採用)

### スコープ外 (将来バージョン)

- targets を子 Job として永続化 (v0.7.0)
- target 単位 resume (v0.9.0+)
- throughput 最適化 scheduler / queue 追い越し / dynamic load balancing
- branch / loop / barrier / stagger (v0.6.1 / v0.8.0)
- retry_safe_shutdown_before_retry (予約フィールドのみ実装)

### テスト (20 件追加、合計 271 passed)

`tests/test_group_map_v060.py`:

- SystemConfig ローダー (yaml / GPIB 自動推定 / 欠落ファイル)
- Resolver ($role / alias / direct resource / canonical sort)
- BusManager (GPIB default 1 / 不明 bus 素通し)
- GroupExecutor (全成功 / partial_failure continue / stop_on_first_error / retry)
- JobManager (group_query 結果順序 / 未知 group / map_recipe with bindings)
- **必須 3 件**:
  - `test_resource_lock_prevents_shared_resource_targets_from_overlapping`
  - `test_bus_manager_gpib_default_concurrency_1`
  - `test_group_executor_partial_failure_continue`

### 後方互換

既存 21 MCP ツール / v0.5.1.1 YAML / v0.4.x recipe はすべて不変。
`_system.yaml` 無しでも v0.5.1.1 と完全に同じ挙動。

---

## v0.5.1.1 — 外部レビュー対応 (P0/P1)

v0.5.1 公開後の外部レビューで指摘された P0 二件 + P1 四件への対応。
コード本体の機能は v0.5.1 で完成しているが、エージェント向け UX と
API 正確性を整える。

### P0

- **`wait_for_condition` 側にも `polling_safe_warning` を入れる**
  - 旧: `wait_for_stable` のみ `polling_safe=False` を警告していた
  - 新: `wait_for_condition` も対象 command の `polling_safe` を確認し、
    progress と結果 dict 双方に `polling_safe_warning` を含める
  - 副作用のある `READ?` / `MEAS?` を condition で繰り返し呼ぶリスクは
    stable と同等なため、警告対象も揃える
- **同期 `execute_recipe` で polling step を踏んだら `AsyncStepRequiresJob`**
  - 旧: `UnsupportedStepType` で内容が分からないまま失敗
  - 新: polling 系 step (`wait_until` / `wait_for_condition` / `wait_for_stable`)
    を含む recipe を `execute_recipe` で実行しようとした場合、即座に明確な
    誘導エラーを返す
    ```json
    {
      "success": false,
      "error": "AsyncStepRequiresJob",
      "message": "... execute_recipe では実行できません。start_recipe_job を使ってください ...",
      "recommended_action": { "tool": "start_recipe_job", "args": {...} }
    }
    ```
  - 実機 write は 1 つも実行されずに即時 reject (副作用なし)

### P1

- **`wait_until` の naive timestamp を拒否**
  - 旧: timezone 無し timestamp を UTC として扱う (日本時間 15:00 を渡したつもりが
    UTC 15:00 として扱われる事故の恐れ)
  - 新: `TimezoneRequired` エラーで拒否、`+09:00` 形式または `Z` 形式での
    明示指定を要求するメッセージ
  - 実験現場のヒューマンエラー (時差ミス) を防止
- **`start_wait_job` の `params: dict = {}` を None default に**
  - mutable default 引数の慣用回避
- **`sample_count` を `poll_count` / `valid_sample_count` / `consecutive_errors` に分離**
  - 旧: 「poll 試行数」と「有効サンプル数」が混在していた
  - 新:
    - `poll_count`: 試行数 (エラー含む)
    - `valid_sample_count`: 有効な数値を得た成功 poll 数
    - `consecutive_errors`: 現在の連続失敗数
  - `wait_for_stable` の安定判定に使われる有効サンプル数が明示的に分かる
  - **後方互換**: 旧 `samples_taken` / `sample_count` キーは v0.5.1.1 で削除
    (v0.5.1 リリース後 1 日以内のため影響範囲は限定的)

### 検討して見送った項目

- **`safe_eval_condition` の `**` 禁止 / AST 上限**: v0.6.0 前に再検討
  (現状 `condition_expr` は LLM が直接生成しないため緊急度低)
- **`polling_safe` を strict モードで block**: 既存 YAML を破壊するため
  v0.6.0 で `state_query` と合わせて整理

### テスト

`tests/test_polling_wait_v0511.py` に 6 件追加 (合計 **251 passed**)。

- `test_wait_for_condition_emits_polling_safe_warning`
- `test_wait_for_condition_no_warning_when_polling_safe`
- `test_execute_recipe_rejects_polling_step` (実機 write 呼ばれない確認込み)
- `test_wait_until_rejects_naive_timestamp`
- `test_wait_until_accepts_tz_aware_timestamp_already_passed`
- `test_poll_count_and_valid_sample_count_differ_on_errors`

---

## v0.5.1 — Polling wait (条件待機 / 安定待機 / 絶対時刻待機) + start_wait_job

v0.5.0 系で導入した Job MVP を、**条件待機**できるレベルへ拡張。
温度が安定するまで待つ、ある電圧を超えたら次へ進む、といった「実験で頻発する待ち」を
LLM 側がブロックせずに表現できるようになる。

### 新規 Step 型 (内部 IR)

`src/visa_mcp/experiment_ir/step.py` に discriminated union として追加:

- **`WaitUntilStep`** ── ISO8601 絶対時刻 / `seconds_from_now` 相対秒数まで待機
- **`WaitForConditionStep`** ── `condition_expr` が True を返すまで polling
  - 例: `"value > 80"`、`"abs(value - 25) < 0.2"`、`"value < 10 or value > 20"`
- **`WaitForStableStep`** ── 測定値の `max - min <= tolerance` (window_s 内) になるまで polling

すべて `interval_s` / `timeout_s` / `command_timeout_s` の **3 層タイムアウト**、
`retry_on_error` / `max_consecutive_errors` (デフォルト 3) の error policy、
`value_path` による測定値抽出ヒントを持つ。

### YAML / Recipe 拡張

`RecipeStep` (`models/instrument_def.py`) で下記キーを認識するようになった:

```yaml
recipes:
  voltage_then_stable_temp:
    parameters: []
    steps:
      - { command: "set_voltage", args: { voltage: 5 } }
      - wait_for_stable:
          instrument: "TEMP::INSTR"
          command: "measure_temperature"
          tolerance: 0.2
          window_s: 60
          interval_s: 5
          timeout_s: 1800
      - { command: "measure_current" }
```

`command` / `wait` / `wait_until` / `wait_for_condition` / `wait_for_stable` のうち
**1 つだけ**を指定する (model_validator で検証)。既存 recipe は全て後方互換。

### 多重 resource 占有 (重要)

polling step が **recipe の主 resource とは別の instrument** を参照する場合、
その resource も Job 起動時に `ResourceScheduler` で占有される。

- 例: PSU で電圧を設定 → 温度計で stable 待ち → PSU で電流測定
  という recipe では、PSU と温度計の **両方が** 同時に lock される
- `Plan.required_resources: list[str]` を新設、`recipe_to_plan(..., primary_resource=)`
  が polling step の `instrument` を再帰収集して canonical sorted で返す
- これにより v0.5.0 で潰した「Job interleave」問題が polling 対象 resource でも発生しない

deadlock 回避のため、複数 resource は **canonical sorted (`sorted(set(...))`)** で
scheduler に渡される。

### Polling 設計 (visa_mcp_v0.5.1の実装方針.md より採用)

- **安定判定**: `max(samples_in_window) - min(samples_in_window) <= tolerance`
- **最初の測定**: 開始直後 (`t=0`) に 1 回、その後 `interval_s` 間隔
- **cancel/timeout 即応**: polling interval 中も `POLL_SLEEP_SLICE_S = 0.2s` 単位でスライス
- **値抽出順序**:
  1. `value_path` が指定されていれば parsed[value_path]
  2. parsed["value"]
  3. 単一数値フィールド
  4. raw を float 化
  5. すべて失敗なら parse エラー
- **error policy**: 1 polling 失敗時は `retry_on_error` 回まで即時 retry、
  連続失敗が `max_consecutive_errors` を超えたら step failed
- **timeout 階層 (3 種)**: `command_timeout_s` (1 query) < `timeout_s` (条件全体) < `job_timeout_s`
- **単位変換しない**: command 返り値をそのまま評価 (将来 `state_query` で対応予定)

### `condition_expr` の安全評価

`utils/condition.py` に `safe_eval_condition()` を新設。許可するもの:

- 変数 `value`
- 数値リテラル
- 比較 (`< <= > >= == !=`)
- 論理 (`and / or / not`)
- 算術 (`+ - * / // % **`)
- 単項 (`+x / -x`)
- 関数呼び出しは **`abs(...)` のみ**

禁止: 属性アクセス / 任意関数呼び出し / import / indexing / 文字列 / 代入 / 内包表記 / lambda。
既存の `utils/expression.safe_eval` とは別関数として実装 (こちらはブール返却・比較演算サポート)。

### 新規 MCP ツール: `start_wait_job` (21 番目)

```python
start_wait_job(
    wait_type: "seconds" | "until" | "condition" | "stable_value",
    params: dict,
    owner: str = "",
    job_timeout_s: float = 0,
    queue_policy: "queue" | "reject_if_busy" = "queue",
) -> dict
```

- `seconds` / `until` は **resource を取らない** (scheduler 即起動)
- `condition` / `stable_value` は `params["instrument"]` を required_resources に持つ
- レスポンスに `data.scheduling` を含む (`immediate_start` / `blocked_by_job` 等)

JobManager 側に `start_wait_job` / `_run_wait_job` / `_build_wait_step` を追加。

### `get_job_status` に polling progress 公開

`waiting` / `running` 状態の Job に polling step が走っている場合、
`data.polling` に下記を含める:

```json
{
  "step_type": "wait_for_stable",
  "instrument": "TEMP::INSTR",
  "command": "measure_temperature",
  "elapsed_s": 42.1,
  "timeout_remaining_s": 1757.9,
  "sample_count": 8,
  "last_value": 25.31,
  "current_delta": 0.18,
  "tolerance": 0.2,
  "window_s": 60.0,
  "stable": false,
  "next_poll_in_s": 2.4,
  "polling_safe_warning": null
}
```

エージェントが「まだ安定待ち、現在 25.31℃、変動幅 0.18℃」と状況判断できる。

### `CommandDefinition.polling_safe`

```yaml
commands:
  measure_temperature:
    scpi: "MEAS:TEMP?"
    type: "query"
    polling_safe: true       # v0.5.1 追加
```

副作用のない query かどうかのヒント。`wait_for_stable` / `wait_for_condition` で
`polling_safe=False` の command を使うと、結果 dict の `polling_safe_warning` に
警告メッセージが入る (実行はブロックしない、v0.5.1 では情報通知のみ)。

### IR validation 厳密化

`WaitForStableStep` で次を model_validator として強制:

- `interval_s > 0`、`timeout_s > 0`、`window_s > 0`、`tolerance >= 0`、`min_samples >= 2`
- `window_s <= timeout_s`
- `interval_s <= window_s`
- `ceil(window_s / interval_s) + 1 >= min_samples` ── サンプル数下限

### 内部レビュー修正 (push 前)

- **`_is_stable` の早期判定バグを修正**: 旧コードは window 内に min_samples 個あれば
  stable と返していたが、実観測時間 (`latest_t - earliest_t`) が `window_s` に達して
  いない場合 stable と判定しないように変更。これがないと window_s=60 / interval=5 で
  開始 10 秒の 3 サンプルだけで stable と返してしまう。
- **`get_progress` のシャローコピー化**: runtime.current_progress を直接返すと
  MCP JSON serialize 中に polling 側 callback で中身が書き換わる可能性があるため
  `dict(progress)` でスナップショットを返す。
- **`samples` リストの prune**: wait_for_stable で全サンプル蓄積していたが、
  window 外の古いサンプルを順次破棄するように変更。24h × 1Hz polling 等の長時間
  実行でのメモリ膨張を防ぐ。最古 1 個は「window_s 経過判定」のため残す。
- **`_build_wait_step` の必須キー検証**: KeyError("seconds") のような不親切な
  エラーではなく、`ValueError("start_wait_job(wait_type='seconds'): params に
  必須キー 'seconds' がありません")` で返す。
- **`CommandStep.instrument` フィールドを削除**: 追加したが YAML / recipe_to_plan
  から populate される経路がなく、v0.5.1 では dead field だった。v0.6.0 で
  group/unit 連携と共に再導入する。
- 追加テスト 2 件: `test_is_stable_rejects_before_window_elapsed` /
  `test_is_stable_accepts_after_window_elapsed`

### テスト

`tests/test_polling_wait_v051.py` に 30 件追加 (合計 **245 passed**)。

- 条件式評価 (比較・論理・abs・禁止構文)
- 値抽出 (value_path / value キー / 単一数値 / raw float / 失敗)
- IR validation (window/interval/timeout 関係、wait_until 排他)
- polling 実行 (immediate success / timeout / cancel)
- stable 実行 (一定値で成功 / 振動で timeout / cancel)
- error retry (1 回失敗 → retry 成功)
- 連続失敗 → step failed
- wait_until (相対秒数)
- **`test_recipe_with_polling_holds_lock_on_temp_resource`** ── polling 対象 instrument
  が `required_resources` に含まれることを scheduler snapshot で確認
- `start_wait_job` (seconds は resource 無し、condition は instrument 占有)
- polling 中の `get_progress` で進捗が取れる

### 後方互換

- 既存の 20 MCP ツール / v0.5.0 YAML / v0.4.x recipe は全て不変
- 既存 215 テストは全パス (新規 28 件と合わせ 243 件)

### 実装しない (v0.5.1 スコープ外)

- 単位変換 (v0.7.0 `state_query` で対応)
- `stddev` / `slope` 安定判定 (v0.5.1 は `method="range"` のみ)
- `monitor_data` 永続化 (v0.7.0)
- `job_events` 完全実装 (v0.7.0)
- branch / loop step (v0.8.0 DSL)
- notification / callback (v0.8.0)

---

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
