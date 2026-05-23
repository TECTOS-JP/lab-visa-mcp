# Contributing to visa-mcp

このプロジェクトは AI エージェント実験自動化評価基盤として、外部
contributor からの **definition pack** / **benchmark task** /
**bug fix** / **docs** の貢献を歓迎します。

> v1.0 で MCP API は凍結済 (詳細は `docs/v1_stability_policy.md`)。
> 既存 Stable tool の名称・引数変更は v1.x 内で **行いません**。

## 1. Definition pack を作る (推奨手順)

v1.7 で authoring CLI が揃っています。以下の流れで誰でも作れます。

```bash
# 1. scaffold (空 directory に雛形生成)
visa-mcp extension init my_pack --template instrument_pack \
    --id com.example.my_pack --author "Acme"

# 2. instrument 等を編集

# 3. doctor で「次に何を直せば良いか」をまとめて確認
visa-mcp extension doctor my_pack/extension.yaml --strict

# 4. package preview (zip を作らない)
visa-mcp extension package my_pack/extension.yaml --dry-run

# 5. strict で package 化
visa-mcp extension package my_pack/extension.yaml --strict

# 6. install して動作確認
visa-mcp extension install dist/com.example.my_pack-0.1.0.visa-mcp-ext.zip
visa-mcp extension check com.example.my_pack
```

詳細は [`docs/extension_authoring.md`](docs/extension_authoring.md)。

## 2. Definition pack PR の checklist

registry / 公開向け pack は **以下すべて** が望ましいです。CI で
strict gate を通すための最低ラインです。

- [ ] `visa-mcp extension doctor <ext.yaml> --strict` が通る
      (`ready_for_registry_review: true`)
- [ ] `visa-mcp extension package <ext.yaml> --strict` が成功
- [ ] `visa-mcp extension verify-package dist/<...>.zip` が成功
- [ ] `executable_code: false` (v1.x ポリシー、変更不可)
- [ ] `README.md` が pack 内に含まれる
- [ ] `catalog.summary` / `catalog.license` を埋めている
- [ ] `catalog.safety_notes` を 1 件以上書いている (実機系の場合は必須)
- [ ] `support_level` が draft より上の場合、根拠 (`validation_evidence`
      or PR description) を明示
- [ ] `support_level: verified` の instrument には
      `metadata.validation_evidence` を必須
- [ ] `tests/` ローカル test を回しても回帰なし
      (`pytest -k "not hardware_integration"`)

詳細 checklist は
[`docs/extension_publishing_checklist.md`](docs/extension_publishing_checklist.md)。

## 3. Bug fix / feature PR の流れ

1. issue を立てる (再現手順 / 期待動作 / 環境)。新規 MCP tool は
   慎重に判断するため、まず discussion を推奨。
2. branch を切る (`fix/<id>` / `feat/<id>`)。
3. 必要なら関連 docs を更新。`docs/` 配下を touch する PR は歓迎。
4. tests を回す (`python -m pytest -k "not hardware_integration"`)。
   ハードウェア依存テストは local hardware が無いと skip されるが、
   CI で network/visa を必要としないテストはすべて pass する必要あり。
5. CHANGELOG.md に entry を追加。version は maintainer が bump。

## 4. ポリシー

- **新規 MCP tool 追加は最小限**。CLI 化で済む機能は CLI に閉じる
  (例: extension install / catalog / doctor)。
- `executable_code: true` および remote install / git pull は **v1.x で
  対応しない**。改めて議論が必要 (`docs/v1_stability_policy.md`)。
- error_class / response_envelope は v1.0 で凍結済。新規 error_class の
  追加は OK、既存の意味変更は NG。
- raw VISA 操作は v1.0 から `VISA_MCP_ALLOW_RAW=1` 環境変数 gate。

## 5. CoC

- 機材 / 安全への配慮: 「実機未検証」は明示する。`support_level: draft`
  / `safety_notes` をきちんと書く。
- LLM 生成 instrument YAML は `support_level: draft` で出すこと
  (validation_evidence が無いので)。
- 機器メーカー manual の引用は出典を明示。

## 6. 参考

- 設計方針: [`docs/v1_stability_policy.md`](docs/v1_stability_policy.md)
- error taxonomy: [`docs/error_taxonomy.md`](docs/error_taxonomy.md)
- compatibility: [`docs/compatibility.md`](docs/compatibility.md)

質問・提案は GitHub Discussions / Issues へ。
