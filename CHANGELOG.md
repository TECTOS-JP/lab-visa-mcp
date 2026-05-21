# 変更履歴

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
