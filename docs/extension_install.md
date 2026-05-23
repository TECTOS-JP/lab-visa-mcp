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
2. install 元 path が `extensions_dir` (例: `~/.visa-mcp/extensions/`)
   配下にある場合は拒否 (`extension_source_inside_extensions_dir`)
3. `validate_extension_file` (path 安全性 + sub-files 検証) を必ず通す
4. 既存 `extension_id` を lockfile から確認
   - 同 id があり `--force` 指定なし → `extension_duplicate_install` error
5. pack directory 内 file を temp directory に **staged copy**
   (詳細は次節 — 全ファイル対象だが除外ルールあり)
6. install path への切り替え (**v1.3.1 backup-rename**):
   1. 既存 `install_path` を `install_path.bak-<UTC ts>` へ rename
   2. tmpdir を `install_path` へ rename
   3. 成功時 backup を削除
   4. 失敗時 backup を `install_path` へ戻す
7. `.install_meta.json` に **sha256 checksums** + manifest を保存
8. lockfile (`extensions.lock.json`) を更新 (既存 entry を置換)

エラー時は temp directory を clean up し、install path は変更されない
(force で既存があった場合は backup から復元される)。

### staged copy の対象

`extension.yaml` が置かれた directory 内の **全ファイル** が再帰コピー
される (manifest の `contents.*` で参照されていない補助 file も含む)。
ただし以下は v1.3.1 から **除外**:

| 種別 | 例 |
|------|-----|
| directory | `.git/` `__pycache__/` `.mypy_cache/` `.pytest_cache/` `.idea/` `.vscode/` `node_modules/` |
| ファイル名 | `.DS_Store` `Thumbs.db` |
| 拡張子 | `*.pyc` `*.pyo` `*.tmp` `*.swp` |

これらは definition pack の動作に不要で、サイズ膨張・誤公開
(SCM metadata) の原因になりやすいため install には持ち込まない。

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
- `visa-mcp extension validate-installed --builtin-registry <path>`
  (現状は repo 内 `registry/INDEX.yaml` を default として利用)
- `.install_meta.json` `source_path` の相対化 / 表示制御 (privacy)

## 関連 docs

- [`extension_policy.md`](extension_policy.md) — v1.2 拡張ポリシー
- [`definition_packs.md`](definition_packs.md) — `extension.yaml` 仕様
- [`extension_registry_overlay.md`](extension_registry_overlay.md) —
  built-in registry と installed extension の統合
- [`v1_stability_policy.md`](v1_stability_policy.md)
