# 変更履歴

## v2.3.2 — Codex v2.3.1 レビュー対応 (reload 保護 + removed 判定 + multi-process lock)

合言葉: **「YAML reload で persisted bindings は消えない」**

### Codex v2.3.1 レビュー指摘 (P1 + P2 × 2)

| # | 指摘 | 対応 |
|---|------|------|
| **P1** | `reload_definitions()` が `session_mgr.clear_all()` を呼んで persisted bindings まで消す。100 台規模で YAML reload しただけで保存済み binding 全消去 | `SessionManager.reload_in_memory_sessions()` を追加。reload_definitions はそれを呼ぶ。persisted bindings は触らず、reload 後 store から再 restore |
| **P2-a** | `clear_persisted_binding` の `removed` を in-memory session の有無で判定。store にだけ残った record (definition 不在で restore skip) を削除しても `removed=false` になる | in-memory OR store の OR で判定。`removed_from_in_memory` / `removed_from_store` も別フィールドで返す |
| **P2-b** | SessionStore に multi-process 排他なし。複数 agent / 複数 server で同じ `sessions.json` を更新すると lost update | cross-platform file lock (`msvcrt.locking` on Windows / `fcntl.flock` on POSIX) + read-modify-write pattern。timeout 5 秒、best-effort fallback |

### 修正

- `SessionManager`:
  - `clear_in_memory()` 新設 (store を触らず in-memory のみクリア)
  - `reload_in_memory_sessions()` 新設 (in-memory 捨てて store から再 restore)
  - `clear_all()` は明示 admin 用として継続 (store も消す)
- `discovery.reload_definitions()`:
  - `clear_all()` → `reload_in_memory_sessions()` に変更
  - response に `sessions_before_reload` / `sessions_after_reload` を追加
- `discovery.clear_persisted_binding()`:
  - `removed` 判定を in-memory OR store の OR に
  - `removed_from_in_memory` / `removed_from_store` を別フィールドで返す
- `SessionStore`:
  - `_file_lock(lock_path, timeout_s)` context manager 追加
  - upsert / touch / remove / clear_all を `read → modify → write`
    の atomic 操作に書き換え (lost update 防止)
  - `_save_locked()` 内部関数 (ロック取得済み前提の save)
  - `.lock` sidecar file が `~/.visa-mcp/sessions.json.lock` に出現
- 依存: `lab-executor-mcp>=2.14.3,<3.0.0`
- 9 件 test 追加 (`test_v2_3_2_review.py`):
  - reload は store を消さない / restore で復活
  - clear_in_memory は store を消さない
  - clear_all は store も消す (admin)
  - store-only ghost record の clear で removed=true
  - 2 thread 並行 upsert で lost update なし (file lock)
  - 別 SessionStore インスタンスからの remove は disk 再読込

### 既知の制約

- file lock は best-effort (`msvcrt`/`fcntl` 無い環境では in-process
  thread lock のみ)。NFS / SMB 上は flock の挙動 OS 依存。
- timeout 5 秒で諦めて save する。logger.warning で通知。

### 互換性

完全後方互換。MCP tool 名・引数は不変、response に新フィールド追加のみ。
lab-executor v2.14.3 と組で release。


## v2.3.1 — Codex v2.2.1 レビュー対応 (resolver 副作用分離 + fixture)

合言葉: **「resolver test に server import は要らない」**

### Codex v2.2.1 レビュー指摘

> `visa_mcp.server` の resolver テストは server module 全体 import を
> 避け、resolver を副作用のない module に分離する。

`from visa_mcp import server` すると JobManager / JobStore まで初期化
されるため、resolver 単体テストとしては副作用が大きかった。

### 修正

- 新規 `src/visa_mcp/instruments_dir.py`:
  - `resolve_instruments_dir(server_file)` を純粋関数化
  - import しても外部状態を変更しない
- `server.py` は新 module から re-export (`_resolve_instruments_dir`
  名は後方互換のため残す)
- `tests/test_v2_2_1_review.py`:
  - resolver test は `from visa_mcp.instruments_dir import
    resolve_instruments_dir` に変更
  - server module を import しなくなり、テスト副作用ゼロ
- shared fixtures (`conftest.py`):
  - `job_store` — `JobStore(tmp_path)` を yield、teardown で `close()`
  - `seed_job` — completed job row INSERT helper
- 既存テスト (`test_v2_2_1_review.py` / `test_v2_1_2_results_integration.py`)
  を fixture ベースに refactor
- 依存: `lab-executor-mcp>=2.14.2,<3.0.0`

### 互換性

完全後方互換。`server._resolve_instruments_dir()` は wrapper として
残るため、既存 import path は壊れない。lab-executor v2.14.2 と組で
release。


## v2.3.0 — bindings / identified state の永続化 (process 再起動耐性)

合言葉: **「bind_definition は一度きり」**

### 背景

レビューで P1 として挙がった: 「process 再起動のたびに
`bind_definition` が必要だと、長時間運用や復旧時に弱い。
手動 bind した 7563 のような *IDN? 非対応機器では特に重要」。

長時間運用 / クラッシュ復旧 / 複数エージェント運用で、過去に
identify / bind した結果を都度やり直すコストが顕著になっていた。

### 修正

- **新規 `src/visa_mcp/session_store.py`**:
  - JSON ファイル (`~/.visa-mcp/sessions.json` または
    `$VISA_MCP_SESSION_STORE`) に bindings を永続化
  - atomic write (tmpfile + os.replace)
  - 不正 JSON / schema mismatch / missing file は warning して
    空扱い (運用継続性優先)
- **`SessionManager`**:
  - `__init__(store=...)` で SessionStore を受け取り、起動時に
    auto-restore する。`store=None` なら従来通り in-memory only
    (完全後方互換)
  - `bind_manually` / `identify` 完了時に store.upsert
  - `clear_session` / `clear_all` 時に store からも削除
  - `identify` の persist は registry の YAML 表記
    (`Kikusui` / `Yokogawa` 等の case 統一) を使う
    (IDN 応答の `KIKUSUI` で persist して restore 失敗する罠を回避)
- **server.py**:
  - 起動時に `SessionStore(default_session_store_path())` を作成
    して SessionManager に渡す
  - 起動 log に `session store: path=..., restored=N` を出力
- **新 MCP tool `clear_persisted_binding(resource_name)`** (experimental):
  - 指定 resource の binding を in-memory と store の両方から削除
  - 返り値: `removed`, `resource_name`, `remaining_sessions`

### 復元できない場合の挙動

- definition が registry に無い (YAML 削除や rename) → warning して
  skip。store の record は残す (後で registry が更新されたら次回
  起動時に restore できる)
- store ファイル破損 → 空扱いで継続、log warning
- write 失敗 → in-memory のみで継続、log warning

### 実機検証

```
Phase 1: identify(PMX35-3A) + bind_manually(7563, Yokogawa, 7563)
  store: 2 records (Kikusui PMX35-3A / Yokogawa 7563)
Phase 2: 新 SessionManager(store=同一 path)
  restored: 2 sessions, both def_loaded=True
```

### 互換性

完全後方互換。Stable / Experimental tool API:
- Stable 不変
- Experimental に `clear_persisted_binding` 1 件追加
- `list_identified_instruments` は内容互換 (restored セッションも
  含めて返す)
- 既存 server.py 起動コードは外部影響なし

### スコープ外 (次回以降)

- multi-process 排他 (lockfile / SQLite 化)
- bindings の export/import (bundle 同梱)
- staleness 検出 (resource が消えた場合の自動 mark)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>


## v2.2.1 — Codex v2.2.0 レビュー対応 (resolver dev 判定 + 7563 corruption pattern)

合言葉: **「wheel install 環境で `<venv>\\Lib\\instruments` を見ない」**

### Codex v2.2.0 レビュー指摘 (visa-mcp 側)

**P1-a**: wheel install 環境で resolver が `<venv>\Lib\instruments`
(過去に手動コピーした古い YAML が残ってる場所) を builtin より
優先してしまい、v2.2.0 の新 parser 定義が適用されていなかった。

**P1-b** (YAML 側): 7563 の loose pattern が `JPPC+0029*0A+0` の
ような corruption を受け入れず、parser が `JPPC` に当たらない
ケースが残っていた。

### 修正

- `server.py:_resolve_instruments_dir()`:
  - dev リポジトリ判定に `<repo>/pyproject.toml` の存在を要求
  - wheel install 環境 (`<venv>\Lib` には pyproject.toml が無い)
    では dev path 探索を skip し、builtin に確実に落ちる
- `examples/instruments/yokogawa_7563.yaml` + builtin copy:
  - loose pattern の値部を `[+-]\d+[.*]\d+[EAea][+-]\d+` に拡張
    し、`*`/`A` corruption を受け付ける
  - lab-executor v2.14.1 の寛容 float 変換と組で正しい値を復元
- `_extract_result_rows` (visa-mcp 側 export shim) も同じ parsed
  metadata 除外を適用 (lab-executor 側と同期)
- 4 件 test 追加 (`test_v2_2_1_review.py`):
  - resolver が pyproject.toml 無しの dev path を skip
  - resolver が pyproject.toml ありの dev path を優先
  - builtin 7563 YAML の loose pattern が JPPC を受け入れる
  - visa-mcp 側 export shim も parsed metadata を skip

### 互換性

`pyproject.toml` を持つ通常の dev リポジトリは引き続き `<repo>/instruments`
が優先される。Stable / Experimental tool API は不変。lab-executor v2.14.1
と組で release。


## v2.2.0 — Yokogawa 7563 parser 強化 + builtin YAML 更新

合言葉: **「parser 未マッチでも値だけは救う」**

### lab-executor-mcp v2.14.0 と同時 release

実機 E2E で同じ 7563 から `NTTC+0033.0E+0` (parser ok) と
`JPPC+0029*1A+0` (未マッチ) が混在する現象を観測。AI エージェントが
「温度が読めなかった」と誤判定する問題があった。

### 修正

- `examples/instruments/yokogawa_7563.yaml` の `measurement_data`
  response_format を:
  - `patterns: list[str]` で複数代替化
  - 厳密 pattern: `tc_type` を `[A-Z]` に広げる (T-type 等の取りこぼし
    解消)
  - 緩い pattern: `^[A-Z]{4}<value>$` で未確認 prefix でも値を抽出
  - `fallback: "numeric_extract"` で完全未マッチでも raw から
    数値だけ救う
- `src/visa_mcp/builtin_instruments/yokogawa_7563.yaml` を同期
- 依存: `lab-executor-mcp>=2.14.0,<3.0.0` (parser 拡張側)

### 実機検証

PMX 出力 2.0V / 7563 read_measurement で raw `JPPC+0029*2A+0\t` を
取得 → parsed.value_numeric=29.0 / fallback_used=numeric_extract が
results / timeline に乗る。

### 互換性

Stable / Experimental tool API 不変。旧 `pattern: <regex>` を持つ
カスタム YAML は後方互換。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>


## v2.1.5 — Codex レビュー反映 (builtin _system 安全化 + resolver 順序確定 + 実 wheel 検証)

合言葉: **「example を fallback default にしない」**

### v2.1.4 への 3 件レビュー指摘 (Codex)

| 優先度 | 内容 | 対応 |
|--------|------|------|
| **P1-a** | builtin `_system.yaml` が EXAMPLE 内容のまま wheel 同梱され、`psu001=GPIB0::6::INSTR` 等の架空 alias / group / unit が production API に出る | builtin `_system.yaml` を空 (instruments/buses/instrument_groups/experiment_units すべて `{}`) で同梱に変更 |
| **P1-b** | resolver 実装が docstring と逆順 (`examples/instruments` を先に見ていた) | 順序を docstring 通り `<repo>/instruments` 優先に修正。docstring も更新 |
| **P2** | fallback test が `Path` 戻り値しか見ておらず builtin が選ばれた・実際に load される・wheel に同梱される、が verify されていない | 実 `InstrumentRegistry` ロード assert + `python -m build` で wheel を作り `visa_mcp/builtin_instruments/*.yaml` の同梱を assert (PMX / 7563 / `_system.yaml`) |

### 修正

- `src/visa_mcp/builtin_instruments/_system.yaml`: 全 mapping を空に
- `src/visa_mcp/server.py:_resolve_instruments_dir()`:
  - 順序を `<repo>/instruments` → `<repo>/examples/instruments` →
    builtin に確定
  - docstring を実装に合わせて更新
- `tests/test_v2_1_5_packaging.py` (新規 7 件):
  - builtin `_system.yaml` に架空 alias が無いこと
  - resolver が `instruments/` を `examples/instruments` より優先
  - `_*.yaml` のみのディレクトリを skip して次に進む
  - dev path 不在時に builtin から実 instrument が load される
    (PMX/7563 を検出)
  - `python -m build --wheel` で wheel を作り、その中に
    `visa_mcp/builtin_instruments/{pmx,7563,_system}.yaml` を
    検出 (CI で `build` パッケージ未導入なら skip)

### 互換性

完全後方互換。Stable / Experimental tool API は不変。
既存の `<repo>/instruments/_system.yaml` を使った運用は引き続き
最優先で読まれる。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>


## v2.1.4 — wheel に builtin_instruments を同梱 + INSTRUMENTS_DIR resolver

合言葉: **「`pip install visa-mcp` 直後に YAML 0 件にならない」**

Codex 実機 E2E (v2.1.3 wheel) で `visa-mcp serve` 起動直後に
instrument 定義が 0 件 load される問題が発生した。原因:

- `src/visa_mcp/examples/instruments/*.yaml` は wheel に含まれていない
- 旧 `server.py` の `INSTRUMENTS_DIR` は `<repo>/instruments` 固定で、
  wheel install 環境ではほぼ存在しない path を指していた

### 修正

- `src/visa_mcp/builtin_instruments/` 配下に主要 YAML を同梱
  (PMX35-3A / Yokogawa 7563 / `_system.yaml`)
- `pyproject.toml` `force-include` に `builtin_instruments` を追加
  → wheel に確実に同梱される
- `server.py:_resolve_instruments_dir()` を導入し、以下の優先順で
  探索:
  1. `$VISA_MCP_INSTRUMENTS_DIR` env 上書き (運用)
  2. `<repo>/examples/instruments` (開発)
  3. `<repo>/instruments` (開発、template/system のみは除外)
  4. `<pkg>/builtin_instruments` (wheel default)
- 起動時 log に `resolved dir + definition count` を出力。
  0 件の場合は WARNING で env 設定方法を案内。
- 新規 test 5 件 (`test_v2_1_4_packaging.py`):
  - builtin_instruments dir に YAML が存在する
  - env override 優先
  - dev path 不在時 builtin fallback
  - version sentinel ≥ 2.1.4
  - pyproject.toml に force-include 設定

### 使い方

```
pip install --upgrade visa-mcp        # 同梱 YAML が利用可能
visa-mcp serve                         # builtin_instruments を auto load
# または運用上書き:
VISA_MCP_INSTRUMENTS_DIR=/path/to/yamls visa-mcp serve
```

### 互換性

完全後方互換。既存 dev 環境 (`<repo>/instruments` / `examples/`)
は引き続き優先される。同梱 builtin は最終 fallback。
Stable / Experimental tool API は不変。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>


## v2.1.3 — get_experiment_results response に version sentinel 追加

v2.1.2 反映後の Codex 実機 E2E でも rows=0 が報告されたため
(コード上は raw_response を読むよう修正済、ローカル MCP tool 経由で
total=12/rows=12 を確認済) **client が server バージョンを即座に
判定できる手段**を response に追加。

### 修正

- `tools/export.py:get_experiment_results` の response data に
  `_meta.versions` を追加 (`visa_mcp` / `lab_executor` / `export_fix`)。
- `pyproject.toml`: `lab-executor-mcp>=2.13.3,<3.0.0`
- test: `_meta` / `versions` / `export_fix` が response data に
  入ることを source check で確認

### 使い方

```python
result = await session.call_tool("get_experiment_results",
                                 {"job_id": "..."})
print(result.data["_meta"]["versions"])
# {'visa_mcp': '2.1.3', 'lab_executor': '2.13.3', 'export_fix': 'v2.1.3'}
```

rows=0 を見た瞬間に `_meta.versions.export_fix` が `v2.1.3` 未満
であれば、起動した server が古いと確定する。

### 互換性

`_meta` は data の追加 field のみ。既存 client は無視できる。
Stable / Experimental tool API 完全互換。


## v2.1.2 — get_experiment_results rows=0 修正 (visa-mcp serve 側 shim)

合言葉: **「lab-executor を直したら visa-mcp の独自 export も直せ」**

lab-executor-mcp v2.13.2 で `_extract_result_rows` のキー名不一致を
修正したが、**`visa-mcp serve` が実際に登録するのは
`visa_mcp.tools.export` 側の独自コピー**であり、こちらは依然
`response_raw` / `response_parsed` しか読まなかった。結果として
Codex 実機 E2E (v2.13.2 + v2.1.1) で `get_experiment_results rows=0`
が再発した (P1 critical)。

### 修正

- `src/visa_mcp/tools/export.py:_extract_result_rows`:
  - parsed: `response_parsed` / `parsed` を OR で読む
  - raw: `raw_response` / `response_raw` / `response` を OR で読む
  - 旧名は後方互換として残置
- `pyproject.toml`: `lab-executor-mcp>=2.13.2,<3.0.0`
- `tests/test_v2_1_2_results_integration.py`: 4 件追加
  - 実 `JobStore` に `raw_response` 付き step を保存し
    `visa_mcp.tools.export._extract_result_rows` が rows を返すこと
  - parsed alias / 後方互換 legacy keys / version sentinel

### 互換性

Stable 43 / Experimental 7 / DSL `dsl_version=0.8` 維持。MCP tool 名
/ 引数 / response 構造は不変。`visa-mcp serve` 起動 + plan 実行で
`get_experiment_results` が rows を返すようになる。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>


## v2.1.1 — Discovery diagnostics: empty-with-success + resource_not_found

合言葉: **「device が消えた状態を agent に伝える」**

Codex 導入テスト (v2.1.0 リリース後) で「v2.1.0 の logic は正しく
動いているが、device が VISA から消えている状態 (USB が unplug
されているなど) を agent が次の行動に繋げにくい」ことが判明。

具体的には:

- `list_resources(query="USB?*")` → `count: 0`
- `discover_resources_safe(...)` → `partial_success: true`、ただし
  `resources: []`
- `probe_resource("USB0::...")` → `VI_ERROR_RSRC_NFOUND (-1073807343)`

いずれも v2.1.0 schema では一般的な error / success に丸まり、
agent が「device 不在」と「driver 異常」を切り分けにくかった。

### `discover_resources_safe` 拡張

新規 field を response 直下に追加:

- **`empty_with_success: bool`** — 1 つ以上の interface が success
  で、かつ全 query 合計の resource 数が 0 なら True
- `data.resource_count: int` — 全 query 合計の resource 数

`empty_with_success=True` の場合、`recommended_next_actions` に
専用のメッセージを差し込む:

```
All queried interfaces returned 0 resources. ...
Check device power and cable for the expected instrument(s).
Open NI MAX and confirm the resource appears under Devices and
Interfaces.
If the device was recently re-plugged, the resource name may have
changed; re-run discover_resources_safe after waiting a few seconds.
```

`failed_interfaces > 0` の従来 next actions と独立して併記される。

### `probe_resource` 拡張

`VI_ERROR_RSRC_NFOUND` (-1073807343) を専用 `error_class` に分類:

- 従来: `error_class="visa_open_resource_failed"` (一般 error)
- 新規: `error_class="visa_resource_not_found"` (device 不在系)

加えて、不在系のときだけ `recommended_next_actions` を返す:

```
Run list_resources(query="USB?*") (or matching interface filter) to
check if the resource is still enumerated.
Verify device power and USB / GPIB cable.
Open NI MAX and confirm the resource name appears under the right
interface.
If the device was recently disconnected and reconnected, the VISA
resource name may have changed; re-enumerate first.
```

### Tests (14 件 pass)

新規:

- `test_discover_safe_empty_with_success_flag` — 全 success / 0
  resource で `empty_with_success=True` + 専用 next actions
- `test_discover_safe_not_empty_no_empty_flag` — resource あれば
  False
- `test_probe_resource_rsrc_nfound_classification` —
  `error_class="visa_resource_not_found"` + `code=-1073807343` +
  `list_resources` / `NI MAX` / `cable` を含む next actions
- `test_probe_resource_other_error_keeps_generic_class` — RSRC_NFOUND
  以外は従来通り `visa_open_resource_failed`

### 互換性

- `discover_resources_safe` 既存 field (`success` / `partial_success`
  / `data.resources` / `data.queries` / `data.successful_interfaces`
  / `data.failed_interfaces` / `recommended_next_actions`) は不変
- `data.resource_count` と top-level `empty_with_success` は **追加
  only**
- `probe_resource` の `error_class` は VI_ERROR_RSRC_NFOUND の場合
  のみ `visa_open_resource_failed` → `visa_resource_not_found` に
  変化 (他はそのまま)。agent が error_class で分岐していた場合は
  注意

### 既知の non-issue

Codex テストで観測された `count: 0` 状態自体は visa-mcp 側で発生
原因ではなく、**device / NI MAX / USB cable 側の問題**。本 release
はそれを agent に伝わる形で報告するためのものであり、自動的に
device を発見し直すものではない。

---

## v2.1.0 — VISA Discovery Diagnostics / Probe Resource

合言葉: **「全件列挙が壊れても、USB は使えるとわかる」**

Codex 導入テストで「GPIB ドライバ異常が全件列挙を巻き込んで USB
列挙まで失敗させる」という実環境問題が見つかった。本 release は
**lab-executor-mcp 側の機能追加ではなく**、visa-mcp の discovery
レイヤを補強する。

### 新規 MCP tool

- **`probe_resource(resource_name, timeout_ms=3000)`** — VISA
  resource を `open_resource` → 属性読取 (interface_type /
  resource_class) → `close` **だけ**で疎通確認。`*IDN?` / `query` /
  `write` は **一切送らない** (test で固定)。VI_ERROR_SYSTEM_ERROR
  等は structured error (`error_class` / `type` / `code` / `message`)
  で返す。raise しない。
- **`discover_resources_safe(queries=[...])`** — query 別の
  `list_resources` を順次実行し、部分成功を返す。default は
  `["USB?*", "GPIB?*", "ASRL?*", "TCPIP?*"]`。GPIB 異常で全件列挙が
  失敗する環境でも USB resource を捨てない。`success` /
  `partial_success` / `successful_interfaces` / `failed_interfaces`
  / `recommended_next_actions` を構造化して返す。

### 既存 tool 拡張

- **`list_resources` docstring 強化** — `query="USB?*"` 等の
  interface 別 filter 使い方を明記、全件列挙失敗時の代替手段として
  推奨
- **`identify_all_instruments(query="?*::INSTR")`** — v2.1.0 で
  `query` 引数を追加。一部 interface だけ識別したい時 (例:
  `query="USB?*"`) に使う

### 安全性 (test で固定)

- `probe_resource` は `*IDN?` / `query` / `write` / `read` を **絶対に
  呼ばない**。pyvisa の resource mock の method 呼び出し回数 0 を
  assert
- `probe_resource` は失敗時にも `query_performed=false` /
  `write_performed=false` を返す
- `probe_resource` は属性読取失敗時にも `finally` で必ず close

### README

- 新規 「Resource discovery with query filters (v2.1+)」セクション
  — `USB?*` / `GPIB?*` / `TCPIP?*` / `ASRL?*` filter の使い方、
  `discover_resources_safe` と `probe_resource` の用途を明記

### Tests

`tests/test_v210_probe_discover.py`: 10 件 pass

- `probe_resource`: open/close success / open failure structured
  error / **does_not_query_or_write** (P0 safety) / closes on success
  / closes on attribute failure
- `discover_resources_safe`: partial_success (USB ok / GPIB fail) /
  all_success / all_failure / default queries

### v2.1 で **やらないこと**

- `doctor visa` CLI (v2.2+ 候補)
- `VISA_MCP_VISA_LIBRARY` 環境変数 (v2.2+ 候補)
- manual resource alias 登録
- `list_resources` / `identify_*` の return schema 破壊的変更

### 互換性

- `list_resources` / `identify_instrument` の戻り値 schema は v2.0
  と同一
- `identify_all_instruments` の戻り値も同一 (新規 `query` 引数は
  default `"?*::INSTR"` で v2.0 互換)
- 新 tool 2 件 (`probe_resource` / `discover_resources_safe`) の
  追加のみ、stable 既存 tool は不変

---

## v2.0.1 — v2.0.0 レビュー応答 (raw VISA env var 統一 / docs/raw_visa.md 更新 / README line-ending note)

合言葉: **「v2.0.0 直後の peripheral 整備」**

v2.0.0 external review (P1/P2) 反映の small patch。public API /
dependency / shim 動作すべて不変。

### 変更点

- **P1-2** (raw VISA 環境変数名統一): release note と
  `docs/raw_visa.md` で別名 (`VISA_MCP_ENABLE_RAW_TOOLS` /
  `VISA_MCP_ALLOW_RAW`) が混在していたが、実装の真正値
  **`VISA_MCP_ENABLE_RAW_COMMANDS=1`** (`src/visa_mcp/tools/
  commands.py` で実際に判定する変数) に統一
  - `docs/raw_visa.md` の bash example
  - `CHANGELOG.md` v2.0.0 entry
  - tool 名も実装に合わせて修正:
    `send_command` → `unsafe_send_command`,
    `query_instrument` → `unsafe_query_instrument`
- **P1-1** (`docs/raw_visa.md`):
  - 冒頭の "v1.11 draft / v2.0.0-rc1 で公開予定" 表記を
    "v2.0.0 で正式公開" へ更新
- **P1** (`README.md`): **line-ending note** を冒頭に追加
  (raw viewer 仕様で「1 line」と mis-report される件は
  `.gitattributes` + CI gate で実体担保している旨を明文化)

### 互換性

- API / shim forward 動作: 不変
- dependency (`lab-executor-mcp >= 2.0.2, < 3.0.0`): 不変
- 環境変数: **既存実装 `VISA_MCP_ENABLE_RAW_COMMANDS` のまま**
  (docs の誤記を修正しただけで、利用者の設定変更は不要)

## v2.0.0 — PyVISA Backend Package + Compatibility Shim (final release)

**visa-mcp** は v2.0 から **lab-executor-mcp 用の PyVISA backend
package + 旧 import 互換 shim** として正式公開する。v1.x まで 1
パッケージで提供していた「実験実行 runtime / DSL / extension
ecosystem」は v2.0 で **`lab-executor-mcp`** (新 repo) に移った。

### Positioning

```
lab-executor-mcp   ← backend-independent experiment runtime
visa-mcp           ← PyVISA backend + raw VISA tools + 旧互換 shim
```

依存方向は固定:

```
visa-mcp  →  lab-executor-mcp  (許可)
lab-executor-mcp  →  visa-mcp  (禁止)
```

### Depends on

- `lab-executor-mcp >= 2.0.2, < 3.0.0`
- `pyvisa`
- `pydantic`
- `pyyaml`

### Kept in visa-mcp

- `PyVisaBackend` (`InstrumentBackend` adapter)
- `VisaManager` / `SessionManager` / `BusManager`
- raw VISA tools (env-gated `VISA_MCP_ENABLE_RAW_COMMANDS=1`)
- `tools/discovery.py` (PyVISA resource 列挙 → `list_resources`)
- `visa-mcp serve` 互換 entry point (composition root)
- `visa_mcp.*` 旧 import path の **shim** (27 module)

### Moved to lab-executor-mcp

- DSL (`ExperimentPlan`, `dsl_version=0.8`) + validator + dry-run
- Job manager / state machine / scheduler / barrier
- Group / Map executor
- Observation API (`timeline` / `live_view` / `summary`)
- Benchmark runner + repair tasks
- Definition pack ecosystem
  (`extension init/install/check/package/catalog/authoring`)
- Instrument authoring
  (`scaffold` / `promote-check` / `review-report`)
- Registry / response parser
- Export / bundle
- Audit / locks / SQLite

### Compatibility

- 旧 `from visa_mcp.* import ...` はすべて **DeprecationWarning** 付き
  で動作 (`from lab_executor.* import *` に forward)
- **MCP tool**: Stable 43 + Experimental 7 = **50** (v1.0 から不変)
- **DSL**: `dsl_version=0.8` 完全互換
- **extension pack** `.visa-mcp-ext.zip` 形式: 完全互換
- **`.install_meta.json`** schema: 完全互換
- **`~/.visa-mcp/extensions/`** install path: 継続使用 (v2.x で
  `~/.lab-executor/extensions/` への移行検討)

### CLI status

- `visa-mcp --version` / `--help` / `serve` / `list-resources`:
  従来互換
- `visa-mcp validate / extension / instrument <subcommand>`:
  shim 経由で動作 (`DeprecationWarning` 付き、lab-executor 側実装に
  forward)
- 実機 MCP server 起動の推奨入口は引き続き **`visa-mcp serve`**
  (`lab-executor serve` は v2.0 では placeholder、v2.1+ で実装予定)

### Raw VISA tools

接続確認 / デバッグ / 緊急確認用。env-gated を維持:

```bash
export VISA_MCP_ENABLE_RAW_COMMANDS=1
visa-mcp serve
```

通常の AI エージェント実験は lab-executor runtime + named command
/ DSL 経由を推奨。

### Verified

ローカル + クリーン clone (`git clone --branch v2.0.0`):

```
python -m tomllib pyproject.toml             OK
python -c "import yaml; yaml.safe_load(ci.yml)"  OK
python -m compileall src tests                OK
pytest tests/test_v200_shim.py                22 passed
python -m build                               OK (wheel)
import visa_mcp                               2.0.0
import lab_executor                           >= 2.0.2
DeprecationWarning on visa_mcp.extension      OK
from visa_mcp.dsl.compiler import X           OK (submodule alias)
PyVisaBackend signature satisfies Protocol    OK (inspect.signature)
PyVisaBackend constructor lazy                OK
visa-mcp <subcommand> --help                  OK (validate / extension /
                                                  instrument shim)
multiline / LF only guard                     OK (11 file)
```

### What's NOT in v2.0.0

- New backend plugin system
- Remote registry
- Signature / trust store
- Replay backend
- CLI redesign
- `lab-executor serve` 実装 (v2.1+ で予定)
- Curated lab-executor inherited tests (v2.1+ で順次)

### v2.0.1 候補 (peripheral)

- README / migration guide 表現補正
- shim 対象 module の追加 (希望があれば)
- `docs/raw_visa.md` 整備
- CLI help 表現修正
- wheel metadata / classifier 微修正

### v2.1+ 候補

- `lab-executor serve` 実装 + CLI 完全 port
- `~/.lab-executor/extensions/` 並走移行計画
- visa-mcp shim 利用状況を見て Deprecation スケジュール調整

## v2.0.0-rc2 — rc1 レビュー応答 (`.gitattributes` LF / PyVisaBackend docstring / Protocol signature test / CLI shim smoke)

合言葉: **「lab-executor-mcp 側の rc2/rc3 と同じ peripheral 整備を
visa-mcp 側にも適用」**

rc1 external review (P0/P1) を反映した patch。public API / shim
forward 動作 / dependency 範囲すべて不変。

### 調査結果 (rc1 P0: raw 改行)

クリーン clone で確認 → git blob は LF 多数行で正常:

| File | git blob LF |
|------|-----:|
| `pyproject.toml` | 59 |
| `.github/workflows/ci.yml` | 51 |
| `README.md` | 279 |
| `docs/v2_migration.md` | 129 |
| `src/visa_mcp/extension.py` (shim) | 27 |
| `src/visa_mcp/backends/pyvisa_backend.py` | 76 |
| `src/visa_mcp/dsl/__init__.py` (shim) | 47 |
| `tests/test_v200_shim.py` | 183 |

原因は Windows checkout (autocrlf=true) 由来の CRLF artifact が
レビュアーの raw viewer で「1 line」と mis-report される件
(lab-executor-mcp 側 v2.0.0-rc3 で確認済の現象と同一)。

### 変更点

- **P0** `.gitattributes` 追加 (lab-executor-mcp と同方針):
  `* text=auto eol=lf` + `*.py` / `*.toml` / `*.yaml` / `*.yml` /
  `*.json` / `*.md` 等に `eol=lf` 強制。Windows checkout でも LF
  維持 → raw viewer 仕様に関わらず正しく line 表示
- **P1-1** (`src/visa_mcp/backends/pyvisa_backend.py`): docstring を
  v1.11 表記から **visa-mcp v2.0 backend** 文脈へ書き直し
  (lab-executor-mcp に runtime が分離されている前提)
- **P1-2** (`tests/test_v200_shim.py` 新規 test):
  - `test_pyvisa_backend_signature_matches_protocol`: `inspect.
    signature` で `InstrumentBackend` の必須 param が
    `PyVisaBackend` に存在することを検証 (Impl が optional 拡張
    param を持つことは許容)
  - `test_pyvisa_backend_constructor_does_not_open_hardware`:
    constructor の lazy 性確認
  - `test_critical_files_are_multiline_and_lf_only`:
    `.gitattributes` の効果を CI で固定 (11 file >= 10 lines + CR=0)
  - `test_visa_mcp_cli_subcommand_help_smoke`: `visa-mcp <sub>
    --help` (validate / extension / instrument) が exit 0 で起動
    する CLI shim forward を検証

### tests

- shim + backend test: **22 件 pass** (rc1 比 +4)

### 互換性

- API / shim forward 動作: 不変
- dependency (`lab-executor-mcp >= 2.0.2, < 3.0.0`): 不変
- MCP tool / DSL / extension pack: 不変

## v2.0.0-rc1 — PyVISA backend + compatibility shim for lab-executor-mcp

合言葉: **「visa-mcp は v2.0 から PyVISA backend package。runtime は
lab-executor-mcp に取り込む」**

v2.0 分離リリースの **visa-mcp 側の rc1**。`lab-executor-mcp v2.0.x`
を依存に取り込み、visa-mcp 側はバックエンド層と旧 import 互換 shim に
特化した。

### 依存方向

```
visa-mcp  →  lab-executor-mcp  (許可)
lab-executor-mcp  →  visa-mcp  (禁止)
```

### 変更点

- **P0** (`pyproject.toml`):
  - version `1.11.1` → `2.0.0-rc1`
  - `lab-executor-mcp >= 2.0.2, < 3.0.0` を新規依存に追加
  - 旧 dependency (pdfplumber) を削除 (lab-executor 側へ)
- **P0** runtime / DSL / extension ecosystem を **shim** 化:
  - 単一 file shim (20 件): `audit.py` / `extension*.py` /
    `instrument_authoring.py` / `instrument_registry.py` /
    `observation.py` / `polling_executor.py` / `response_envelope.py` /
    `response_parser.py` / `safety.py` / `stability.py` /
    `state_query.py` / `system_config.py` / `recipe_executor.py` /
    `step_executor.py` / `registry.py` 等
  - package shim (7 件、`sys.modules` aliasing で submodule も解決):
    `dsl/` / `job/` / `group/` / `experiment_ir/` / `models/` /
    `testing/` / `utils/`
  - すべての shim は import 時に `DeprecationWarning` を発し、
    `from lab_executor.* import *` に forward する
- **visa-mcp 側に残す** (実装そのまま):
  - `visa_manager.py` / `session_manager.py` / `bus_manager.py`
  - `backends/pyvisa_backend.py` (`InstrumentBackend` adapter)
  - `tools/discovery.py` (PyVISA resource 列挙)
  - `tools/commands.py` (raw VISA + env-gated)
  - `server.py` / `cli.py` (composition root)
- **`scripts/convert_to_shim.py`** (新規): 再現可能な shim 化 script
  (`visa-mcp v1.x checkout から v2.0 shim 状態へ変換)
- **CI 全面刷新** (`.github/workflows/ci.yml`):
  - `test`: lab-executor-mcp を git tag (`v2.0.2`) から install +
    visa-mcp install + `tests/test_v200_shim.py` smoke
  - `build`: wheel build + install + import smoke
  - 旧 separation-boundary / multi-line guard CI は v1.x 用途のため
    削除 (lab-executor-mcp 側で継承済み)
- **`tests/test_v200_shim.py`** (新規 18 件):
  version / lab-executor 依存 / shim DeprecationWarning / submodule
  alias / Stable 43 + Experimental 7 = 50 / backend layer / CLI smoke
- **`tests/_legacy_v1_archived/`**: v1.x の 1500+ tests を archive
  ディレクトリへ移動 (動作は lab-executor-mcp 側のテストで担保)

### 互換性

- **旧 import path**: すべて DeprecationWarning 付きで動作
  - `from visa_mcp.extension import ...`
  - `from visa_mcp.dsl.compiler import ...`
  - `from visa_mcp.job import ...`
  - 等
- **MCP tool 数**: Stable 43 + Experimental 7 = 50 (lab-executor 経由
  で不変)
- **DSL `dsl_version=0.8`**: 完全互換
- **extension pack 形式 / `.install_meta.json`**: 完全互換
- **`~/.visa-mcp/extensions/` install path**: 継続使用

### CLI status

`visa-mcp serve` / `visa-mcp list-resources` / raw VISA tools は従来
互換。`visa-mcp extension ...` / `visa-mcp instrument ...` 等は shim
経由で動作 (`DeprecationWarning` 付き)。

### 検証

```
pytest tests/test_v200_shim.py    18 passed
pytest tests/test_session_manager.py tests/test_visa_manager_locking.py \
       tests/test_raw_commands.py + shim  43 passed
```

### 次フェーズ

- レビュー + GitHub Actions green 確認後 `v2.0.0` 本番 release
- `lab-executor-mcp` の PyPI 公開 → visa-mcp の依存も PyPI に変更

## v1.11.1 — v1.11.0 レビュー応答 (docstring / split_rehearsal AST verify / docs 補強)

合言葉: **「v2.0 直前の境界 docs を v1.11 実体化状態に揃える」**

v1.11.0 external review (P0/P1) を反映した patch release。
public API / CLI 引数 / schema / MCP tool 一覧すべて不変。

### 変更点

- **P1-1** (`src/visa_mcp/backends/base.py`): Protocol docstring を
  v1.1 spike 表現から **v1.11 実体化状態** に書き換え。`PyVisaBackend`
  / `MockBackend` の位置づけ、Protocol が最小に留まる理由、`timeout_ms`
  / termination 引数を v2.0 公開境界として採用する旨を明記
- **P1-3** (`tests/test_v111_separation_refactor.py`):
  PyVisaBackend の **import** と **instance 生成** のテスト責務を分離
  - `test_pyvisa_backend_module_imports_without_instantiating`:
    module import が PyVISA 不要で成功すること
  - `test_pyvisa_backend_class_satisfies_protocol_shape`: class
    structural shape のみ確認 (`__new__` を使った曖昧な instance
    check を廃止)
- **P1-4** (`src/visa_mcp/dev/split_rehearsal.py`): `verify_candidate()`
  関数 + `--verify` CLI flag を追加。candidate tree を:
  - 全 `*.py` を `ast.parse` で構文検証
  - `visa_mcp.<lab module>` 文字列の rewrite 漏れを再走査
  対応 test: `test_split_rehearsal_verify_candidate` /
  `test_split_rehearsal_cli_verify_flag`
- **P1-5** (`docs/separation/module_ownership.yaml`): 先頭コメントを
  `v1.10.0, draft for v2.0 split` → `v1.11.1, split rehearsal ready`
  に更新。v1.10/v1.11 の達成を明記
- **P1-6** (`docs/raw_visa.md`): import path deprecation 文言を
  「v2.2 で削除」→「v2.2 以降の削除候補 (実利用状況を見て判断)」に
  softening
- **P1-7** (`docs/raw_visa.md` 追記): `MockBackend` / `MockVisaManager`
  naming 方針 (public は `MockBackend`、`MockVisaManager` は legacy
  internal、v2.1+ で rename 検討) を memo 追加
- **P0-2** (`tests/test_v111_separation_refactor.py` 追加 2 件):
  - `test_v111_new_files_covered_by_format_guard`: v1.11 新規 7 file
    が repo-wide `SWEEP_PATTERNS` でカバーされる
  - `test_v111_new_files_are_multiline`: 30 行以上 + LF only 確認

### tests

- 全 test: **1538 passing** (v1.11.0 比 +5)

### 互換性

- MCP tool 数 / DSL schema / extension pack 形式: 不変
- `InstrumentBackend` Protocol shape: 不変
  (docstring と docs のみ更新)
- `split_rehearsal` の既存 CLI: 不変 (`--verify` flag を追加のみ)

### v2.0.0-rc1 へ (preview)

v1.11.1 で v2.0 直前の docs / verify が揃った。次は:
- feature freeze
- `git filter-repo` dry-run + wheel build verification
  (lab-executor-mcp wheel が PyVISA 非依存であること確認)
- `docs/v2_migration.md` draft
- 利用者用 install path / extension pack 互換 smoke test

## v1.11.0 — Separation Refactor + Split Rehearsal

合言葉: **「分離はまだしない。ただし、分離しても壊れないことを CI で
証明する」**

ロードマップ v6 Phase A 最終ステップ。**v2.0 で実際にリポジトリを
分割する前の最後の内部リファクタ**。public API / MCP tool 数
(Stable 43 + Experimental 7 = 50) / DSL schema / extension pack 形式
すべて不変。

### 主な変更

**1. `KNOWN_V111_TO_RESOLVE = 0` (P0 達成)**

v1.10 で tracking していた 10 件の lab→visa top-level violation を
すべて `if TYPE_CHECKING:` ブロック + 関数内 lazy import に移動し、
runtime 候補 module の top-level から backend layer 依存を排除した。

- `visa_mcp.dsl.compiler`
- `visa_mcp.group.executor`
- `visa_mcp.job.manager`
- `visa_mcp.testing.benchmark_runner` (`InstrumentSession` 実体化箇所は
  関数内 lazy import に変更)
- `visa_mcp.tools.dsl`
- `visa_mcp.tools.info`
- `visa_mcp.tools.recipes`

`src/visa_mcp/dev/ownership_check.py` の `KNOWN_V111_TO_RESOLVE` を
空 set に変更。新規 violation は CI で即 fail。

**2. Backend Protocol 実体化 (`src/visa_mcp/backends/`)**

| Module | Owner | 役割 |
|--------|-------|------|
| `backends/base.py` | shared | `InstrumentBackend` Protocol (v1.1 から継承) |
| `backends/pyvisa_backend.py` | visa-mcp | `PyVisaBackend` adapter (新規, `VisaManager` 包む) |
| `backends/mock_backend.py` | lab-executor-mcp | `MockBackend` adapter (新規, `MockVisaManager` 包む) |

`PyVisaBackend()` / `MockBackend()` は引数なしで構築でき、内部で対応する
manager を lazy import する。runtime module は `InstrumentBackend` 経由で
backend を扱える状態 (実際の経路置換は v2.0)。

**3. Split Rehearsal (`src/visa_mcp/dev/split_rehearsal.py`)**

`module_ownership.yaml` を基に tmp directory に
`lab_executor_candidate/` ツリーを生成する CLI:

```
python -m visa_mcp.dev.split_rehearsal --out tmp/lab_executor_candidate
```

- lab-executor owner の module を copy
- import 文を `visa_mcp.<lab module>` → `lab_executor_candidate.<...>`
  に rewrite (backend / shared / visa-mcp owner は維持)
- release artifact に **含めない** (テスト中に tmp 生成 → 検査 →
  自動削除)

これにより v2.0 で `git filter-repo` + path rename を実行する前の
**dry-run** が CI で常時走る。

**4. `docs/raw_visa.md` draft 追加**

v1.10.1 で TODO 化していた visa-mcp 側 v2.0 用 docs を draft 作成。
PyVISA setup / `list_resources` / env-gated raw tools
(`VISA_MCP_ALLOW_RAW=1`) / lab-executor runtime との composition root
関係を整理。

**5. Manifest / docs 更新**

- `module_ownership.yaml`: 新規 module 3 件追加
  (`backends.pyvisa_backend`, `backends.mock_backend`,
  `dev.split_rehearsal`)、statistics 73 → 76
- `docs/separation/notes.md`: v1.11 達成内容を追記

### tests

`tests/test_v111_separation_refactor.py` (新規 14 件):
- `test_no_known_v1_11_violations` (P0 gate)
- `test_runtime_modules_no_toplevel_visa_manager_import`
- `test_instrument_backend_protocol_runtime_checkable`
- `test_pyvisa_backend_satisfies_protocol` /
  `test_mock_backend_satisfies_protocol`
- `test_*_backend_constructible_without_explicit_visa`
- `test_split_rehearsal_generates_candidate`
- `test_split_rehearsal_candidate_has_no_visa_mcp_imports`
- `test_split_rehearsal_cli_runs`
- `test_raw_visa_doc_exists`
- `test_stable_tool_count_unchanged` (Stable 43 / Experimental 7 不変)
- `test_backends_init_exposes_adapters`

全 test: **1533 passing** (v1.10.1 比 +14)

### 互換性

- MCP tool 名 / 引数 / response: 不変 (Stable 43 / Experimental 7)
- DSL `dsl_version=0.8`: 完全互換
- extension pack 形式 / `.install_meta.json`: 完全互換
- import path: 不変 (v2.0 で shim 化予定)
- backend class 名: 新規追加のみ、既存 `VisaManager` / `MockVisaManager`
  は v2.0 まで残る

### v2.0.0-rc1 で取り組むこと (preview)

- feature freeze
- `git filter-repo` dry-run + wheel build verification
  (lab-executor-mcp wheel が PyVISA 不要であること確認)
- `docs/v2_migration.md` draft 公開 (両 repo 用)
- migration guide review

## v1.10.1 — v1.10.0 レビュー応答 (format guard 拡張 / statistics 自動検証 / 補強)

合言葉: **「v1.10 で導入した分離設計台帳を、format guard と自動検証で
守る」**

v1.10.0 の external review (P0/P1) を反映した patch release。
public API / CLI 引数 / schema / MCP tool 一覧すべて不変。

### 変更点

- **P0-2** (`tests/test_repo_format_guard.py`): SWEEP_PATTERNS に
  `docs/**/*.yaml` / `docs/**/*.yml` を追加。今回 `module_ownership.yaml`
  / `split_manifest.yaml` が repo-wide format guard の対象外だった件
  への直接 fix (将来同種の YAML が圧縮されたら CI で即検出)
- **P0-3 / P1-4** (`tests/test_v110_separation_audit.py` 追加 3 件):
  - `test_dependency_graph_md_committed_multiline`:
    `docs/separation/dependency_graph.md` が 20 行以上 + 必須 section
    (`# Dependency Graph Report` / `## Statistics` / `## Owner counts`)
    を含む + CR 無し
  - `test_module_ownership_statistics_match`: manifest 末尾の
    `statistics:` block が **実 owner count と一致**することを Counter
    で自動検証
  - `test_module_ownership_yaml_not_collapsed`: separation YAML 2 件が
    30 行以上 + LF only
- **P1-4 副作用修正** (`docs/separation/module_ownership.yaml`):
  v1.10.0 の statistics 宣言値 (51/4/7/2) が実 count (59/4/8/2) と
  ズレていたため正しい値に更新。test で今後の drift を防止
- **`test_version_is_1_10_x`**: patch release で fail させないよう
  `1.10.0` 固定 → `startswith("1.10.")` に緩和
- **`split_manifest_paths_exist`**: しきい値方針を inline コメント化
  (v1.10 70% / v1.11 split_files 除き 100% / v2.0.0-rc1 で
  move_to/keep_in は 100%、split_files はすべて resolved)
- **`docs/separation/notes.md`** 拡張:
  - v1.10.1 patch summary
  - **v1.11 最重要 gate: `KNOWN_V111_TO_RESOLVE = empty set`** を強調
  - `visa-mcp` 側 docs 戦略 (`docs/raw_visa.md` を v1.11 / rc1 で
    draft 作成) を TODO 化
  - `instrument_authoring.py` の `lab_executor/instrument_authoring/`
    分割検討 memo

### tests

- 全 test: **1519 passing** (v1.10.0 比 +3)

### 互換性

- MCP tool 数 / DSL schema / extension pack 形式: 不変
- `module_ownership.yaml` / `split_manifest.yaml` の構造: 不変
  (statistics 値のみ正確化)

## v1.10.0 — Separation Readiness Audit + Instrument Review Workflow

合言葉: **「分離前の最終仕上げ — module ownership を機械可読化し、
v2.0 分離を git filter-repo で実行できる地点まで設計を固める」**

ロードマップ v6 Phase A の 2 件目 (v1.9 → v1.10 → v1.11 → v2.0.0-rc1
→ v2.0.0)。本 release で Stable / Experimental tool surface
(43 + 7 = 50) は不変、DSL schema / extension pack 形式も完全互換。

### 主な変更

- **`docs/separation/module_ownership.yaml`** (新規, 機械可読 manifest):
  - `src/visa_mcp/` 配下 73 module すべてを
    `lab-executor-mcp` / `visa-mcp` / `split` / `shared` のいずれかに
    分類 (unclassified = 0)
  - `split` entry には `lab_executor_part` + `visa_mcp_part` を記述
  - statistics: lab-executor-mcp 51 / visa-mcp 4 / split 7 / shared 2
- **`docs/separation/split_manifest.yaml`** (新規):
  - v2.0 で `git filter-repo` する path の網羅リスト
  - `keep_in_visa_mcp` (visa_manager.py / bus_manager.py /
    session_manager.py / tools/discovery.py)
  - `split_files` (cli.py / tools/commands.py / step_executor.py /
    recipe_executor.py / registry.py / server.py / __init__.py /
    backends/) を v1.11 で split する方針を明記
  - `rc1_gates`: v2.0.0-rc1 で満たすべき 8 項目 checklist
- **`src/visa_mcp/dev/ownership_check.py`** (新規 CI gate):
  - manifest と src/ 実体を照合 (未分類 module 検出)
  - lab-executor owner module が visa-mcp owner module を
    **top-level import** していないこと検査 (AST ベース)
  - `LAZY_EXCEPTIONS` (関数内 lazy import の許容例外、
    `testing/mock_instruments.py` 1 件)
  - `KNOWN_V111_TO_RESOLVE` (v1.11 で InstrumentBackend Protocol
    経由化により解消する **既知 10 件**を tracking。CI fail を防ぐが
    新規追加禁止 / v1.11 で削減のみ)
  - 使い方: `python -m visa_mcp.dev.ownership_check [--json]
    [--graph-md docs/separation/dependency_graph.md]`
- **`docs/separation/dependency_graph.md`** (自動生成):
  - statistics / owner counts / NEW violations / known v1.11-to-resolve
    の markdown レポート
  - `ownership_check.py --graph-md` で再生成 (手で編集しない)
- **`visa-mcp instrument review-report <path>`** CLI (新規):
  - 1 instrument YAML → markdown 形式 PR review report
  - `validate_instrument_file(strict=True)` +
    `promote_check_instrument(target=tested)` +
    `promote_check_instrument(target=verified)` を集約した「読み物」
  - `--output <file.md>` で書き出し、`--json` で構造化出力
  - `review_report_instrument(path)` Python API も提供
- **`docs/separation/notes.md`** 更新:
  - v1.10 の成果物 4 件を navigation 用に追記
  - v1.11 で `KNOWN_V111_TO_RESOLVE` を 0 件に削減することを gate と
    明記

### tests

- `tests/test_v110_separation_audit.py` (新規 12 件):
  manifest 完全性 / NEW violation = 0 / KNOWN tracking /
  split_manifest path 実在 / dependency graph 生成 /
  ownership_check CLI exit=0 + JSON / review-report function +
  missing file / review-report CLI markdown + JSON
- 全 test: **1516 passing** (v1.9.1 比 +12)

### 互換性

- MCP tool 数: Stable 43 / Experimental 7 / 合計 50 (v1.0 から不変)
- DSL `dsl_version=0.8` 完全互換
- extension pack 形式 / `.install_meta.json` schema 完全互換
- 新規 CLI (`instrument review-report`) は追加のみ、既存 CLI 無変更

### v1.11 で取り組むこと (preview)

- `InstrumentBackend` Protocol 実体化 + `PyVisaBackend` /
  `MockBackend` adapter
- `KNOWN_V111_TO_RESOLVE` 10 件を adapter 経由化で 0 へ
- `step_executor.py` / `tools/commands.py` を runtime / backend に分割
- `src/lab_executor_candidate/` 仮 namespace で split rehearsal

## v1.9.1 — v1.9.0 レビュー応答 (repo-wide format guard / docs 補強)

合言葉: **「format guard を repo 全体に効かせ、v1.9 で追加した境界 /
昇格 / category の挙動をドキュメント化する」**

v1.9.0 の external review (P0/P1/P2) を反映した patch release。
public API / CLI 引数 / schema すべて不変。

### 変更点

- **P0-1 / P0-3**: **`tests/test_repo_format_guard.py`** 新規追加
  - `src/**/*.py` / `tests/**/*.py` / `docs/**/*.md` /
    `.github/workflows/**/*.yml` / `schemas/**/*.json` /
    `registry/**/*.yaml` / `examples/**/*.yaml` /
    `scripts/**/*.py` / `src/visa_mcp/templates/**/*.yaml` の
    **repo 全体 sweep**
  - CR 検出 + **5 行未満潰れ検出**を 1 file で集約 (version 別の
    parametrize から CI lint job の single source of truth へ)
  - `__init__.py` は default で除外、個別 file は
    `MIN_LINES_EXCEPTIONS` で micro-tune 可
- **P0-2**: CI workflow を `yaml.safe_load` で parse + 必須 keys
  (`name` / `on` / `jobs`) と job 構成 (`test` / `pyvisa-not-installed`
  / `lint`) を test 化。
  `test_yaml_workflows_parse_correctly` /
  `test_ci_workflow_includes_pyvisa_not_installed_job` で回帰防止
- **CI lint job 強化** (`.github/workflows/ci.yml`):
  `pytest tests/test_repo_format_guard.py -v` を最初に走らせ、
  既存の per-version test も継続実行 (二重 gate)
- **P1-4 / P1-5** (`docs/separation/notes.md` 拡張):
  - 「v1.9 boundary smoke tests の限界」section 追加
    (top-level import のみ検出 / lazy import 許容 / v1.10/v1.11 で改善)
  - 「pyvisa CI 戦略」section: v1.9 で uninstall して検出 → v2.0 で
    base install から外す方針
- **P1-6** (`docs/instrument_promote_check.md` 新規):
  - `--target draft/experimental/tested/verified` ルール表
  - 下方移動が常に eligible である理由
  - JSON 出力例 / 終了コード / `validate instrument --strict` との
    使い分け
  - 内部実装 (strict 結果を再利用) の明記
- **P1-7** (`docs/separation/notes.md`):
  registry.py 分割候補 (`registry/index.py` / `instrument_validation.py`
  / `strict_checks.py` / `plan_validation.py` /
  `benchmark_validation.py` / `category_policy.py`) を v1.10 向け
  TODO として明記
- **P2-8** (`docs/category_policy.md` 新規):
  - canonical category 表 (output-capable 列付き)
  - alias 表 (`multimeter` → `dmm` 等)
  - `normalize_category()` 呼び出し場所一覧
  - scaffold template の `metadata.category` 一致状況 (v1.8.1 で
    `dmm` 統一済み)
  - v1.10+ への TODO (registry INDEX lint alias 検出 / 階層化判断)

### 互換性

- public API / CLI 引数 / 既存 docs / 既存 test すべて不変
- 新規 docs 2 件 (`instrument_promote_check.md` / `category_policy.md`)
- 新規 test file 2 件 (`test_repo_format_guard.py` /
  `test_v191_review.py`)
- CI workflow に lint step が 1 つ追加されただけ
- Stable 43 / Experimental 7 / 合計 50 不変

---

## v1.9.0 — Instrument Quality + Separation Boundary Smoke Tests

合言葉: **「scaffold した instrument を tested / verified へ昇格させら
れる品質か診断する」+「v2.0 分離に向けて、これ以上依存を悪化させない」**

ロードマップ v6 の Phase A (分離前最終仕上げ) の **最初のリリース**。
MCP tool 追加ゼロ、Stable 43 / Experimental 7 / 合計 50 不変。

### P0: `validate instrument --strict` 強化

新 strict checks (`validate instrument <path> --strict` でのみ有効):

| error_class | 条件 |
|-------------|------|
| `instrument_manual_ref_todo` | `metadata.manual_ref` に "TODO" / "TBD" / "FIXME" / "URL or document" placeholder 残存 |
| `instrument_missing_safe_shutdown` | 出力系 instrument で `safe_shutdown` 未定義 |
| `instrument_missing_safety_ratings` | 出力系 instrument で `safety.ratings` 未定義 / 空 |
| `instrument_missing_verify` | state 変更系 set command (set_voltage / set_current / set_output / set_temperature / set_frequency 等) に `verify` 未定義 |
| `instrument_verified_missing_evidence` | `support_level=verified` だが `metadata.validation_evidence` 空 |

#### 設計判断 (P1 反映)

- `instrument_missing_verify` は **state 変更系の prefix list** で限定。
  `set_display_brightness` / `set_beep` / `reset` / `clear_error` 等の
  auxiliary write は除外
- `_STATE_CHANGING_PREFIXES`: `set_voltage` / `set_current` /
  `set_output` / `set_temperature` / `set_frequency` /
  `set_amplitude` / `set_range` / `set_mode` / `set_setpoint` /
  `set_pressure` / `set_flow` / `set_speed`
- error message に `suggested_readback_command` を含める
  (`set_voltage` → `query_voltage` / `measure_voltage` を自動推定)

#### 出力系 category 判定 + alias

- `OUTPUT_CAPABLE_CATEGORIES`: `power_supply` / `smu` /
  `function_generator` / `electronic_load` /
  `temperature_controller` / `heater` / `actuator`
- `CATEGORY_ALIASES`: `multimeter` → `dmm` / `psu` → `power_supply` /
  `function_gen` / `fg` → `function_generator` / `eload` →
  `electronic_load` / `tc` → `temperature_controller`
- `normalize_category()` public helper

### P0: Separation Boundary Smoke Tests

**新規 module** `src/visa_mcp/dev/dependency_report.py`:

```bash
python -m visa_mcp.dev.dependency_report
python -m visa_mcp.dev.dependency_report --json
```

- runtime 候補 module 10 個の **top-level import** だけを AST で集計
- `visa_mcp.visa_manager` / `pyvisa` の直接 import を検出
- 関数内 lazy import は許容 (例:
  `testing/mock_instruments.py` の VISA timeout error 互換 raise)

**新規 test** `tests/test_separation_boundary.py` (14 件):

- clean subprocess で runtime module を import し、`sys.modules` に
  `pyvisa` が漏れないことを確認
- AST で top-level の `visa_manager` / `pyvisa` 直接 import 検出
- 各 runtime 候補 module の in-process import 可能性
- `dependency_report --json` の clean subprocess 実行

### P0: pyvisa-not-installed CI

**新規** `.github/workflows/ci.yml` (3 job 構成):

- `test`: pyvisa あり + 全 suite (hardware 除外)
- **`pyvisa-not-installed`**: pyvisa を uninstall した状態で runtime
  module import / `dependency_report` / `test_separation_boundary.py` /
  validate / catalog CLI smoke を実行
- `lint`: repo 全体の LF / multi-line guard

### P1: `extension doctor` instrument quality summary

`doctor_extension()` の `summary` に **`instrument_quality`** subdict
を追加:

```json
{
  "instrument_quality": {
    "total": 5,
    "strict_passed": 3,
    "strict_failed": 2,
    "missing_verify_commands": 4,
    "missing_safe_shutdown_instruments": 1,
    "manual_ref_todo_instruments": 2
  }
}
```

### P1: `visa-mcp instrument promote-check` (minimal)

```bash
visa-mcp instrument promote-check <path> --target tested|verified [--json]
```

- 内部的に `validate_instrument_file(strict=True)` を再利用
- `eligible: true/false` + `blocking_issues` + `recommended_actions` を返却
- 下方移動 (verified → tested 等) は即 eligible
- `target=verified` のみ `validation_evidence` 必須を追加

### 新規 API

- `visa_mcp.registry.normalize_category(category)` → str
- `visa_mcp.registry.OUTPUT_CAPABLE_CATEGORIES` / `CATEGORY_ALIASES`
- `visa_mcp.registry.validate_instrument_file(path, *, strict=False)`
  (新 keyword)
- `visa_mcp.instrument_authoring.promote_check_instrument(path, *,
  target)` → `PromoteCheckResult`
- `python -m visa_mcp.dev.dependency_report`

### 新規 docs

- **`docs/separation/notes.md`** (v1.10 で機械可読 manifest に昇格予定):
  v1.9 で固定した境界 / v2.0 分割の最終ゴール / PDF extractor extras
  candidate / install path 段階移行 / tool registration boundary

### 新規 error_class

- `instrument_manual_ref_todo`
- `instrument_missing_verify`
- `instrument_missing_safe_shutdown`
- `instrument_missing_safety_ratings`
- `instrument_verified_missing_evidence`

### 互換性

- `validate_instrument_file` の signature: `strict` は keyword-only で
  default `False`。既存呼び出しは無変更で動く
- 既存 lint warnings は不変
- MCP tool / DSL / extension pack 形式すべて不変
- Stable 43 / Experimental 7 / 合計 50 不変

### v1.9 で **やらないこと** (v1.10〜v1.11 へ)

- backend adapter 実体化 (v1.11)
- `module_ownership.yaml` 機械可読版 (v1.10)
- `instrument review-report` フル機能 (v1.10)
- import 違反の根本 refactor (v1.11)
- PyVISA を base install から外す (v2.0)

---

## v1.8.1 — v1.8.0 レビュー応答 (template 外部化 / dmm 統一 / .bak / rollback test)

合言葉: **「template を読める形にし、authoring の事故を防ぐ」**

v1.8.0 の external review (P0/P1) を反映した patch release。
public API / 既存 CLI 引数 / `.install_meta.json` 形式すべて不変。
**生成 YAML の出力は v1.8.0 と互換** (template 外部化は実装手段のみ変更)。

### 変更点

- **P0-1**: v1.8 関連 file 13 件 (src / docs / tests / CHANGELOG /
  CONTRIBUTING + 4 つの新規 **`templates/instruments/<cat>.yaml`**) の
  LF / multi-line を parametrized test 化 (`tests/test_v181_review.py`)
- **P0-2**: 生成 YAML の multi-line + `yaml.safe_load` +
  `validate_instrument_file` を **category ごと**に再保証
  (power_supply は 80 行超、他は 25 行超)
- **P1-3** (`src/visa_mcp/templates/instruments/`):
  4 category template を **外部 YAML file 化**
  (`power_supply.yaml` / `dmm.yaml` / `temperature_meter.yaml` /
  `generic_scpi.yaml`)。Python 同梱の巨大文字列を撤廃。読み込みは
  `importlib.resources` (Jinja2 依存なし)。置換は `str.replace` で
  `{manufacturer}` / `{model}` の 2 placeholder のみ
  (SCPI の `{voltage}` 等のブレースを壊さないため)。
  `pyproject.toml [tool.hatch.build.targets.wheel.force-include]` で
  wheel に同梱。
- **P1-4** (`templates/instruments/dmm.yaml`):
  `metadata.category` を **`multimeter` → `dmm`** へ統一 (CLI
  `--category dmm` と同一値)。回帰防止 test:
  `test_each_category_template_metadata_category_matches_cli`
- **P1-5** (各 template `reset` description):
  CAUTION 文言 (「device-specific behavior / Verify against manual」)
  を追加。`power_supply.safety.cautions` にも
  "Verify *RST behavior" を追記。
- **P1-6** (`scaffold_instrument_definition`):
  `--force` で既存 YAML を上書きする場合、**`.bak-<UTC ts>` backup**
  を自動作成。`instrument_scaffold_force_backup` warning で報告。
  backup cleanup は手動 (review 後に削除)。
- **P1-7** (`tests/test_v181_review.py`):
  `extension add-instrument` の rollback テストを強化:
  - `test_rollback_restores_extension_yaml`
  - `test_rollback_restores_registry_index`
  - `test_rollback_removes_new_instrument_file`
  - `test_rollback_preserves_existing_instrument_file` (--force 上書き
    後の rollback で元内容に戻る)
  monkeypatch で post-update validate を強制失敗させる手法。
- **P1-8** (`docs/instrument_authoring.md`):
  `manual_ref` 記入例 4 件 (Kikusui / Keysight / Rigol / URL) を追加。
  category 統一の docs section、`--force` backup の挙動 docs も追加。

### 新規 warning_class

- `instrument_scaffold_force_backup`

### 互換性

- 生成 YAML 出力は v1.8.0 と等価 (template 内容を意図的に変更した
  箇所: `dmm.category` 変更 / `reset.description` 強化 / `cautions`
  追記 — いずれも instrument validation を error 0 で通る範囲)
- `scaffold_instrument_definition` の signature 不変
- `add_instrument_to_pack` の signature 不変
- CLI 引数 / public API すべて不変
- Stable 43 / Experimental 7 / 合計 50 不変

### Wheel packaging

`src/visa_mcp/templates/` 配下の YAML を wheel に同梱
(`[tool.hatch.build.targets.wheel.force-include]`)。`pip install
visa-mcp` 後も `instrument scaffold` が動く。

---

## v1.8.0 — Instrument Definition Authoring

合言葉: **「instrument YAML を作りやすくする」**

v1.7 で **空 directory から pack 雛形** が作れるようになった。v1.8 では
pack の中身、つまり **instrument YAML を category 別 template から
scaffold** できるようにし、pack に **`add-instrument`** で登録する
ところまで CLI 化する。

> MCP tool 追加ゼロ (Stable 43 / Experimental 7 / 合計 50 不変)。
> PDF → YAML 自動抽出 / LLM 自動定義生成 / 実機自動検証 / remote
> registry / Python plugin / backend plugin にはまだ進まない。

### 新規 CLI subcommands

```bash
# 単体 instrument YAML を生成 (pack 非依存)
visa-mcp instrument scaffold <category> --output <file>
    [--manufacturer "..."] [--model "..."] [--force] [--json]

# pack に instrument を追加 (scaffold + extension.yaml 更新 + registry index)
visa-mcp extension add-instrument <pack_dir>
    --id <instrument_id> --category <category>
    [--manufacturer "..."] [--model "..."]
    [--dry-run] [--force] [--json]
```

`<category>` は `power_supply` / `dmm` / `temperature_meter` /
`generic_scpi` の 4 種。

### 新規 module

**`src/visa_mcp/instrument_authoring.py`**:

- `scaffold_instrument_definition(category, *, output, manufacturer,
  model, force)` → `ScaffoldResult`
  - 4 category template (multi-line YAML)
  - 生成 YAML は **`support_level: draft`** 固定
  - `metadata.manual_ref` に必ず TODO placeholder
  - 冒頭に「THIS IS A DRAFT DEFINITION」コメント
  - 生成直後に `validate_instrument_file` を error 0 で通る
- `add_instrument_to_pack(pack_dir, *, instrument_id, category, ...,
  dry_run, force)` → `AddInstrumentResult`
  - 既存 instrument file: `--force` 必須
  - **registry id 重複は `--force` でも拒否**
  - pack 事前 validate / 更新後 validate を実施
  - 更新後 validate 失敗時は **rollback** (instrument YAML /
    extension.yaml / registry_entries/INDEX.yaml の元 state 復元)
  - `--dry-run`: file 書き込み・lockfile 編集ゼロ + `changes_preview`

### power_supply template の中身 (v1.8)

AI agent 向けに「安全な構造」を最初から含む:

- `safety.ratings` (voltage / current の rated / absolute_max /
  recommended_max / unit)
- `safety.preconditions` (`set_output ON` 前に
  `set_voltage` + `set_current_limit` 必須、severity: high)
- `safe_shutdown` (`OUTP OFF` → `VOLT 0` の 2 step)
- `state_query` (voltage_set / current_limit / voltage_measured /
  current_measured / output_state + ON/OFF map)
- `verify` (`set_voltage` / `set_current_limit` の read-back tolerance)
- `polling_safe: true` (query 系)

### 新規 docs

- **`docs/instrument_authoring.md`**: scaffold → validate →
  add-instrument workflow / category 別 template / draft policy /
  manual_ref / support_level 昇格目安
- `CONTRIBUTING.md`: 「Instrument definition の追加」section + PR
  checklist を追加

### 新規 error_class

- `instrument_scaffold_unknown_category`
- `instrument_scaffold_target_exists`
- `add_instrument_invalid_id`
- `add_instrument_target_exists`
- `add_instrument_duplicate_registry_id`
- `add_instrument_pack_invalid`
- `add_instrument_rolled_back`

### 新規 warning_class

- `instrument_definition_draft`

### 互換性

- 既存 CLI 引数 / public API / schema すべて不変
- 新規 top-level `instrument` subcommand と `extension add-instrument`
  は完全新規
- Stable 43 / Experimental 7 / 合計 50 不変

### v1.8 で対応しない (v1.9+ 候補)

- PDF / datasheet → YAML 自動抽出の本格化
- LLM 完全自動生成 (人間 review 前提)
- 実機自動検証 (CI hardware loop)
- `instrument lint` 専用 CLI (現状 `validate instrument` で代替)
- remote registry / Python plugin / backend plugin

---

## v1.7.1 — v1.7.0 レビュー応答 (--force warning / doctor 分類表示 / docs 補強)

合言葉: **「authoring の挙動と判定基準を contributor に分かりやすく伝える」**

v1.7.0 の external review (P0/P1/P2) を反映した patch release。
public API / 既存 CLI 引数 / 生成 schema すべて不変。

### 変更点

- **P0**: v1.7 関連 file 16 件 (src / docs / tests / README / CONTRIBUTING /
  CHANGELOG) の LF / multi-line を parametrized test 化
  (`tests/test_v171_review.py`)
- **P1-2** (生成 YAML 妥当性):
  全 template (`minimal` / `mock_basic` / `instrument_pack`) について
  - 生成 `extension.yaml` が `> 15 行` の multi-line layout
  - `yaml.safe_load` で round-trip 成功
  - 期待 keys (`extension_id`, `name`, `version`, `type`, `contents`,
    `stability`, `catalog`) 揃い
  - `stability.executable_code == False` / `support_level == "draft"`
  - `catalog.license == "MIT"`
  - `validate_extension_file` が **error 0** で通る
  を回帰防止テスト化 (`test_v171_review.py`)
- **P1-3** (`init_extension_pack` + `docs/extension_authoring.md`):
  `--force` で既存 directory 内に **scaffold が生成しない file
  (手書き file 等) が残る**仕様を:
  - 新 warning **`extension_init_force_retains_files`** で件数・sample
    を返却
  - docs に「scaffold で誤って成果物を消す事故を防ぐためのポリシー」と
    明記、完全クリーンが必要な場合の手順 (rm -rf → init) も記載
- **P1-4** (`docs/extension_authoring.md`):
  `ready_to_package` (local zip 化の最低条件) vs
  `ready_for_registry_review` (publishing / PR / registry 掲載の品質
  条件) の **対比表 + gate 列**を追加し、`ready_to_package=true` でも
  `ready_for_registry_review=false` が自然にあり得ることを強調
- **P1-5** (`cli.py` doctor handler):
  human-readable 出力を **3 グループに分類表示**:
  - `Errors (block package)`
  - `Warnings (quality)`
  - `Strict-only issues (must fix before registry / publishing)`
  - `Recommended actions`
  CLI 出力からも contributor が次に何を直すべきかが直感的に分かる
- **P1-7** (`CONTRIBUTING.md`):
  「データ取り扱いポリシー」section を新規:
  - 機材 / 安全 (LLM 生成は draft 必須)
  - **計測器マニュアル / SCPI 表 / proprietary 情報** (出典 /
    `metadata.manual_ref` 必須、NDA 内容を公開 PR に含めない)
  - **認証情報 / raw data** (API key / password / IP / Serial 等を
    YAML に書かない、`_system.yaml` で解決)
  - **LLM-generated content** (人間目視確認、`support_level=verified`
    は LLM のみで判定しない)
- **P2-8** (`docs/extension_authoring.md`):
  v1.8+ で scaffold template を `src/visa_mcp/templates/extensions/`
  へ外部化 (`importlib.resources` 経由、Jinja2 依存は引き続き入れない)
  という TODO を記録

### 新規 warning_class

- `extension_init_force_retains_files`

### 互換性

- `init_extension_pack` の signature / 戻り値 schema は不変 (warning が
  追加されるのみ)
- CLI 引数 / 生成 YAML 形式 / public API すべて不変
- doctor の JSON 出力 schema は不変 (CLI の human-readable のみ強化)
- Stable 43 / Experimental 7 / 合計 50 不変

---

## v1.7.0 — Definition Pack Authoring Assistant / Scaffolding

合言葉: **「良い definition pack を作りやすくする」**

v1.2〜v1.6 で「定義 → install → check → package → catalog / install」
までは揃った。v1.7 では **入口側 (authoring)** を支援する CLI を 3 本
追加し、外部 contributor / 将来の自分が空の directory から安全な pack
を作れる状態にする。

> MCP tool 追加ゼロ。CLI 専用 (Stable 43 / Experimental 7 / 合計 50 不変)。
> remote registry / Python plugin / signature / AI-assisted authoring
> には進まない。

### 新規 CLI subcommands

```bash
visa-mcp extension init <pack_name>
    [--target-dir <dir>] [--id <ext-id>]
    [--template minimal|mock_basic|instrument_pack]
    [--author "<name>"] [--force] [--json]

visa-mcp extension package <ext.yaml> --dry-run [--strict] [--json]

visa-mcp extension doctor <ext.yaml> [--strict] [--json]
```

### 新規 module

**`src/visa_mcp/extension_authoring.py`**:

- `init_extension_pack(pack_name, *, target_dir, extension_id,
  template, author, force)` → `InitResult`
  - templates: `minimal` / `mock_basic` / `instrument_pack`
  - 生成された pack は即 `validate extension` を通る
  - catalog metadata の雛形 (summary / license / authors /
    safety_notes) を含む。`support_level: draft` /
    `executable_code: false`
- `package_dry_run(extension_yaml, *, strict)` → dict
  - zip を**作らず**、`files_included` / `files_excluded` /
    `package_manifest_preview` / `checksums_preview_count` を返す
  - 通常 package と同じ validation を経由
- `doctor_extension(extension_yaml, *, strict)` → `DoctorReport`
  - `validate` + `strict validate` + `package dry-run` + catalog /
    README / license / verified evidence を 1 ステップで実行
  - 構造化された **`recommended_actions`** + `summary` を返す
  - `summary.ready_to_package` / `ready_for_registry_review` で
    publishing 判断を即時提示

### 新規 docs

- **`docs/extension_authoring.md`**: scaffold → doctor → package
  workflow を 1 本道で整理。各 CLI の引数 / 出力 / template 比較表
- **`CONTRIBUTING.md`** (新規): definition pack PR checklist /
  bug fix flow / ポリシー / CoC

### 新規 error_class

- `extension_init_unknown_template`
- `extension_init_target_exists`
- `extension_init_invalid_id`

### 互換性

- 既存 CLI 引数 / public API / schema すべて不変
- `package --dry-run` は新規 flag で既存挙動を変えない
- `extension init` / `doctor` は完全新規
- Stable 43 / Experimental 7 / 合計 50 不変

### v1.7 で対応しない (v1.8+ 候補)

- `extension add-instrument` / `extension add-template`
  (instrument 1 件追加の専用 CLI)
- author profile の永続化 (`~/.visa-mcp/profile.yaml`)
- AI-assisted authoring (LLM に PDF → YAML を起こさせる)
- remote registry / pull CLI
- Python plugin / backend plugin
- replay backend 実装

---

## v1.6.1 — v1.6.0 レビュー応答 (schema catalog / zip 上限 / verification status)

合言葉: **「catalog を使う側」に必要な明確性と安全余白を足す**

v1.6.0 の external review (P0/P1/P2) を反映した patch release。
public API / 既存 schema / CLI 引数は不変。

### 変更点

- **P0**: v1.6 関連 file 18 件の LF / multi-line を parametrized test 化
  (`tests/test_v161_review.py`)
- **P1-2** (`schemas/extension_manifest.schema.json`):
  schema 再生成で `catalog` property を反映 (Pydantic
  `ExtensionManifest.catalog` から auto-emit)
- **P1-3** (`examples/.../extension.yaml`):
  multi-line layout を test で保証 + `yaml.safe_load` round-trip /
  `catalog` block の dict 構造を test 化
- **P1-4** (`extension_catalog.py::inspect_package`):
  軽量 zip slip check 追加。`inspect_package_unsafe_member` warning
- **P1-5** (`extension_install.py::install_definition_pack_from_zip`):
  zip 構造制約を明示化:
  - `extension.yaml` は zip root 直下必須
    (`extension_install_zip_no_root_manifest`)
  - file 数上限 5000 (`extension_install_zip_too_many_files`)
  - uncompressed total 200 MB (`extension_install_zip_too_large`)
  - tmp cleanup は `finally` で必ず実行 (旧来挙動を明文化)
  - `docs/extension_install.md` に「zip 構造の要件」追加
- **P1-6** (`extension_catalog.py::quality_signals`):
  `null` の意味を明示する補助 field:
  - `package_verification_status`: `"not_checked"` / `"verified"` /
    `"failed"`
  - `strict_validation_status`: `"not_checked"` / `"passed"` /
    `"failed"`
  既存 `*_passed` (true/false/None) は不変 (後方互換)
- **P1-7** (`tests/test_v161_review.py`):
  zip 由来 `installed_from.kind="package"` が
  `extension catalog --installed` 経由で
  `package_path` / `package_sha256` / `package_format_version` 付き
  で取得できる E2E test
- **P2-8** (`docs/extension_catalog.md`):
  top-level `author` vs `catalog.authors` の役割対比表を追加。
  v1.6+ は `catalog.authors` 推奨と明記
- **P2-9** (`docs/error_taxonomy.md`):
  v1.6 / v1.6.1 の **Zip install error 7 件**を新規 section、
  Strict 表に `strict_missing_catalog_*` 追記

### 新規 error_class

- `extension_install_zip_no_root_manifest`
- `extension_install_zip_too_many_files`
- `extension_install_zip_too_large`

### 新規 warning_class

- `inspect_package_unsafe_member`

### 互換性

- `catalog` field は引き続き完全 optional
- `quality_signals` の新 field 2 件は追加のみ (consumer 無視可)
- `.install_meta.json` 形式 / CLI 引数 / public API signature 不変
- Stable 43 / Experimental 7 / 合計 50 不変

---

## v1.6.0 — Definition Pack Discovery / Catalog Metadata + Local zip install

合言葉: **「package できる」を「どの pack を使うべきか判断できる」に繋ぐ**
(同時に「そのまま install できる」も実装)

v1.5 までで pack の作成・配布・整合性確認は揃った。v1.6 では (1) 選定 /
比較のための **catalog metadata + discovery CLI**、(2) `.visa-mcp-ext.zip`
からの **local zip install**、を CLI に追加する。
提案の「実用優先」path を採用し、両方を同 release に含める。

### 新規 MCP ツール: **ゼロ** (Stable 43 / Experimental 7 / 合計 50 不変)

### 新規 CLI subcommands

```bash
visa-mcp extension catalog [--installed | --packages <dir>] [--json]
visa-mcp extension inspect-package <zip-path> [--json]
visa-mcp extension install <path-to-pack.visa-mcp-ext.zip>   # 拡張子 auto-route
```

### Catalog / Discovery (主テーマ)

#### `extension.yaml` の `catalog` field (v1.6 新規、任意)

```yaml
catalog:
  summary: "..."           # 1 行紹介。strict で空 → error
  description: "..."       # 詳細紹介
  authors: [{name: "..."}]
  license: "MIT"           # SPDX 推奨。strict で空 → error
  homepage: "https://..."
  tags: [...]
  categories: [...]
  target_users: [...]
  safety_notes: [...]
```

すべて optional。`extension package --strict` で `summary` / `license`
空は error。

#### 新規 module `src/visa_mcp/extension_catalog.py`

- `support_level_summary(pack_dir, instrument_rels)` →
  `{verified, tested, experimental, draft}` 件数
- `quality_signals(manifest, pack_dir, *, package_verified=None,
  strict_validation_passed=None)` → **数値 score を返さず**、
  boolean / count の構造化シグナル (`has_readme`,
  `has_catalog_summary`, `has_validation_evidence`, `*_instruments`
  等)
- `list_catalog_installed(...)` → installed pack を catalog 形式で
  一覧化
- `list_catalog_packages(dist_dir)` → `.visa-mcp-ext.zip` を catalog
  形式で一覧化
- `inspect_package(zip)` → install せずに zip 中身の catalog /
  contents / signals / package_manifest を返す (軽量読み取り)

#### 設計判断: score 化しない

`quality_signals` は **boolean / count のみ**。`quality_score: 85`
のような数値は返さない (提案 P4)。

- 評価基準が未成熟
- AI エージェントが数値を過信しやすい
- 単一 score は「なぜそうなったか」を隠す

各次元独立 signal で「何が足りないか」を直接読み取らせる。

### `installed_from` (v1.6 新規、`.install_meta.json`)

install 元を構造化記録。

| 値 | 意味 |
|----|------|
| `{kind: "directory", source_path}` | extension.yaml から直接 install |
| `{kind: "package", package_path, package_sha256, package_format_version}` | .visa-mcp-ext.zip から install |

後の audit / bundle で「この pack はどの配布物から入れたか」を辿る。

### Local zip install (実用優先 path)

`visa-mcp extension install` が `.zip` (`.visa-mcp-ext.zip` 含む) を
受け付ける。CLI は **拡張子で auto-route**:
- `.zip` → zip install 経路 (新規)
- それ以外 → 従来の extension.yaml 経路

#### 新規 API: `install_definition_pack_from_zip(...)`

Flow:
1. `verify_extension_package()` を必ず通す (zip slip / 絶対 path /
   checksum / executable_code / re-validate)。`skip_verify` は test
   のみ。
2. zip を tmp directory に展開 (二重 zip-slip check)
3. tmp 内 `extension.yaml` を既存 `install_definition_pack()` フロー
   に流す
4. `.install_meta.json.source_path` を **zip path** に書き換え、
   `source_format: "visa-mcp-extension-package"` と
   `installed_from.kind: "package"` を追加記録

### 新規 docs

- **`docs/extension_catalog.md`**: catalog metadata 仕様 / CLI 役割分担 /
  quality_signals の設計判断 / installed_from / v1.7+ ロードマップ
- `docs/extension_install.md`: v1.6 zip install フロー追記
- `docs/extension_packaging.md`: v1.6 で zip install 対応済み旨を追記

### 新規 error_class / warning_class

新規 error_class:
- `extension_install_zip_invalid`
- `extension_install_zip_verify_failed`
- `extension_install_zip_unsafe`
- `extension_install_zip_no_manifest`
- `strict_missing_catalog_summary`
- `strict_missing_catalog_license`

新規 warning_class:
- `missing_catalog_summary`
- `missing_catalog_license`
- `installed_pack_unreadable`
- `package_unreadable`
- `package_missing_manifest`

### 互換性

- `catalog` field は **完全 optional** (default 空)。既存 pack は無修正
  で動く
- `install_definition_pack(extension.yaml)` の signature / 挙動は不変
- `.install_meta.json`: 既存 field は不変、`source_format` /
  `installed_from` は追加 (consumer が無視できる)
- 既存 v1.5.x install 済み pack はそのまま check / inspect /
  uninstall / catalog 可
- Stable 43 / Experimental 7 / 合計 50 不変

### v1.6 で受け付けない (v1.7+ 候補)

- remote URL / git からの install
- remote registry / pull CLI
- quality **score** 化 (signals に留める方針継続)
- signature / trust store / 公開鍵検証
- automatic update
- Python plugin / entry_points discovery

## v1.5.1 — v1.5.0 レビュー応答 (CLI help / docs 明確化 / overlay 反映テスト)

合言葉: **「package できる」を、迷わず使えるようにする**

v1.5.0 の external review (主に docs / help / repo 品質) を反映した
patch release。挙動・schema 変更なし。

### 変更点

- **P0**: v1.5 関連 file の LF / multi-line を parametrized test 化
  (`tests/test_v151_review.py`)。新規 doc / module / schema を全カバー。
- **P1-2** (`cli.py`):
  - `extension package --help` / `extension verify-package --help` に
    **使用例** (epilog) と **検査項目 / strict mode 説明**を追加
  - `RawDescriptionHelpFormatter` で改行 / 箇条書きを保持
  - 各 argument の help を強化 (用途例 / CI 用などを明記)
- **P1-3** (`docs/extension_packaging.md`):
  Normal vs Strict mode の **比較表**を section として追加。
  10 行で `empty_contents` / `support_level=draft` /
  `validation_evidence` / `README.md` / `registry_entries`
  深掘り / `extension_extra_file` までを 1 望み。
- **P1-4** (`docs/extension_packaging.md`):
  `package_manifest.json` の **Field 仕様表** を追加 (型 / 説明)。
  `package_format_version` の **後方互換ポリシー** (minor up = field
  追加のみ / major up = breaking) も明記。
- **P1-5** (`tests/test_v151_review.py`):
  package → verify → 展開 → install → overlay registry に反映、
  までの **end-to-end** テスト 2 本追加。
  - 例 pack (registry_entries なし) → installed_extensions 反映
  - registry_entries 付き pack → overlay に extension 由来 entry 出現
    + `source.extension_id` / `extension_version` 整合

### 互換性

- public API / CLI 引数 / package 形式すべて不変
- `package_format_version` は `"1.0"` のまま
- Stable 43 / Experimental 7 / 合計 50 不変

---

## v1.5.0 — Definition Pack Packaging / Publishing Preparation

合言葉: **「作れる / install できる / 整合できる」→「配布可能な成果物として
まとめられる」**

v1.4 で integrity check / strict validation が整った。v1.5 では、外部
contributor が作った definition pack を **配布可能 zip パッケージ**
にまとめ、受け取り側で **再検証**できる仕組みを CLI に追加する。

> v1.5 は **packaging まで**。zip からの install / remote install /
> signature には進まない (v1.6+ 候補)。

### 新規 MCP ツール: **ゼロ** (Stable 43 / Experimental 7 / 合計 50 不変)

extension package / verify-package も管理操作のため CLI に閉じる。
AI エージェント MCP surface は v1.0 以降一貫して 50 のまま。

### 新規 CLI subcommands

```bash
visa-mcp extension package <extension.yaml>
    [--output <dir>] [--strict] [--json]
visa-mcp extension verify-package <zip-path> [--json]
```

### 新規 module

**`src/visa_mcp/extension_packaging.py`**:

- `package_definition_pack(extension_yaml, *, output_dir, strict=False)`
  → `PackageResult`
- `verify_extension_package(zip_path)` → `VerifyResult`
- 定数: `PACKAGE_FORMAT="visa-mcp-extension-package"`,
  `PACKAGE_FORMAT_VERSION="1.0"`, `PACKAGE_SUFFIX=".visa-mcp-ext.zip"`

### Package 形式

```
<extension_id>-<version>.visa-mcp-ext.zip
├── extension.yaml
├── package_manifest.json          ← v1.5 必須
├── checksums.sha256               ← v1.5 必須 (sha256sum 互換)
├── README.md                      (任意、--strict で error 候補)
├── instruments/ benchmarks/ templates/ ...
```

**`package_manifest.json`** に持つ field:
- `package_format` / `package_format_version`
- `extension_id` / `extension_version`
- `created_at` / `created_by`
- `executable_code: false` (v1.5 で恒に false)
- `file_count` / `files: [{path, sha256}, ...]`
- `checksums_file: "checksums.sha256"` / `checksums_sha256`

### package 時の検査

1. `validate_extension_file(strict=...)` を必ず通す
2. 除外ルール (`.git/`, `__pycache__/`, `*.pyc`, `.DS_Store` 等)
3. zip-slip / 絶対 path / `..` の二重 check
4. deterministic な順序で zip 化 (sorted by rel path)
5. zip 全体の sha256 を返却

### verify-package の検査

- zip として読める
- すべての member が **zip slip safe** (絶対 path / drive letter /
  `..` を拒否)
- `extension.yaml` / `package_manifest.json` / `checksums.sha256` 必須
- `package_manifest.executable_code: true` を error
- zip 内 file の sha256 vs `checksums.sha256` / `manifest.files[*].sha256`
- tmp 展開後に `validate_extension_file()` を再実行

### strict mode の追加チェック

- `support_level=verified` で `validation_evidence` 空 →
  `strict_verified_requires_evidence` (v1.4 から)
- pack に `README.md` が無い → **`strict_missing_pack_readme`** (v1.5 新規)
- v1.4.1 で導入した `strict_registry_entry_*` 系も包含

### 新規 error_class

- `extension_validation_failed`
- `empty_package`
- `package_path_unsafe`
- `package_invalid_zip`
- `package_zip_slip`
- `package_missing_required_file`
- `package_manifest_invalid`
- `package_format_invalid`
- `package_executable_code_true`
- `package_checksum_mismatch`
- `package_file_missing`
- `package_manifest_sha_mismatch`
- `strict_missing_pack_readme`

### 新規 warning_class

- `missing_pack_readme`
- `package_extra_file`

### 新規 docs

- **`docs/extension_packaging.md`**: package 形式 / 検査 / strict mode /
  v1.6+ ロードマップ
- **`docs/extension_publishing_checklist.md`**: 配布前 / registry PR
  前のチェックリスト (10 セクション)

### 互換性

- 既存 install 機能・schema は変更なし
- package zip は v1.6+ で local install 元として受け取る予定 (forward
  compatibility のため `package_format_version` を持たせた)
- Stable 43 / Experimental 7 / 合計 50 不変

### v1.5 で対応しない (v1.6+ 候補)

- zip からの install (`visa-mcp extension install <zip>`)
- remote URL / git からの install
- registry pull CLI
- signature / trust store / 公開鍵検証
- automatic update
- Python plugin / entry_points discovery

---

## v1.4.1 — v1.4.0 レビュー応答 (strict 整合 / inspect 明示 / taxonomy 整理)

合言葉: **「strict mode の挙動を一貫させ、inspect が何を見ているかを明示する」**

v1.4.0 の external review (P0/P1) を反映した patch release。
MCP tool 追加ゼロ、互換変更なし。

### 変更点

- **P0**: 新規 / 変更 file の LF / multi-line を v1.4.1 でも parametrized
  test 化 (`tests/test_v141_review.py`)。CR / 1 行潰れを raw 上で検知。
- **P1-2** (`extension_integrity.py::check_installed_extension`):
  `strict=True` 時に **`validate_extension_file(strict=True)` を呼ぶ**
  ように修正。これにより `extension check --strict` でも
  `strict_support_level_draft` / `strict_verified_requires_evidence`
  等の strict-only error が拾える。
- **P1-3** (`models/instrument_def.py`):
  `validation_evidence` の docstring を「strict mode で空のとき
  `strict_verified_requires_evidence` **error**」と修正
  (旧コメントは "warning" 表記でリリースノートと不整合)。
- **P1-4** (`extension_integrity.py::InspectReport`):
  `inspect` が **軽量チェック**であることを JSON 上で明示。
  - `integrity_check_level: "light"` を返却 dict に追加
  - `full_check_tool: "visa-mcp extension check <id>"` を併記
  - docstring に「sha256 drift 検査は `check` を使う」と明記
- **P1-5** (`extension.py::validate_extension_file` strict ブロック):
  strict mode で `registry_entries` 内 entry の **深掘り検査**を追加:
  - `id` / `path` 必須 (`strict_registry_entry_missing_id` /
    `_missing_path`)
  - `vendor` / `model` / `category` / `support_level` 必須
    (`strict_registry_entry_missing_<field>`)
  - `support_level` 値域チェック
    (`strict_registry_entry_invalid_support_level`)
  - path が pack 外を指していないか
    (`strict_registry_entry_path_outside_pack`)
  - 参照先 instrument YAML との `support_level` 一致
    (`strict_registry_entry_support_level_mismatch`)
- **P1-6** (`docs/error_taxonomy.md`):
  Extension 系 error_class taxonomy を新規 section として整理。
  Manifest validation / Install / Overlay registry / Integrity /
  Strict validation の 5 グループに分類、関連 warning_class も併記。
  「v1.0 で凍結した MCP error taxonomy とは別グループ (CLI 専用)」
  であることを明記。
- **P1-7** (`docs/extension_integrity.md`):
  `extension uninstall --dry-run` と通常 `uninstall` の差を表で明示
  (rmtree / lockfile 編集の有無、返却 field、終了コード)。strict
  mode の用途分け (ローカル / CI / registry / release) も追加。

### 新規 error_class (strict validation 深掘り)

- `strict_registry_entry_missing_id`
- `strict_registry_entry_missing_path`
- `strict_registry_entry_missing_vendor`
- `strict_registry_entry_missing_model`
- `strict_registry_entry_missing_category`
- `strict_registry_entry_missing_support_level`
- `strict_registry_entry_invalid_support_level`
- `strict_registry_entry_path_outside_pack`
- `strict_registry_entry_support_level_mismatch`

### 互換性

- `validate_extension_file` / `check_installed_extension` の signature
  は不変 (引数追加なし、`strict` keyword は v1.4.0 から)
- `InspectReport.to_dict()` に 2 field 追加 (`integrity_check_level`,
  `full_check_tool`)。既存 consumer は新 key を無視するだけで動く。
- 通常 (`--strict` 無し) validate は挙動不変
- Stable 43 / Experimental 7 / 合計 50 不変

---

## v1.4.0 — Installed Definition Pack Integrity / Overlay Registry Inspection

合言葉: **「install できる」→「install したものを信頼して使い続けられる」**

v1.3 で local user 領域へ definition pack を install できるようになった。
v1.4 では、install 済み pack の **整合性検査 (sha256 drift)** と
**overlay registry の可視化**、および **strict validation mode** を CLI に
追加する。引き続き remote install / Python plugin / backend plugin /
replay 実装には進まない。

### 新規 MCP ツール: **ゼロ** (Stable 43 / Experimental 7 / 合計 50 不変)

v1.4 も MCP surface を増やさない。新機能は **CLI 専用**。
extension integrity check は管理操作で、実験中の AI エージェントが頻繁に
呼ぶものではないため、CLI 側に閉じている。

### 新規 CLI subcommands

```bash
visa-mcp extension check [<extension_id>] [--strict] [--json]
visa-mcp extension inspect <extension_id> [--json]
visa-mcp extension uninstall <extension_id> --dry-run [--json]
visa-mcp registry overlay [--source builtin|extension] [--json]
visa-mcp validate extension <path> --strict
```

### 新規 module

**`src/visa_mcp/extension_integrity.py`**:

- `check_installed_extension(extension_id, *, strict=False, ...)`:
  sha256 drift / missing / extra / extension.yaml 再 validate
- `check_all_installed_extensions(*, strict=False, ...)`
- `inspect_installed_extension(extension_id, ...)`:
  metadata + contents summary + registry_entry_ids
- `uninstall_dry_run(extension_id, ...)`:
  削除予定 path / file 数 / overlay id を返す

### integrity 値

| 値 | 意味 |
|----|------|
| `ok` | 全 checksum 一致 |
| `modified` | install 後に file 変更あり |
| `missing_file` | 記録 file が消えている |
| `extra_file` | metadata 外の file が増えている (warning) |
| `invalid` | `.install_meta.json` 無し / re-validate 失敗 |

### strict mode

`validate extension --strict` で以下を error 化:

- `empty_contents` warning → `strict_empty_contents` error
- `registry_entries_format` warning → `strict_registry_entries_format` error
- 参照 instrument の `support_level=draft` → `strict_support_level_draft`
- `support_level=verified` で `validation_evidence` 空 →
  `strict_verified_requires_evidence`

`extension check --strict` でも warning (`extension_extra_file` 等) を
error に格上げ。

### 新規 schema field (任意)

**`metadata.validation_evidence`** (instrument YAML、`dict[str, Any]`):

- `support_level=verified` の実質的根拠を構造化
- 例: `tested_by`, `tested_at`, `interface`, `firmware`, `tested_items`, `notes`
- v1.4 では schema レベルで subkey 検証しない (freeform)
- strict mode で空のとき error

### 新規 docs

- **`docs/extension_integrity.md`**: integrity 検査 / strict mode /
  validation_evidence 仕様
- 関連 docs (extension_install / extension_registry_overlay / etc.)
  からの cross link を更新

### 新規 error_class / warning_class

新規 error_class:
- `extension_install_path_missing`
- `extension_install_meta_missing`
- `extension_manifest_missing`
- `extension_checksum_mismatch`
- `extension_checksum_unreadable`
- `extension_file_missing`
- `strict_empty_contents`
- `strict_registry_entries_format`
- `strict_support_level_draft`
- `strict_verified_requires_evidence`

新規 warning_class:
- `extension_extra_file`

### 互換性

- 既存 install 済み pack (v1.3.x) はそのまま `extension check` 可能
  (lockfile / `.install_meta.json` 形式は変更なし)
- `validate_extension_file` の signature に `strict` が optional keyword
  として追加された (default False、既存呼び出しは無変更で動く)
- `MetadataConfig.validation_evidence` が optional field として追加
  (default `{}`、既存 instrument YAML は無変更で動く)
- Stable 43 / Experimental 7 / 合計 50 不変

---

## v1.3.1 — v1.3.0 レビュー応答 (atomic install / overlay validation / docs 整合)

合言葉: **「force install でも既存喪失しない / overlay registry を入口で守る」**

v1.3.0 の external review (P0/P1/P2) を反映した patch release。
新規 MCP ツールゼロ、CLI 引数の互換変更なし。

### 変更点

- **P0**: 新規・変更 file の LF / multi-line を v1.3.1 でも parametrized
  test で確認 (`tests/test_v131_review.py`)。
- **P1-2** (`extension_install.py`):
  force install の置換を `rmtree + replace` から **backup-rename** へ
  変更。失敗時に backup を復元するので、既存 install を喪失しなくなった。
- **P1-3** (`extension_install.py::load_overlay_registry`):
  `registry_entries[*].path` が pack 外を指す場合は error
  (`registry_entry_path_outside_pack`)。
- **P1-4** (`extension_install.py::load_overlay_registry`):
  registry entry の `id` / `path` 不足を error
  (`registry_entry_missing_id` / `registry_entry_missing_path`)、
  `vendor` / `model` / `category` / `support_level` 不足を warning。
- **P1-5** (`cli.py`):
  module docstring を `v0.9.2:` から **v1.3** 内容 (extension
  install/list/uninstall/validate-installed + serve) へ更新。
- **P1-6** (`extension_install.py` + `docs/extension_install.md`):
  staging copy が pack directory 内の **全 file** を copy する旨を
  docs に明記。同時に `.git/`, `__pycache__/`, `.mypy_cache/`,
  `.pytest_cache/`, `.idea/`, `.vscode/`, `node_modules/`,
  `.DS_Store`, `Thumbs.db`, `*.pyc`, `*.pyo`, `*.tmp`, `*.swp`
  を **除外** (誤公開・サイズ膨張を予防)。
- **P1-7** (`extension_install.py`):
  install 元 path が `extensions_dir` 配下にある場合は拒否
  (`extension_source_inside_extensions_dir`)。force 時に source 自身を
  消す事故を防ぐ。
- **P2-8/9** (`docs/extension_install.md`):
  v1.4+ 候補として `--builtin-registry` 引数と `source_path` 相対化を
  明記。

### 新規 error sub_class

- `extension_source_inside_extensions_dir`
- `registry_entry_path_outside_pack`
- `registry_entry_missing_id`
- `registry_entry_missing_path`

### 新規 warning_class

- `registry_entry_missing_vendor`
- `registry_entry_missing_model`
- `registry_entry_missing_category`
- `registry_entry_missing_support_level`

### 互換性

- 既存 v1.3.0 で install 済みの `.install_meta.json` / lockfile は
  読み取り互換 (schema 変更なし)。
- 既存 v1.3.0 の CLI 引数・出力は変更なし。
- Stable 43 / Experimental 7 / 合計 50 不変。

---

## v1.3.0 — Local Definition Pack Management (no executable code, no remote)

合言葉: **「definition pack を『作れる』から『安全に導入できる』へ」**

v1.3 では Python plugin / remote install / signature を**実装しない**。
代わりに、v1.2 で検証可能になった definition pack を **ローカル user 領域
へ安全に install / list / uninstall** できるようにし、built-in registry
との **overlay registry** 統合を提供する。

### 新規 MCP ツール: **ゼロ** (Stable 43 / Experimental 7 / 合計 50 不変)

v1.3 は引き続き MCP surface を増やさない。新機能は **CLI のみ**。

### 新規 CLI subcommands

```bash
visa-mcp extension install <path-to-extension.yaml> [--force] [--json]
visa-mcp extension list [--json]
visa-mcp extension uninstall <extension_id> [--json]
visa-mcp extension validate-installed [--json]
```

### 新規 module

**`src/visa_mcp/extension_install.py`**:

- `install_definition_pack(yaml_path, *, force, ...)`:
  validate → duplicate チェック → staging copy → atomic rename →
  sha256 metadata 保存 → lockfile 更新
- `list_installed_packs(...)` → lockfile 一覧
- `uninstall_definition_pack(extension_id, ...)`
- `load_overlay_registry(builtin_path, ...)` → built-in + installed
  packs を統合、duplicate id を error 検出
- 既定 path: `~/.visa-mcp/extensions/<extension_id>/` + lockfile
  `~/.visa-mcp/extensions.lock.json`
- 各 file の sha256 を `.install_meta.json` に記録

### 安全策

- **path traversal / 絶対パス拒否** (v1.2.1 で実装済の
  `validate_extension_file` を install 前に必ず通す)
- **`executable_code: true` は schema レベルで拒否** (v1.2 から継続)
- **Python code 実行なし** (ロード時に import / exec しない)
- **リモート URL からの install なし** (ローカル path のみ)
- **atomic rename** で staging 途中失敗時に中途半端な install を残さない
- **duplicate `extension_id`** はデフォルトで拒否
  (`extension_duplicate_install`)、`--force` で上書き許可

### Overlay registry

```text
effective registry  =  built-in registry  +  installed pack の registry_entries
```

各 entry は `source` を持ち、由来 (`builtin` / `extension`) を区別:

- built-in と extension の id 衝突 → **error** (`overlay_registry_duplicate_id`)
- extension 同士の id 衝突 → **error**
- v1.3 では暗黙の override / 明示 override を **提供しない** (AI agent への安全側)

### 新規 / 更新 docs

| ファイル | 内容 |
|---------|------|
| `docs/extension_install.md` (新規) | install 先 / フロー / metadata / lockfile / 安全策 / duplicate / uninstall / v1.4+ 候補 |
| `docs/extension_registry_overlay.md` (新規) | overlay の意味 / source 区別 / 衝突ルール / API 例 |
| `docs/v1_stability_policy.md` (更新) | extension install を **experimental operational feature** として注記 |

### 新規 error_class (sub_class)

- `extension_duplicate_install` (sub_class、`validation` 経由)
- `extension_validation_failed` (sub_class、`validation` 経由)
- `overlay_registry_duplicate_id` (sub_class、`validation` 経由)

### テスト

`tests/test_v13_extension_install.py` 29 件:

- version v1.3 / Stable 43 不変 / Experimental 7 不変
- install success / writes lockfile / metadata に sha256
- duplicate (force なし → 拒否、force → 上書き)
- invalid pack (executable_code=true) 拒否
- path traversal 拒否
- list (空 / install 後)
- uninstall (実体削除 + lockfile から削除 / not_found)
- overlay registry (builtin のみ / extension 追加 / duplicate id 検出)
- CLI extension help / 空 list
- repo format guard (LF + multiline 4 ファイル × 2)
- docs 必須キーワード (extension_install / overlay / v1_stability)

旧 v1.2 / v1.2.1 version assertion を v1.x 系列全般許容に微調整。

**合計 827 件 passing** (v1.2.1: 798 → v1.3.0: 827)

### 互換性

- **新規追加** のみ (CLI / module / docs)
- **Stable / Experimental MCP tools 不変** (Stable 43 / Experimental 7 / 計 50)
- 既存 example pack (`mock_basic_pack`) は引き続き install 可能
- DB schema / response envelope / error_class core 全て不変

### スコープ外 (v1.4+ 候補)

- リモート URL / git からの install
- digital signature / trust store / automatic update
- Plugin entry_points discovery
- `visa-mcp extension upgrade` 専用フラグ
- 明示 override (extension が builtin を上書きする宣言)
- Remote registry / pull CLI
- Python code 実行

---

## v1.2.1 — v1.2 レビュー対応 (P0/P1/P2)

v1.2.0 外部レビュー対応。新規 MCP ツール / CLI 無し、互換維持。

### P0 確認

- raw 改行: v1.2 関連 12 ファイル全て **LF only / CR=0 / 多行** で正常
  確認 (`tests/test_v121_review.py` の parametrized テストで CI 回帰防止)

### P1 改修

- **P1-2: `extension_manifest.schema.json` から "stable" 表現を除去**
  - `title`: `"Extension Manifest (definition pack) (v1.2 experimental)"`
  - `description`: 「EXPERIMENTAL: ... v1.x 内で変更可能。Not a stable
    plugin API」を明記
  - `_add_preview_metadata` の汎用処理を経由せず schema 個別 override
- **P1-3: `definition_packs.md` の `extension_id` を「reverse-DNS style
  recommended」に表現調整**
  - validator は小文字英数 + `.` / `-` / `_` を受け付ける緩い実装である旨
    も併記
- **P1-4: contents.* path traversal / 絶対パス拒否** (`extension.py`)
  - `_check_path_safety` を追加、`extension_path_outside_pack`
    (`error_class=validation`) を返す
  - 5 sub-section (instruments / benchmarks / templates / mock_scenarios /
    registry_entries) に一律適用
- **P1-5: `validate extension` の保証範囲を `definition_packs.md` に明記**
  - 「保証すること」5 項目 + 「保証しないこと」4 項目 (実機実行 / system
    config 完全 compile / benchmark 実行成功 / pack 全体の semantic
    consistency)
  - 完全 validation は MCP tool `validate_experiment_plan` の役割と明示
- **P1-6: empty_contents の strict mode 昇格候補を明記**
  - 将来 `--strict` フラグで empty_contents → error、registry_entries
    整合性チェック、SemVer range 評価、duplicate id 検出を TODO 化
- **P2-7/8: `visa_mcp_compatibility` は記録用メタデータと明記**
  - 互換 range の厳密評価は将来候補

### テスト

`tests/test_v121_review.py` 35 件:

- repo 12 ファイル × LF + multi-line (24 件)
- extension schema title/description が experimental (2 件)
- definition_packs docs に reverse-DNS / 記録用メタデータ / 保証範囲 /
  strict mode キーワード (4 件)
- path traversal 拒否 (3 件: `../` / 絶対パス / 正常相対パスは通る)
- example pack が引き続き pass (1 件)
- v1.2.1 version (1 件)

**合計 798 件 passing** (v1.2.0: 763 → v1.2.1: 798)

### 互換性

- 動作変更は **contents.* の path traversal / 絶対パス拒否** のみ
  (これまで暗黙に許容されていたが、definition pack の安全性上 reject)
- 既存 `mock_basic_pack` example は引き続き pass (相対パスのみ使用)
- Stable API 不変。experimental スコープのみ修正。

---

## v1.2.0 — Definition Extension Release

合言葉: **「plugin を実装する前に、何を拡張可能にするのかを固定する」**

v1.2 は Python plugin loader を**実装しない**。代わりに、拡張は YAML/JSON
の **definition pack** に集約し、`extension.yaml` manifest schema + CLI
検証 + 4 件の docs で「何が拡張可能か」を固定する。

### 新規 MCP ツール: **ゼロ**

v1.2 は MCP tool を増やさない (Stable 43 / Experimental 7 / 合計 50 不変)。
拡張機能は **CLI / docs / schemas / examples** のみ。

### 新規 CLI subcommand

```bash
visa-mcp validate extension <path-to-extension.yaml> [--json]
```

検査:
- `stability.executable_code: false` (true は **schema validation で拒否**)
- `type: definition_pack` (他値は拒否)
- `extension_id` の reverse-DNS 形式 / `version` の SemVer
- 参照ファイル群 (instruments / benchmarks / templates / mock_scenarios /
  registry_entries) の存在 + それぞれの schema validation

### 新規 module / schema

- **`src/visa_mcp/extension.py`** 新規: `ExtensionManifest` / `ExtensionContents`
  / `ExtensionStability` Pydantic models + `validate_extension_file()`
  + `ExtensionValidationReport`
- **`schemas/extension_manifest.schema.json`** 新規 (experimental スコープ、
  `x-visa-mcp-status="experimental"`, `x-compatibility="subject-to-change-within-v1.x"`)

### 新規 docs (4 件)

| ファイル | 内容 |
|---------|------|
| `docs/extension_policy.md` | v1.2 拡張ポリシー全体 / 5 supported surfaces / 9 NOT supported / data vs code 表 |
| `docs/definition_packs.md` | `extension.yaml` 仕様 / 必須フィールド / CLI 検証手順 / 配布方法 / Python plugin にしない理由 |
| `docs/registry_contribution.md` | 機器定義 contribute 手順 + チェックリスト 9 項目 |
| `docs/replay_backend_concept.md` | replay 設計メモ (実装はしない、何が可能/不可能か、v1.3+ ロードマップ) |

### docs 拡充 (既存)

- **`docs/backend_abstraction.md`**: Backend capability model + Error mapping
  proposal を design memo として追加 (実装はしない)
- **`docs/v1_stability_policy.md`**: definition packs / executable plugin
  未対応 / replay backend concept 注記を追加

### 新規 example

`examples/extensions/mock_basic_pack/`:

```
extension.yaml
instruments/
  mock_psu.yaml
  mock_dmm.yaml
benchmarks/
  task_001.yaml
README.md
```

`visa-mcp validate extension <pack>/extension.yaml --json` でリファレンス
検証可能 (CI で常時 pass)。

### キーポリシー (v1.2)

| 項目 | v1.2 ステータス |
|------|---------------|
| YAML/JSON 定義拡張 (instrument / registry / benchmark / template) | **stable** (各 schema 経由) |
| `extension.yaml` (definition pack manifest) | **experimental** (v1.2 新規) |
| `InstrumentBackend` Protocol | **experimental spike** (v1.1〜) |
| Backend capability model / Error mapping | **design memo** (実装なし) |
| Replay backend | **design memo** (実装なし) |
| Executable Python plugin | **未対応 (v1.x 内予定なし)** |
| Plugin entry_points discovery | **未対応 (v1.3+ 候補)** |
| Remote registry / pull CLI | **未対応 (v1.3+ 候補)** |

### テスト

`tests/test_v12_extension.py` 42 件:

- 4 件の v1.2 docs 存在 + 必須キーワード
- `v1_stability_policy.md` の definition packs 言及
- `backend_abstraction.md` の capability model + error mapping
- `ExtensionManifest` schema (minimal valid / `executable_code: true` 拒否 /
  非 definition_pack type 拒否 / 不正 extension_id 拒否 / 非 SemVer version
  拒否 / 不正 support_level 拒否)
- `validate_extension_file()` (example pack pass / not found / executable
  code reject / missing file / empty contents warning)
- `extension_manifest.schema.json` 生成 + experimental status
- CLI 統合: `visa-mcp validate extension` 成功 / 失敗
- **新規 Stable tools 無し** (Stable 43 不変)
- **Experimental tools 不変** (7 のまま、validate/inspect_experiment_bundle のみ)
- v1.2 ファイル 8 件の LF + multi-line 検証

旧 v1.1 / v1.1.1 version assertion を `1.` 全般許容に微調整 (互換維持)。

**合計 763 件 passing** (v1.1.1: 721 → v1.2.0: 763)

### 互換性

- **Stable API / Experimental MCP tools とも完全不変**
- 新規追加は **すべて CLI / docs / schemas** (MCP tool ゼロ)
- 既存 DB schema 不変
- definition pack manifest は experimental スコープ

### スコープ外 (v1.3+ 候補)

- Python plugin auto-loading / `entry_points` discovery
- Backend plugin の実行
- Remote registry / pull CLI
- Replay backend 本実装 + `bundle_version=1.1` 拡張
- `import_experiment_bundle` / replay as active job
- Human intent / approval
- Custom DSL step / evaluator function

---

## v1.1.1 — v1.1 レビュー対応 (P0/P1/P2)

v1.1.0 外部レビュー P0/P1/P2 対応。新規 MCP ツール無し、互換維持。

### P0 確認

- raw 改行: 該当 10 ファイル全て **LF only / CR=0 / 多行** で正常確認
  (`tests/test_v111_review.py` の parametrized テストで CI 回帰防止)

### P1 改修

- **P1-2: `docs/bundle_export.md` に `plan.json` optional の説明追加**
  - DSL Job 由来でのみ含まれる旨、required/optional ファイル一覧を表で明示
- **P1-3: `inspect_experiment_bundle` レスポンスに `compatibility` 追加**
  ```json
  {
    "compatibility": {
      "bundle_version_supported": true,
      "created_by_current_major_version": true,
      "can_be_validated": true,
      "can_be_replayed": false,
      "reason": "Replay / import is not implemented in v1.1. ..."
    }
  }
  ```
  AI エージェントが誤って "replay できる" と解釈しないよう
  `can_be_replayed: false` を明示。
- **P1-4: bundle inspection の zip 安全性を docs 化**
  - ファイルシステムへの展開は行わない (`ZipFile.read()` のみ)
  - zip slip 不可 (展開しないため)
  - zip bomb 上限は v1.2+ 候補として記録
- **P1-5: `docs/backend_abstraction.md` に Open questions セクション追加**
  - stateful session / timeout / binary transfer / encoding /
    backend capability / mock-replay-simulator の収まり / error mapping
    の 7 論点を v1.2+ 検討候補として記録
- **P1-6: naming strategy の表現を緩和**
  - 「v1.x 全期間で唯一」→ **「Default decision + Exception」** 構造に
  - 再評価の余地を残しつつ default を明示
- **P2-7: `docs/v1_stability_policy.md` に `InstrumentBackend` 注記**
  - public import 可能だが **stable plugin API ではない** ことを明示
  - 外部 plugin 利用者は v1.2+ 以降の正式化を待つよう案内

### テスト

`tests/test_v111_review.py` 27 件:

- repo 10 ファイル × LF + multi-line (20 件)
- bundle docs に plan.json optional / zip 安全性
- backend docs に Open questions
- naming strategy の Default decision / Exception
- v1_stability_policy の InstrumentBackend 注記
- `inspect_experiment_bundle` の compatibility field 存在 + 値

`tests/test_v11.py::test_version_is_v1_1_0` を v1.1.x 系列許容に微調整。

**合計 721 件 passing** (v1.1.0: 694 → v1.1.1: 721)

### 互換性

- 動作変更は `inspect_experiment_bundle` レスポンスに **新フィールド追加**
  のみ (純粋追加、experimental スコープ)
- Stable API 不変

---

## v1.1.0 — Direction-setting release (naming / backend spike + bundle inspection)

合言葉: **「分離するのではなく、分離できるかを判断できる状態にする」**

v1.1 はリポジトリ分割や backend abstraction を **実装しない**。代わりに、
方向性を文書で固め、bundle 検証 read-only ツールを 2 つ追加する小規模リリース。

### 新規 MCP ツール (2 個、合計 48 → 50, ともに experimental)

| ツール | 役割 |
|--------|------|
| `validate_experiment_bundle` (experimental) | bundle zip の整合性 (checksum / required files / version) を実行なしに検証 |
| `inspect_experiment_bundle` (experimental) | bundle 中身要約 (manifest / plan / job_summary / result rows) — **analysis-only、import / replay は行わない** |

### Direction docs (2 件新規)

- **`docs/naming_and_repository_strategy.md`**: visa-mcp を v1.x 全期間で
  唯一のリポジトリとして継続。分割しない理由 5 つ、再評価条件 5 つ、v1.1
  決定一覧 (リポジトリ / package / 名称 / backend / 仮称予約 / 全て NO)
- **`docs/backend_abstraction.md`**: `InstrumentBackend` Protocol の責務
  境界、5 backend 候補 (pyvisa / mock / replay / rest / simulator)、
  **LabVIEW は候補外**、v1.1 spike のスコープ (Protocol 公開のみ、既存経路
  不変)、判断基準

### Backend spike (実装はしない)

- `src/visa_mcp/backends/base.py` 新規: `InstrumentBackend` Protocol
  (`@runtime_checkable`)、async `list_resources` / `query` / `write`
- `src/visa_mcp/backends/__init__.py` 新規
- **既存 `VisaManager` / `MockVisaManager` は Protocol を明示継承しない**
  (duck-typed 互換のみ証明、動作変更なし)

### Stability 数の更新

- Stable: **43** (v1.1 で増減なし、v1.x 互換保証維持)
- Experimental: **5 → 7** (`validate_experiment_bundle` / `inspect_experiment_bundle` 追加)
- 総数 (raw 除く): 48 → 50

`src/visa_mcp/stability.py` の `EXPERIMENTAL_TOOLS` に
`"Bundle inspection (v1.1)"` カテゴリを追加。`v1_stability_policy.md` 同期。
README の数 / バナーも更新。

### `validate_experiment_bundle` 詳細

検査:

- bundle が読める zip である
- `manifest.json` が存在し JSON として読める (なければ `validation` +
  `details.sub_class=missing_manifest`)
- `bundle_version` が `SUPPORTED_BUNDLE_VERSIONS = ("1.0",)` に含まれる
  (それ以外は `warning_class=version_mismatch`)
- 必須 files (`manifest.json` / `job_record.json` / `timeline.jsonl` /
  `results.jsonl` / `results.csv`) が揃う
  (`plan.json` は DSL Job 由来でないと存在しないため必須から除外)
- `manifest.checksums` 各 sha256 が zip 内 file の実 sha256 と一致
  (`validation` + `details.sub_class=checksum_mismatch`)
- `visa_mcp_version` が記録されている

### `inspect_experiment_bundle` 詳細

返却:

- `manifest` (bundle_version / visa_mcp_version / job_id / created_at /
  contents / include_monitor_data / include_audit)
- `plan` (任意、`include_plan=true` 既定) - dsl_version / name / unit /
  step_count
- `job_summary` (任意、`include_summary=true` 既定)
- `result_row_count` (`results.jsonl` 行数)
- `has_audit` / `has_monitor_data`
- `warnings` (version_mismatch 等)

**import / replay は行わない** (`docs/bundle_export.md` 参照)。

### テスト

- `tests/test_v11.py` 24 件:
  - version v1.1.0 確認
  - direction docs 2 件存在 + 必須キーワード
  - `InstrumentBackend` Protocol import + duck-typed 互換証明
  - stability 数 43 / 7 / 50 整合、bundle tools が experimental に登録
  - 新規 stable tools 無し
  - `validate_experiment_bundle`: success / missing_manifest /
    checksum_mismatch / not_found / version_mismatch warning (5 件)
  - `inspect_experiment_bundle`: summary 取得
  - v1.1 docs / source files の LF + multi-line (10 件)
- 既存 `test_v1_stability.py` / `test_v101_review.py` を v1.1 互換に微調整
- **合計 694 件 passing** (v1.0.1: 669 → v1.1.0: 694)

### 互換性

- **Stable API 不変** (新規追加は全 experimental)
- 既存 `VisaManager` / `MockVisaManager` の動作変更なし
- `InstrumentBackend` Protocol は public import 可能だが **既存経路から
  参照されない** (spike のみ)
- `lab-executor-mcp` 仮称は **正式予約しない** (`docs/naming_and_repository_strategy.md`)

### スコープ外 (v1.2+)

- リポジトリ / package 分割
- backend abstraction の本格導入 / adapter 化
- plugin / extension mechanism (v1.2 候補)
- remote registry / registry pull CLI
- bundle replay / import as active job / `import_experiment_bundle`
- human intent / approval (v1.3+ 候補)
- 本物の LLM API CI

---

## v1.0.1 — v1.0 レビュー対応 (P0/P1) + 整合性 single source

v1.0.0 外部レビュー P0/P1 対応。新規 MCP ツール無し、互換維持。

### P0 確認

- raw 改行: 12 ファイル全て **LF only / CR=0 / 多行** で正常確認
  (`tests/test_v101_review.py` の parametrized テストで CI 回帰防止)。

### P1 改修

#### P1-2: Stable / Experimental 単一 source of truth

- **`src/visa_mcp/stability.py` 新規**: Stable / Experimental / Raw を
  カテゴリ別に列挙する **唯一の source**。docs / README / tests がここから参照
- v1.0.0 release note の **「Stable 35」は誤記**で、実際の Stable 数は **43**
  (Core 14 + Recipe/Job 9 + Group/Map 4 + DSL 6 + Observation 3 + Monitor 4 +
  Results 2 + Ingest 1)。`docs/v1_stability_policy.md` を **43** に訂正
- `tests/test_v101_review.py` で stable_count == 43 / experimental_count == 5
  / total == 48 を CI 検証。stable と experimental の重複も検出
- v1_stability_policy.md に列挙された **全 stable / experimental tool 名**が
  実際に列挙されているか自動 cross-check

#### P1-3: README の results tools 表記修正

- `get_experiment_results` / `export_experiment_results` の README 行から
  **`(experimental)` を削除** し `(stable v1.x)` に変更
  (release note 上は Stable 分類だったため整合)
- `tests/test_v101_review.py::test_readme_results_tools_not_marked_experimental`
  で回帰防止

#### P1-4: docs/bundle_export.md 新規

`export_experiment_bundle` 専用 docs:

- bundle 目的 (再検証 / 共有 / 監査 / 記事化、**完全再現実行は v1.x 非対応**)
- bundle layout (manifest / plan / compiled_summary / job_record /
  job_summary / timeline / results.{jsonl,csv} / monitor_data / audit)
- manifest.json 例 + checksums の意味
- path 安全策 (default dir / traversal 拒否 / overwrite 既定)
- SHA-256 の二段検証 (zip 全体 + 中身各 file)
- experimental スコープと **`bundle_version="1.0"` の存在保証** (informal)
- v1.1+ ロードマップ (validate_bundle / import_bundle_for_analysis /
  replay_bundle_with_mock)

#### P1-6: `extract_pdf_commands` の保証範囲を明記

`docs/v1_stability_policy.md` に注記:

- v1.x で **tool 名・引数・response 構造**は固定
- **PDF 抽出精度 / メーカー資料ごとの成功率は保証対象外**
- 抽出結果は YAML 草案として人間レビューを前提

### README 整合修正

- 「Core 35 tools」→「**Stable 43 tools + Experimental 5 tools**」へ
- raw 2 tools は env-gated を明示
- 「48 個 / raw 系は別途」→「48 個 / raw 系 **2 個** は別途」と数を明示

### 新規ファイル

| ファイル | 役割 |
|---------|------|
| `src/visa_mcp/stability.py` | Stable / Experimental tool 分類の唯一 source |
| `docs/bundle_export.md` | bundle export 専用 docs |
| `tests/test_v101_review.py` | review response テスト (37 件) |

### テスト

- `tests/test_v101_review.py` 37 件:
  - repo 12 ファイル × LF + multi-line (24 件)
  - version v1.0.1 確認
  - **stability module 数の整合** (43 / 5 / 48) + 重複検出 + 全 tool が
    v1_stability_policy.md に存在
  - README の results tools が experimental 表記でない
  - docs/bundle_export.md の必須キーワード存在
  - extract_pdf_commands の保証範囲注記
  - schema files の stable status 維持
- **合計 669 件 passing** (v1.0.0: 632 → v1.0.1: 669)

### 互換性

- 純粋な docs / README / test の整合性修正
- Stable API 不変。動作変更なし。

---

## v1.0.0 — AI エージェント実験自動化評価基盤の安定化

合言葉: **「新機能追加ではなく、安定化・互換保証・再現性・公開準備」**

v0.8.x 〜 v0.9.3.1 で積み上げた DSL / Job / Observation / Benchmark /
Repair / Export / Registry / Audit を **stable / experimental の 2 段階**で
正式に分類し、v1.x 期間中の互換保証を宣言する。新規 MCP ツールは 1 個
(`export_experiment_bundle`, experimental) のみ。

> **v1.0 ≠ 実用全機能完成**。AI エージェント実験自動化を **評価するための
> 安定版**として位置づける。runtime 分離・backend abstraction・plugin・
> human-in-the-loop は v1.1 以降。

### 新規 MCP ツール (1 個、合計 47 → 48)

| ツール | 役割 |
|--------|------|
| `export_experiment_bundle` (experimental) | Job 実験記録を再現性 bundle (zip) として出力 |

bundle 内容: `manifest.json` (bundle_version=1.0 + sha256 checksums) +
`plan.json` / `compiled_summary.json` / `job_record.json` / `job_summary.json`
/ `timeline.jsonl` / `results.jsonl` / `results.csv` (+ `monitor_data.jsonl` /
`audit.jsonl` をオプション)。

⚠ v1.x では **bundle import / replay は提供しない** (`import_experiment_bundle`
は v1.1+ 候補)。bundle は **再検証・共有・監査・記事化** のためのパッケージ
として位置づける。

### Stable / Experimental 分類 (正式)

**Stable** (v1.x 互換保証、35 ツール):

- Core: `list_resources` / `identify_*` / `bind_definition` /
  `list_available_definitions` / `list_commands` / `get_instrument_info` /
  `list_safety_constraints` / `validate_operation` / `reload_definitions` /
  `describe_instrument` / `get_state` / `get_last_measurement`
- Recipe/Job: `execute_named_command` / `list_recipes` / `execute_recipe` /
  `start_recipe_job` / `start_wait_job` / `get_job_status` / `get_job_result` /
  `list_jobs` / `cancel_job`
- Group/Map: `list_groups` / `list_experiment_units` /
  `start_group_query_job` / `start_map_recipe_job`
- DSL: `validate_experiment_plan` / `dry_run_plan` / `start_experiment_job` /
  `save_experiment_template` / `list_experiment_templates` /
  `get_experiment_template`
- Observation: `get_experiment_timeline` / `get_job_live_view` / `get_job_summary`
- Monitor: `start_monitor` / `stop_monitor` / `get_monitor_data` /
  `prune_monitor_data`
- Results: `get_experiment_results` / `export_experiment_results`
- Ingest: `extract_pdf_commands`

**Experimental** (v1.x 内で変更可能、5 ツール):

- `start_experiment_job_from_template`
- `resume_job`
- `query_audit`
- `list_locks`
- `export_experiment_bundle` (v1.0 新規)

### Schema status: preview → **stable**

- `schemas/{instrument,system_config,dsl,benchmark_task}.schema.json` の
  `x-visa-mcp-status` を **`"stable"`**、`x-compatibility` を
  **`"v1.x-compatible"`**、`$id` を `*.schema.v1.json` URL に更新
- ただし schema 内の experimental fields (`template_source` / `resume metadata`
  / `audit/lock 関連`) は `docs/v1_stability_policy.md` で別途明示

### error_taxonomy v1.0 整理

- **`lock_conflict` / `lock_stale` を deprecated** (独立 error_class としての
  使用を廃止)
- v1.x 公開 API では `error_class="blocked"` + `details.reason="lock_conflict"
  | "lock_stale"` + `blocked_by` 詳細に統一
  - audit log の内部 marker としては `lock_conflict` 文字列を引き続き使用可

### 新規 / 更新 docs

- **`docs/v1_stability_policy.md`** (新規): Versioning policy / Stable
  tools 一覧 / Experimental tools 一覧 / Stable schemas / Response envelope
  guarantee / Error taxonomy guarantee / Deprecation policy / What is NOT
  guaranteed / v1.x → v2.0 展望
- `docs/compatibility.md`: v0.8.2 草案表記から **v1.0 正式版** へ更新、
  `v1_stability_policy.md` への参照を追記
- `docs/error_taxonomy.md`: `lock_conflict` / `lock_stale` の deprecation
  方針を追記
- `README.md`: 入口に `v1.0 stability` バナー + `v1_stability_policy.md`
  への link + `export_experiment_bundle` 追記

### `__version__` 追加

`visa_mcp.__version__ = "1.0.0"` を `src/visa_mcp/__init__.py` に追加。
`pyproject.toml` の `Development Status` を `4 - Beta` → `5 - Production/Stable`
に昇格。

### テスト

- `tests/test_v1_stability.py` 15 件:
  - `__version__` / `pyproject` の v1 確認
  - `docs/v1_stability_policy.md` の必須キーワード存在
  - `compatibility.md` が新ポリシーを参照
  - 全 schema が `x-visa-mcp-status: stable` + `v1.x-compatible`
  - `lock_conflict` の deprecation 文言が `error_taxonomy.md` に存在
  - `export_experiment_bundle`: zip 中身に `manifest.json` / `plan.json` /
    `job_record.json` / `timeline.jsonl` / `results.jsonl` / `results.csv`
    が含まれる
  - bundle 内 manifest の sha256 が zip 中身と一致
  - 外側 zip 全体の sha256 が response の `sha256` と一致
  - path traversal 拒否
  - `overwrite=False` 既定で既存ファイル拒否
  - README が v1_stability_policy + export_experiment_bundle にリンク
- 旧 schema preview テストを `preview / stable` どちらも許容に更新
  (互換維持)
- **合計 632 件 passing** (v0.9.3.1: 617 → v1.0.0: 632)

### 互換性

- **新規追加** (`export_experiment_bundle` + Schema status 昇格) のみ
- 動作変更は無し (lock_conflict は v0.9.x で error_class として返した
  経路を持たないため、deprecation の実害なし)
- 既存 Stable API は v1.x 内で破壊的変更を行わないことを宣言

### スコープ外 (v1.1 以降)

- `import_experiment_bundle` / replay
- runtime 分離 (`lab-executor-mcp` 仮称) / backend abstraction
- plugin / extension mechanism
- human intent / approval 層
- remote registry / registry pull CLI 本格化
- 本物の LLM API CI
- ResourceScheduler と SQLite `locks` の source-of-truth 統合
- audit retention 自動削除
- 完全な分散 lock

---

## v0.9.3.1 — Operational integrity レビュー対応 (P0/P1)

v0.9.3 外部レビュー P0/P1 対応。新規 MCP ツール無し、互換維持。

### P0 確認

- raw 改行: 該当 8 ファイル全て **LF only / CR=0 / 多行** で正常確認
  (`tests/test_v0931_review.py` の parametrized テストでリグレッション防止)。

### P1 改修

- **AuditStore init 失敗時の visibility (P1-6)**:
  - `JobManager._audit_init_error` フラグを追加 (init 失敗時 True)
  - `logger.warning` で stderr に明示警告
  - `query_audit` / `list_locks` が `error_class=internal` +
    `details.sub_class=audit_unavailable` を返す (no-op を隠さない)
- **`docs/operational_integrity.md` に以下を追記** (P1-2/3/4/5/7/8/9):
  - **Lock source of truth**: ResourceScheduler (in-memory) と SQLite
    `locks` テーブルの並行存在を明示、v1.0 までの統合 open question
  - **Stale lock の定義と解除条件**: `lease_until < now()` 判定、起動時
    `release_stale_locks()`、Job status 連動は v1.0 候補
  - **監査対象 tool の範囲**: v0.9.3 で記録される 4 種類 (server_started /
    job_started / cancelled / resume_started) + v1.0 候補一覧
    (export / safety_blocked / lock_blocked / unsafe_* 等)
  - **AuditStore unavailable response 構造**: `audit_unavailable` sub_class
  - **`include_details=true` の payload schema 例示**: request_summary /
    response_summary / metadata + redaction marker (`_truncated` /
    `_truncated_list` / `[REDACTED]` / `<deep>`)
  - **`blocked` vs `lock_conflict` v1.0 方針**: `error_class=blocked` +
    `details.reason=lock_conflict` に統一予定
  - **Audit retention 方針**: v1.x まで自動削除なし、手動 DELETE + VACUUM
    手順、v1.x で `retention_days` / `max_rows` を検討

### テスト

`tests/test_v0931_review.py` 22 件:

- repo 8 ファイル × LF + multi-line (16 件)
- AuditStore init_error flag default False
- `query_audit` / `list_locks` が audit_unavailable を返す (2 件)
- docs に必須キーワード (source of truth / ResourceScheduler / Stale lock /
  audit_unavailable / Audit retention / blocked_by / lock_conflict /
  v1.0 / 監査対象 tool / start_experiment_job 等) が含まれる (4 件)

**合計 617 件 passing** (v0.9.3: 595 → v0.9.3.1: 617)

### 互換性

- 動作変更は `query_audit` / `list_locks` の **AuditStore 失敗時のみ**
  (これまで実装上 init は失敗しないため、実質的には新規 visibility 追加)
- 互換維持。experimental スコープ。
- Stable API 不変。

---

## v0.9.3 — Operational integrity (audit + locks)

合言葉:「**実験を実行できるだけでなく、誰が・いつ・何を・どの resource に
対して行い、なぜ失敗 / 拒否されたかを後から追えるようにする**」。SQLite
`audit` / `locks` テーブルを `user_version=3` で追加し、AuditStore + 2 つの
MCP ツールで監査・lock 競合追跡・stale lock 検出を実現。

### 新規 MCP ツール (2 個、合計 45 → 47)

| ツール | 役割 |
|--------|------|
| `query_audit` | 監査ログを filter + cursor pagination で取得 (experimental) |
| `list_locks` | 現在の resource lock 一覧 / stale 判定 (experimental) |

### SQLite migration (user_version 2 → 3)

- **`audit`** テーブル (audit_id / timestamp / event_type / severity / owner /
  client_id / tool_name / job_id / resource / target_id / status / error_class
  / message / request_summary_json / response_summary_json / metadata_json)
  + 5 indexes (timestamp / job_id / resource / owner / event_type)
- **`locks`** テーブル (resource PRIMARY KEY / owner / job_id / client_id /
  acquired_at / lease_until / lock_reason / metadata_json) + 2 indexes
- 既存 DB は非破壊的に migrate (空テーブル追加のみ)

### AuditStore (`src/visa_mcp/audit.py`)

- `record_event(event_type, *, severity, owner, tool_name, job_id, resource,
  status, error_class, message, request, response, metadata, ...)` で 1 行 INSERT
- `query(*, job_id, resource, owner, event_type, severity, since, until,
  limit, cursor, include_details)` で複合 cursor `{timestamp, audit_id}`
  pagination
- `acquire_lock` / `release_lock` / `list_locks` / `release_stale_locks`
  で lock 操作

### Redaction (`summarize_for_audit`)

`request` / `response` payload を audit に保存する際の安全策:

| 入力 | 変換 |
|------|------|
| `len > 200` の文字列 | `{"_truncated": true, "len": N, "head": "..."}` |
| `len > 5` の list | `{"_truncated_list": true, "len": N, "head": [...]}` |
| key に `token` / `api_key` / `password` / `secret` / `authorization` / `credentials` | `[REDACTED]` |
| 深さ 6 超 | `"<deep>"` |

raw SCPI 応答 / 大量測定値 / credentials は保存されない。

### JobManager 統合

- 起動時に `AuditStore` 初期化 + `release_stale_locks()` 実行
  → `server_started` event を記録 (metadata に `stale_locks_released` 件数)
- `start_experiment_job` 完了時に `job_started` / `job_failed` を記録
- `cancel_job` (cancel 経路) で `job_cancelled` 記録
- `resume_job` 成功時に `resume_started` 記録 (original_job_id /
  from_step / safe_shutdown_before_resume を metadata に)

### 新規 error_class

| クラス | 意味 | recoverable |
|--------|------|------|
| `lock_conflict` | resource lock が他 owner に保持 (`blocked` の詳細種別) | True |
| `lock_stale` | 自 lock の lease 切れ | True |
| `audit_query_failed` | query_audit 内部 error (通常は `internal`) | False |

v1.0 で `error_class=blocked` + `details.reason=lock_conflict` に統一するか
独立 class とするか決定する。

### docs

- `docs/operational_integrity.md` 新規: audit / locks の設計 / 記録対象 /
  redaction / `query_audit` / `list_locks` / retention 方針
- `docs/error_taxonomy.md`: `lock_conflict` / `lock_stale` / `audit_query_failed`
  追記

### テスト

`tests/test_v093_audit_locks.py` 18 件:

- migration (user_version >= 3 / audit + locks テーブル存在 / 列)
- record_event / query (filter / cursor pagination / include_details default)
- redaction (sensitive keys / 長文 / 長 list / DB 内も redact 済)
- locks (acquire / release / owner-only release / stale 検出 / stale 上書き)
- list_locks (filter / include_stale)
- JobManager 統合 (server_started 自動記録 / job_started on DSL job)
- MCP tools (query_audit / list_locks 経由)

**合計 595 件 passing** (v0.9.2.1: 577 → v0.9.3: 595)

### 互換性

- 新フィールド / 新テーブルのみ追加 (既存 DB は migration で空テーブルを追加)
- `audit` / `locks` 系 API はすべて **experimental** スコープ
- AuditStore 初期化失敗時は機能を継続 (audit 記録は no-op になる)
- Stable API 不変

### スコープ外 (v1.0 以降)

- 完全な分散 lock / remote audit backend / WebSocket push
- audit retention purge (delete API)
- role-based access control / user authentication
- ResourceScheduler との完全統合 (v0.9.3 では並行存在、v1.0 で検討)

---

## v0.9.2.1 — Ecosystem 準備レビュー対応 (P0/P1)

v0.9.2 外部レビュー P0/P1 対応。新規 MCP ツール / CLI 無し、互換維持。

### P0 確認

- raw 改行: 該当 12 ファイル全て **LF only / CR=0 / 多行** で正常確認
  (`tests/test_v0921_review.py` の parametrized テストでリグレッション防止)。
- 各ファイルが 5 行以上で潰れていないことを CI で検証。

### P1 改修

- **registry INDEX validation 強化**: `validate_registry` の最初の report
  として INDEX 自体を lint する `_validate_index_entries` を追加。
  - `registry_entry_missing_field` (id / vendor / model / category / path)
    → **error**
  - `registry_duplicate_id` → **error**
  - `registry_entry_path_not_found` → **error**
  - `registry_path_outside_registry` → warning
  - `invalid_support_level` (INDEX 側、registry 掲載時) → **error**
    (機器定義単体 lint では引き続き warning)
- **`docs/registry.md` 新規**: 用語揺れ (`vendor` vs `manufacturer`) /
  `support_level` 各段階の意味と v1.0 までの強化予定 /
  `visa-mcp validate plan` が Pydantic schema-only である旨 /
  `visa-mcp validate registry` の新規 lint 項目を明文化。
- **`validate_plan_file` docstring に schema-only 注記**: CLI で「Plan
  として実行可能か」を完全に確認するには MCP tool
  `validate_experiment_plan` を使うこと、と明記。
- **`support_level=verified` の自己申告状態を docs 化**: v0.9.2 時点では
  自己申告、v1.0 で `tested_interfaces` 非空 / 主要 command 網羅 /
  safe_shutdown 存在を必須条件として強制する予定であることを
  `docs/registry.md` に記録。

### テスト

- `tests/test_v0921_review.py` 31 件:
  - repo text files LF only + multi-line (12 ファイル × 2 = 24 件)
  - registry INDEX validation: missing field / duplicate id / missing path /
    invalid support_level → 各 error 検出
  - 現行 `registry/INDEX.yaml` が強化後 lint を通る
  - `validate_plan_file` docstring に `validate_experiment_plan` 言及
  - `docs/registry.md` の必須キーワード存在
- **合計 577 件 passing** (v0.9.2: 546 → v0.9.2.1: 577)

### 互換性

- registry INDEX validation の強化は **追加のみ**。現行 `registry/INDEX.yaml`
  は無変更で通る。
- 機器定義単体の `invalid_support_level` は引き続き warning (互換維持)。
  registry validation 経路でのみ error 昇格。
- Stable API 不変。

---

## v0.9.2 — Ecosystem 準備 (Registry / Schema / CLI / 英語 docs)

合言葉:「**実験実行能力を増やすのではなく、外部ユーザーや将来の AI エージェント
が安全に使える定義・Schema・検証基盤を整える**」。新規 MCP ツールなし。
新規 CLI subcommand 1 種 (`visa-mcp validate`)、機器定義 registry skeleton、
`support_level` 導入、英語 docs 草案を追加。

### 新規 CLI

| コマンド | 用途 |
|---------|------|
| `visa-mcp validate instrument <path>` | 機器定義 YAML を schema + lint で検証 |
| `visa-mcp validate system <path>` | `_system.yaml` を検証 |
| `visa-mcp validate plan <path>` | DSL plan を ExperimentPlan として検証 |
| `visa-mcp validate benchmark <path>` | benchmark task YAML を検証 |
| `visa-mcp validate registry <path>` | `registry/INDEX.yaml` に列挙された全機器を一括検証 |
| `visa-mcp validate schemas [<dir>]` | `schemas/*.schema.json` の pretty-print / LF only / preview metadata を確認 |

各コマンドは `--json` で CI 向け machine-readable 出力。引数なしの
`visa-mcp` 単体は従来通り MCP server 起動 (後方互換)。

### 機器定義 Registry skeleton

```
registry/
├── README.md
├── INDEX.yaml          # 機器定義の一覧
└── instruments/
    └── mock/
        ├── mock_psu.yaml   (support_level: tested)
        ├── mock_dmm.yaml   (support_level: tested)
        └── mock_temp.yaml  (support_level: tested)
```

`INDEX.yaml` の各 entry は `id` / `vendor` / `model` / `category` /
`support_level` / `path` を持つ。

### `support_level` 導入

機器定義 `metadata.support_level` で品質を 4 段階に分類:

| level | 条件 |
|-------|------|
| `verified` | 実機で identify / 主要 command / state_query / verify / safe_shutdown 確認済み |
| `tested` | mock または実機で基本 command 確認済み |
| `experimental` | マニュアル等から作成、限定的動作確認 |
| `draft` | 未検証 (Plan 生成時に注意推奨) |

既定値は `draft` (互換維持)。新規フィールド: `tested_interfaces` /
`tested_firmware` / `definition_version`。

### Instrument lint (warning level)

`visa-mcp validate instrument <yaml>` で以下を warning として検出:

- `missing_safe_shutdown` (write command を持つが safe_shutdown 未定義)
- `missing_state_query` (state_query 未定義)
- `missing_verify` (set 系 write command に verify 未定義)
- `support_level_draft` (draft レベルの注意喚起)
- `invalid_support_level` (4 段階以外の値)
- `missing_metadata` (manufacturer/model 不足)
- `registry_support_level_mismatch` (INDEX vs YAML の不一致)

### JSON Schema 整理

- **`schemas/benchmark_task.schema.json` 新規** 追加 (BenchmarkTask root + repair section)
- 全 schema は `indent=2` pretty-print + LF only + `x-visa-mcp-status: preview` 必須
- `visa-mcp validate schemas` で CI 向け自動チェック

### 英語 docs 草案

- **`docs/en/quickstart.md`** (60 秒インストール / Claude Desktop 登録 /
  3 layer workflow / safety notes)
- **`docs/en/concepts.md`** (Instrument definition / system_config /
  ExperimentPlan / Job model / Benchmark task / Observation API /
  error_class)

### 新規ファイル

| ファイル | 役割 |
|---------|------|
| `src/visa_mcp/registry.py` | registry + lint + validation helpers |
| `src/visa_mcp/cli.py` | `visa-mcp validate` subcommand |
| `registry/INDEX.yaml` + `registry/instruments/mock/*.yaml` | registry skeleton |
| `docs/en/{quickstart,concepts}.md` | 英語 docs 草案 |

### テスト

- `tests/test_v092_ecosystem.py` 30 件 (schema files pretty-print / preview
  metadata / benchmark_task schema generated / registry index loads /
  entries point to existing files / registry definitions validate /
  support_level required / default support_level=draft / accepts 4 levels /
  lint missing safe_shutdown / lint missing verify / lint missing
  state_query / lint draft warning / lint verified clean / CLI instrument
  success / CLI failure exit=1 / CLI registry / CLI benchmark / CLI schemas /
  english docs exist)
- **合計 546 件 passing** (v0.9.1.1: 516 → v0.9.2: 546)

### 互換性

- `MetadataConfig` への新フィールドは optional / 既定値あり (既存 YAML 無変更で動作)
- `pyproject.toml` entry point: `visa-mcp = "visa_mcp.cli:main"`。引数なし
  実行は従来通り server 起動なので Claude Desktop 設定変更不要
- Stable API 不変

### スコープ外 (v0.9.3 以降)

- audit SQLite 統合 + `query_audit` (v0.9.3)
- multi-agent lock 完成 (v0.9.3)
- LLM ベンチ CI / API 凍結 / 再現性 bundle (v1.0)
- registry pull CLI / remote registry (v1.0+)

---

## v0.9.1.1 — Self-repair / Export レビュー対応 (P0/P1)

v0.9.1 外部レビュー P0/P1 対応。新規 MCP ツール無し、互換維持 (experimental
スコープ内の整理)。

### P0 確認

- raw 改行: 該当ファイル (`benchmark_task.py` / `benchmark_runner.py` /
  `export.py` / `tests/test_v091_repair_export.py` / repair task YAML 各種 /
  `error_taxonomy.md`) すべて **LF only / CR=0 / 多行** で正常確認。

### P1 改修

- **`docs/benchmark_repair.md`** 新規追加: repair task の目的、broken_plan /
  expected_failure / repaired_plan / expected_repair セクションの意味、
  must_not の使い方、新規 task の追加手順、v1.0 までのスコープ外を整理。
- **`docs/result_export.md`** 新規追加: `get_experiment_results` ↔
  `export_experiment_results` の使い分け、`get_job_summary` /
  `get_job_result` / `get_monitor_data` との関係、抽出元 (job_steps /
  target_runs / monitor_data) を明示、安全策 (default dir / traversal 拒否 /
  overwrite)、sha256、error_class 一覧を整理。
- **`repair_006_partial_failure_retry`** 新規 fixture: 2 branch parallel の
  片方 timeout シナリオに対し「失敗 target だけを除外した repaired_plan」を
  正解とする。must_not で `rerun_all_targets_unnecessarily` /
  `ignore_failed_targets` / `mark_partial_failure_as_total_success` を禁止。
  benchmark_runner は expected_failure.error_class=None のとき
  「broken_plan は validate を通り runtime で失敗する」シナリオを許容。
- **`unsupported_export_format` を独立 error_class に昇格** (sub_class 廃止):
  `response_envelope.ErrorClass` Literal にも追加。AI エージェントが直接
  分岐しやすくする。同時に `invalid_export_path` / `export_failed` /
  `resume_not_allowed` も Literal に正式追加。
- **`invalid_export_path` に `recommended_next_actions`**:
  `set_overwrite_true` / `choose_different_output_path` を返却。既存ファイル
  拒否時の AI 修正経路を明示。
- **`unsupported_export_format` に `recommended_next_actions`**:
  `use_csv_format` / `use_jsonl_format` を返却。

### テスト

- `tests/test_v0911_review.py` 6 件 (docs 存在 / repair_006 pass /
  unsupported_export_format independent class / invalid_export_path
  recommended actions / traversal recommended actions)
- 既存 `test_export_unsupported_format` を独立 error_class 期待に更新
- **合計 516 件 passing** (v0.9.1: 510 → v0.9.1.1: 516)

### 互換性

- `response_envelope.ErrorClass` Literal への追加は **純粋追加** (既存値は
  そのまま)。
- `unsupported_export_format` を sub_class から独立 error_class へ変更したが、
  experimental スコープなので即時反映。
- Stable API 不変。

---

## v0.9.1 — Agent self-repair 評価 + 測定結果 export API

合言葉:「**AI に修正させる前に、修正すべき失敗を定義する**」。v0.9.0 で作った
Benchmark 基盤に **self-repair 評価層** を追加し、`broken_plan が期待通り失敗
する` ↔ `repaired_plan が通る` を再現可能に確認できる。あわせて測定結果の
**JSON 確認 API + ファイル出力 API** を 2 ツール構成で追加。

### 新規 MCP ツール (2 個、合計 43 → 45)

| ツール | 役割 |
|--------|------|
| `get_experiment_results` | Job 測定結果を **少量確認用** JSON で返却 (experimental) |
| `export_experiment_results` | Job 測定結果を **CSV / JSONL ファイル**へ出力 (experimental) |

### Self-repair 評価層

BenchmarkTask schema を拡張し `layer: "repair"` を追加:

- **`broken_plan`** + **`expected_failure`** (phase / error_class / field_path /
  required_recommended_actions) で Stage A: 失敗再現
- **`repaired_plan`** + **`expected_repair`** (repair_actions / must_not / layer)
  で Stage B: 修正後の plan が通る
- benchmark_runner に `_run_repair` を追加 (既存 3 layer と共存)

repair task 5 件 (`benchmarks/repair/`):

| task | 評価対象 |
|------|---------|
| `repair_001_unknown_command` | `unknown_command` → 正しい command 名へ修正 |
| `repair_002_invalid_parameter_range` | `parameter_invalid` → 許容範囲内へ修正 |
| `repair_003_unit_role_missing` | `unit_role_missing` → bindings override (★重要) |
| `repair_004_raw_resource_with_unit` | `raw_resource_used_with_unit` warning → $role |
| `repair_005_safety_violation` | parameter 範囲超え → 適切値、override_safety を使わない (★最重要) |

特に `repair_005` の `must_not: [override_safety, unsafe_send_command,
retry_with_override]` で「AI が安易に override で逃げる修正」を失敗扱いにする。

### `get_experiment_results` (JSON 少量確認)

```json
{
  "data": {
    "columns": ["timestamp", "target_id", "instrument", "measurement",
                "value", "unit", "step_index", "step_path"],
    "rows": [...],
    "pagination": {"limit": 1000, "offset": 0, "returned": N,
                   "total": M, "has_more": false},
    "include_monitor_data": false
  }
}
```

- **monitor_data はデフォルト除外** (大量データを混ぜないため)
- limit 上限 10000 (`get_monitor_data` と同じクランプ)
- `include_monitor_data=true` で monitor_data を追記 (monitor_id == job_id 慣習)

### `export_experiment_results` (CSV / JSONL)

```json
{
  "data": {
    "path": "~/.visa-mcp/exports/<job_id>_results.csv",
    "rows": N, "size_bytes": K, "sha256": "...",
    "format": "csv", "include_monitor_data": false,
    "columns": [...]
  }
}
```

**安全策**:
- 既定 export dir は `~/.visa-mcp/exports/`
- output_path は **default dir 配下のみ許可**。絶対パス / `..` traversal は
  `error_class=invalid_export_path` で拒否
- 既存ファイルは `overwrite=False` 既定で拒否 (デフォルトパスでも同様)
- 出力後に `sha256` を返却 (v1.0 bundle export の事前準備)

### 新規 `error_class`

| クラス | 説明 |
|--------|------|
| `invalid_export_path` | output_path が範囲外 / 既存 / 不正 |
| `export_failed` | 書き込み I/O 失敗 |
| `unsupported_export_format` | csv/jsonl 以外指定 (sub_class) |

### テスト

- `tests/test_v091_repair_export.py` 16 件 (repair task schema / 5 fixture pass /
  unit_role_missing recommended_action / safety_violation must_not /
  get_experiment_results paginated / monitor_data 除外 / CSV+JSONL 出力 /
  path traversal 拒否 / overwrite 拒否 / sha256 一致 / unsupported_format /
  not_found)
- **合計 510 件 passing** (v0.9.0.1: 494 → v0.9.1: 510)

### 互換性

- `get_experiment_results` / `export_experiment_results` は **experimental**
- BenchmarkTask の repair セクションは optional (既存 task は無変更で動く)
- Stable API 不変

### スコープ外 (v0.9.x 以降)

- 本物の LLM self-repair loop (v1.0)
- `export_experiment_bundle` / `import_experiment_bundle` (v1.0)
- audit SQLite 統合 (v0.9.3)

---

## v0.9.0.1 — Benchmark / Resume レビュー対応 (P0/P1)

v0.9.0 外部レビュー指摘を P0/P1 で対応。新規 MCP ツールなし。互換維持
(experimental スコープ内の改善)。

### P0 改修

- **`.tmp/benchmark_db/*.sqlite` の untrack** + `.gitignore` に `.tmp/`
  `benchmarks/.tmp/` `*.sqlite` `*.sqlite3` を追記。Benchmark 実行時の
  生成物がリポジトリに混入していた問題を解消。
- raw 改行確認 (該当ファイル全て LF only / CR=0)。

### P1 改修

- **`task_005_partial_failure_group.yaml` → `task_005_partial_failure_parallel.yaml`
  にリネーム**: parallel branch 1 つが timeout する scenario であり、
  Group/Map の partial_failure とは別概念。命名を実装に合わせた。
  (Group/Map 用 partial_failure task は v0.9.1 で追加予定)
- **`Fixtures.random_seed` フィールド**: benchmark task YAML で
  `fixtures.random_seed: 12345` を指定すると runner が `random.seed()` を
  設定し、mock の `stable` / `stable_after` 等の noise を再現可能に。
- **`Fixtures.safety_mode` フィールド**: benchmark task ごとに safety mode
  (`strict` / `advisory` / `permissive`) を override 可能。strict が必要な
  verify mismatch シナリオで使う。
- **`task_004_verify_mismatch.yaml` を strict mode + seed で再構成**:
  permissive では verify mismatch が step success のまま記録のみだったため、
  strict mode を指定して job_status=failed まで到達するように。
- **`build_run_summary` が step error も verify 情報源に**: strict mode で
  step が failed になる場合 verify 情報は `error` 側に入るため、両方を
  走査するように修正。
- **`resume_job` の `safe_shutdown_before_resume=True` 失敗時は resume 中止**:
  v0.9.0 では warning 記録のみだったが、レビュー指摘 (実装方針 #12 推奨仕様)
  に合わせ、shutdown 失敗時は `error_class=safe_shutdown_failed` を返して
  Job 起動を阻止するよう変更。
- **`resume_job` の docstring に `from_step` の意味を明示**: DSL top-level
  step index (original_plan の `steps[]` 0-origin index) と明文化。
- **`resume_job(dry_run)` の `steps_to_execute` に `step_path` を追加**:
  LLM が original DSL の参照位置を特定しやすいよう `step_path: "steps[N]"`
  を併記。

### テスト

- `tests/test_v0901_review.py` 7 件 (Fixtures.random_seed / safety_mode /
  task_005 rename / resume step_path / safe_shutdown 失敗時の中止 / 成功時
  続行 / runner safety_mode override)
- task_005 名称変更に伴い `tests/test_benchmark_v090.py` 更新
- **合計 494 件 passing** (v0.9.0: 487 → v0.9.0.1: 494)

### 互換性

- benchmark task YAML への新フィールドは optional (既存 task は無変更で動作)
- `resume_job` の動作変更 (safe_shutdown 失敗時の中止) は experimental スコープ
- Stable API 不変

---

## v0.9.0 — Agent Benchmark 基盤 + Job resume MVP

合言葉:「**AI を呼ぶ前に、AI を評価できる実験場を作る**」。LLM を呼ばなくても
再現可能に評価できる benchmark 基盤と、interrupted/cancelled/failed Job を
**新規 Job として** 手動再開する resume MVP を導入。

### 新規 MCP ツール (1 個、合計 42 → 43)

| ツール | 役割 |
|--------|------|
| `resume_job` | interrupted/cancelled/failed/timeout Job を新規 Job として再開 (experimental) |

### Benchmark 基盤 (`src/visa_mcp/testing/`, `benchmarks/`)

LLM を呼ばずに validate / dry_run / mock execution の 3 layer で回帰評価する
パッケージ。`src/visa_mcp/testing/` を **experimental** として追加:

- **`benchmark_task.py`**: `BenchmarkTask` Pydantic schema (input / expected /
  fixtures / success_criteria)。`load_benchmark_task(path)` /
  `load_benchmark_tasks(dir)`。
- **`mock_instruments.py`**: `MockVisaManager` (VisaManager 互換 async API
  ですが VISA 依存ゼロ) + `InstrumentScenario` (9 mode: constant / echo /
  stable / stable_after / drifting / timeout / flaky / verify_mismatch /
  raise_protocol)。fixture YAML から制御可能。
- **`benchmark_runner.py`**: `BenchmarkRunner.run(task)` で 3 layer 実行 →
  `BenchmarkResult(status, scores, checks[], artifacts, tool_call_log)` を返す。
- **`run_task_file(...)`**: CLI / pytest から 1 タスクを起動する shortcut。

### Benchmark task fixtures (`benchmarks/`)

```
benchmarks/
├── README.md
├── tasks/
│   ├── task_001_basic_validate_dry_run.yaml   # validate / dry-run only
│   ├── task_002_unit_based_voltage_sweep.yaml # unit + sweep + execute
│   ├── task_003_template_override_run.yaml    # template + parameters override
│   ├── task_004_verify_mismatch.yaml          # verify mismatch シナリオ
│   └── task_005_partial_failure_group.yaml    # parallel 1 branch timeout
└── fixtures/
    ├── system_config_basic.yaml
    ├── system_config_partial_failure.yaml
    └── instruments/
        ├── mock_psu.yaml
        ├── mock_dmm.yaml
        └── mock_temp.yaml
```

5 件のうち 4 件は **passed**、1 件 (task_004) は permissive mode の挙動を
ドキュメント化する目的で `job_status: completed` を期待 (verify 失敗は
step.result.verify に記録)。

### `resume_job` MVP (experimental)

**設計 (実装方針 #10 案 B): 新規 Job 方式**

- 元 Job の status は変えず、`resumed_from_job_id` を持つ **新 Job** を作る
- 履歴は両 Job の `job_events` に残す:
  - 元 Job 側: `resume_started` (payload: resumed_job_id / from_step)
  - 新 Job 側: `job_resumed` (payload: original_job_id / from_step)
- 新 Job の `parameters.template_source.resume = {resumed_from_job_id,
  resumed_from_step, original_total_steps}`

**安全制約 (実装方針 #8 / #9):**

- `from_step=None` は **実行されない** (`suggested_from_step` を返すだけ)
- `dry_run=True` で `steps_to_execute` / `required_resources` / warnings を
  返す (Job 起動なし)
- **`resume_may_repeat_side_effects` warning が必ず付く**
- resume 可能 status: `interrupted` / `cancelled` / `failed` / `timeout`
  のみ
- resume 不可: `completed` / `running` / `waiting` / `safe_shutdown_failed`
  終端 / experiment_plan 未保存 / dsl_version 非互換
- `safe_shutdown_before_resume=True` で再開前に best_effort_safe_shutdown
  を試行 (結果は warnings に記録)

新 `error_class`: `resume_not_allowed` (error_taxonomy への追加候補)。

### docs / examples

- `benchmarks/README.md`: benchmark 全体の使い方
- `docs/error_taxonomy.md`: `resume_not_allowed` を追記

### テスト

- `tests/test_benchmark_v090.py`: 14 件 (schema loader / mock 各 mode /
  3 layer runner / 4 タスク実行 / result shape)
- `tests/test_resume_v090.py`: 8 件 (completed/running 拒否 / not_found /
  experiment_plan 必須 / from_step=None 拒否 / dry_run / invalid from_step /
  新 Job 作成 + resumed_from_job_id / job_events 記録)
- **合計 487 passing** (v0.8.3.1: 465 → v0.9.0: 487)

### 互換性

- `resume_job` は **experimental** スコープ。v0.9.x 内で API 変更ある可能性。
- `src/visa_mcp/testing/` パッケージも experimental (v1.0 で外部公開検討)。
- Stable API 不変。

### スコープ外 (v0.9.0)

- 本物の LLM 呼び出し評価 (v1.0)
- self-repair loop (v0.9.1)
- 測定結果 export API (v0.9.1)
- audit SQLite migration (v0.9.3)
- step-level fully idempotent checkpoint (v1.1+)

---

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
