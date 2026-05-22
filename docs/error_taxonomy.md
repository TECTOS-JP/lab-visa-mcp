# `error_class` Taxonomy (v0.8.2 草案、v1.0 で凍結予定)

AI エージェントが「次の判断」を決めるために、`error_class` の意味は**安定**して
いる必要がある。v0.8.2 では候補 taxonomy を整理し、v1.0 で互換保証対象とする。

## 分類 (5 カテゴリ)

### 1. validation (入力検証エラー、実機ノータッチ)

| error_class | 発生 | recoverable |
|------------|------|-------------|
| `unknown_command` | 機器定義に存在しない command を指定 | True (修正可) |
| `unknown_instrument` | binding / alias / resource いずれにも該当しない | True |
| `unknown_binding` | DSL `$role` が plan.bindings にない | True |
| `parameter_invalid` | type / range / enum 違反 | True |
| `wrong_command_type` | query を期待した位置に write 等 | True |
| `safety_violation` | strict mode で安全制約違反 | False (override 要承認) |
| `unsupported_step_type` | DSL schema 不明な step type | True |
| `schema_invalid` | Pydantic schema 検証失敗 | True |
| `safe_shutdown_targets_empty` | DSL `safe_shutdown.targets=[]` | True |
| `parallel_placement` | parallel が top-level 末尾以外 | True |
| `parallel_inside_sweep` | sweep.body 内 parallel | True |
| `nested_parallel` | parallel.branches 内に parallel | True |
| `expanded_too_large` | sweep 展開後 step 数が上限超過 | True |
| `not_query_type` | polling/state_query で query 型でない command | True |
| `command_not_found` | (実行時) 機器定義の command が未登録 | True |
| `invalid_since_timestamp` | `get_experiment_timeline(since=...)` の値が ISO8601 でない | True (sub_class) |
| `invalid_until_timestamp` | `get_experiment_timeline(until=...)` の値が ISO8601 でない | True (sub_class) |

> Note (v0.8.2.1): `invalid_since_timestamp` / `invalid_until_timestamp` は
> `error_class="validation"` の `details.sub_class` として返される。
> v1.0 で `error_class` Literal に昇格するか、`sub_class` のまま固定するかは
> v0.9.x で検討する。

### 2. execution (実機実行時のエラー)

| error_class | 発生 | recoverable |
|------------|------|-------------|
| `timeout` | VISA timeout / wait_for_* timeout / job_timeout | True (再試行可) |
| `protocol` | VISA プロトコルエラー | True |
| `hardware` | 機器側エラー (詳細不明) | True |
| `verify_mismatch` | verify read-back で値不一致 | True (機器状態調査要) |
| `blocked` | resource が他 Job により busy | True (待機) |
| `cancelled` | cancel_job により停止 | True |
| `interrupted` | サーバ再起動で中断 | True |
| `WaitConditionTimeout` | wait_for_condition timeout | True |
| `WaitStableTimeout` | wait_for_stable timeout | True |
| `PollingErrorExceeded` | polling の連続エラーが max_consecutive_errors 超過 | True |
| `TimezoneRequired` | wait_until で naive timestamp | True |
| `InvalidTimestamp` | wait_until.timestamp parse 失敗 | True |
| `InstrumentNotFound` | polling 対象 instrument が未識別 | True |
| `AsyncStepRequiresJob` | execute_recipe で polling/barrier 検出 | True (start_recipe_job へ) |

### 3. group / map (Group / Map Job 特有)

| error_class | 発生 | recoverable |
|------------|------|-------------|
| `partial_failure` | 一部 target 失敗 (Job 全体は完了) | True |
| `target_failed` | target_runs の status=failed | True |
| `barrier_timeout` | BarrierCoordinator が timeout | True |
| `policy_stop` | failure_policy で stop_on_first_error 等 | True |
| `PollingErrorExceeded` | (Group target 内) polling 連続失敗 | True |

### 4. persistence (永続化エラー)

| error_class | 発生 | recoverable |
|------------|------|-------------|
| `persistence_warning` | critical event の DB 書き込み失敗 | True (実行は継続) |
| `persistence_error` | (将来) 致命的 DB 障害 | False |

### 5. system (内部エラー)

| error_class | 発生 | recoverable |
|------------|------|-------------|
| `internal` | 予期しない例外 | False (要バグ報告) |
| `not_found` | job_id / template / resource が見つからない | True |
| `validation` | 一般的な引数検証エラー | True |
| `configuration_error` | (将来) _system.yaml 不整合 | True |

## recoverable の意味

- `True`: AI エージェントが追加情報取得 / retry / 設定修正で次のアクションを取れる
- `False`: 人間の判断が必要 (override / バグ調査 / 機器調査)

## v1.0 で凍結対象

- 上記すべての `error_class` 名 (大文字小文字含む)
- `recoverable` の判定基準

v1.0 以降は **新規 error_class 追加は OK、既存 error_class の意味変更・rename は NG**。

## 関連

- `docs/compatibility.md`: v1.0 互換保証対象一覧
- `src/visa_mcp/response_envelope.py`: `make_error` 構造
- `src/visa_mcp/observation.py`: `_is_recoverable` 判定ロジック
