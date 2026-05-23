# Definition Pack Install (v1.3, experimental)

合言葉: **「definition pack を『作れる』から『安全に導入できる』へ」**

v1.2 で `extension.yaml` を **検証** できるようになった。v1.3 ではその先、
**ローカル user 領域へ安全に install / list / uninstall** できるようにする。

> ⚠ **Python plugin (実行コード) は引き続き未対応**。
> **リモート URL からの install も未対応** (ローカル path のみ)。

## install 先

| 項目 | path |
|------|------|
| extension 本体 | `~/.visa-mcp/extensions/<extension_id>/` |
| lockfile | `~/.visa-mcp/extensions.lock.json` |
| install metadata | `~/.visa-mcp/extensions/<extension_id>/.install_meta.json` |

built-in registry (`<repo>/registry/`) と **完全分離**。package 更新時にも
ユーザー追加 definition pack は壊れない。

## CLI

```bash
# 検証 (v1.2 から)
visa-mcp validate extension <path-to-extension.yaml>

# install (v1.3 新規)
visa-mcp extension install <path-to-extension.yaml>
visa-mcp extension install <path> --force        # 同 id 上書き

# list (v1.3 新規)
visa-mcp extension list [--json]

# uninstall (v1.3 新規)
visa-mcp extension uninstall <extension_id>

# overlay registry 整合検証 (v1.3 新規)
visa-mcp extension validate-installed [--json]
```

## install フロー

1. `extension.yaml` を read
2. `validate_extension_file` (path 安全性 + sub-files 検証) を必ず通す
3. 既存 `extension_id` を lockfile から確認
   - 同 id があり `--force` 指定なし → `extension_duplicate_install` error
4. pack 内 file を temp directory に staged copy
5. install path に **atomic rename** (途中失敗で中途半端な状態を残さない)
6. `.install_meta.json` に **sha256 checksums** + manifest を保存
7. lockfile (`extensions.lock.json`) を更新 (既存 entry を置換)

エラー時は temp directory を clean up し、install path は変更されない。

## duplicate / version conflict

| 状況 | デフォルト | --force |
|------|-----------|---------|
| 同 `extension_id` + 同 version | 拒否 (`extension_duplicate_install`) | 上書き |
| 同 `extension_id` + 異 version | 拒否 | 上書き |
| 異 `extension_id` | 許可 | n/a |

v1.3 では **`--upgrade` は無く `--force` のみ**。version 差分は metadata
に残るため、後から手動で確認可能。

## install metadata (`.install_meta.json`)

```json
{
  "extension_id": "tectos.mock.basic",
  "version": "0.1.0",
  "installed_at": "2026-05-23T12:00:00+00:00",
  "source_path": "/abs/path/to/source/extension.yaml",
  "visa_mcp_version": "1.3.0",
  "checksums": {
    "extension.yaml": "9f86d081...",
    "instruments/mock_psu.yaml": "..."
  },
  "manifest": { ... entire manifest ... }
}
```

## lockfile (`extensions.lock.json`)

```json
{
  "installed_extensions": [
    {
      "extension_id": "tectos.mock.basic",
      "version": "0.1.0",
      "path": "~/.visa-mcp/extensions/tectos.mock.basic",
      "installed_at": "2026-05-23T12:00:00+00:00",
      "visa_mcp_version": "1.3.0"
    }
  ]
}
```

## 安全策

v1.3 の install で **以下は実行されない / 許可されない**:

- `executable_code: true` (manifest schema レベルで拒否)
- Python code import / exec
- arbitrary path への copy (path traversal は `extension_path_outside_pack`)
- リモート URL からの download
- 自動 update
- signature / trust store (v1.x では未対応)

v1.3 の install で **行われる**:

- ローカル file から install 先への safe staged copy
- sha256 ベースの integrity 記録
- lockfile 更新 (atomic write)
- `validate_extension_file` 経由の事前 schema 検証

## uninstall

`extensions.lock.json` から対応 entry を削除し、install path を `rmtree`
する。ユーザーが直接 install path を編集していた場合は失われる
(metadata に sha256 があるため、検証は可能)。

## v1.3 で対応しない (v1.4+ 候補)

- `visa-mcp extension upgrade` (専用フラグ)
- リモート URL / git からの install
- signature / digital signing / trust store
- automatic update
- plugin entry_points discovery
- remote registry
- Python code 実行

## 関連 docs

- [`extension_policy.md`](extension_policy.md) — v1.2 拡張ポリシー
- [`definition_packs.md`](definition_packs.md) — `extension.yaml` 仕様
- [`extension_registry_overlay.md`](extension_registry_overlay.md) —
  built-in registry と installed extension の統合
- [`v1_stability_policy.md`](v1_stability_policy.md)
