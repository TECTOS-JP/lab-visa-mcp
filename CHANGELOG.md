# 変更履歴

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
