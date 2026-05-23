# Extension Packaging (v1.5, experimental)

合言葉: **「作れる / install できる / 整合できる」→「配布可能な成果物としてまとめられる」**

v1.4 までで「local pack の検証 / install / integrity」が揃った。
v1.5 では、外部 contributor が作った definition pack を **配布可能な
zip パッケージ**にまとめ、受け取り側で **再検証**できるようにする。

> v1.5 は **packaging まで**。zip からの install / remote install /
> 署名 / trust store には進まない (v1.6+ 候補)。

## CLI

```bash
# pack 化 (default: <pack_dir>/dist/<extension_id>-<version>.visa-mcp-ext.zip)
visa-mcp extension package path/to/extension.yaml
visa-mcp extension package path/to/extension.yaml --output dist/
visa-mcp extension package path/to/extension.yaml --strict
visa-mcp extension package path/to/extension.yaml --json

# package 整合検証
visa-mcp extension verify-package dist/tectos.mock.basic-0.1.0.visa-mcp-ext.zip
visa-mcp extension verify-package dist/xxx.zip --json
```

## Package 形式

```
<extension_id>-<version>.visa-mcp-ext.zip
├── extension.yaml
├── package_manifest.json        ← v1.5 必須
├── checksums.sha256             ← v1.5 必須
├── README.md                    (任意 / --strict で error 候補)
├── instruments/
├── benchmarks/
├── templates/
├── registry_entries/
└── mock_scenarios/
```

### package_manifest.json

```json
{
  "package_format": "visa-mcp-extension-package",
  "package_format_version": "1.0",
  "extension_id": "tectos.mock.basic",
  "extension_version": "0.1.0",
  "created_at": "2026-05-23T12:00:00+00:00",
  "created_by": "visa-mcp 1.5.0",
  "executable_code": false,
  "file_count": 8,
  "files": [
    {"path": "extension.yaml", "sha256": "..."},
    {"path": "instruments/mock_psu.yaml", "sha256": "..."}
  ],
  "checksums_file": "checksums.sha256",
  "checksums_sha256": "..."
}
```

`executable_code` は v1.5 では **常に `false`**。v1.x では Python plugin
を許可しないポリシーが続く。

### checksums.sha256

各行 `<sha256>  <relative-path>` 形式 (`sha256sum` と互換)。

```
9f86d081...  extension.yaml
b94d27b9...  instruments/mock_psu.yaml
```

## package 時の検査

1. `validate_extension_file()` を通す (`--strict` 指定時は strict)
2. pack directory 内の file を再帰収集 (除外ルール適用)
3. `..` や絶対 path を含む rel は拒否 (`package_path_unsafe`)
4. file 数 0 なら拒否 (`empty_package`)
5. `checksums.sha256` を生成
6. `package_manifest.json` を生成
7. deterministic な順序で zip 化 (sorted by rel path)
8. zip 全体の sha256 を計算して返却

### 除外ルール (staging copy と同じ)

| 種別 | 例 |
|------|-----|
| directory | `.git/` `__pycache__/` `.mypy_cache/` `.pytest_cache/` `.idea/` `.vscode/` `node_modules/` |
| file 名 | `.DS_Store` `Thumbs.db` |
| 拡張子 | `*.pyc` `*.pyo` `*.tmp` `*.swp` |

pack directory 内の `package_manifest.json` / `checksums.sha256` は
**package 生成側で常に上書き**されるため、元 pack に置かれていても無視
される (誤って手書きしないこと)。

## verify-package の検査

1. zip として読める (`package_invalid_zip`)
2. すべての member が **zip slip safe** (`package_zip_slip`)
   - 絶対 path / drive letter / `..` 含みを拒否
3. `extension.yaml` / `package_manifest.json` / `checksums.sha256` 必須
4. `package_manifest.json` parse + `package_format` 値域
5. `executable_code: true` を error (`package_executable_code_true`)
6. zip 内 file の sha256 と `checksums.sha256` 行を照合
   (`package_checksum_mismatch`)
7. `package_manifest.files[*].sha256` と実 file を照合
   (`package_manifest_sha_mismatch`)
8. tmp 展開後 `validate_extension_file()` を再実行

## strict mode

`package --strict` で:

- `support_level=verified` で `validation_evidence` 空 → error
  (`strict_verified_requires_evidence`)
- `registry_entries[*]` の必須 field / pack 内 path / support_level 一致
  (v1.4.1 の strict_registry_entry_* 系)
- pack に `README.md` が無い → error
  (`strict_missing_pack_readme`)

通常 (`--strict` 無し) では README 無しは warning
(`missing_pack_readme`) のみ。

## 出力例 (JSON)

```json
{
  "package": {
    "status": "ok",
    "extension_id": "tectos.mock.basic",
    "version": "0.1.0",
    "package_path": "dist/tectos.mock.basic-0.1.0.visa-mcp-ext.zip",
    "package_sha256": "...",
    "file_count": 8,
    "errors": [],
    "warnings": [],
    "manifest": { ... }
  }
}
```

## v1.5 で対応しない (v1.6+ 候補)

- **zip からの install** (`visa-mcp extension install <zip>`) ── v1.6 候補
- remote URL / git からの install
- registry pull CLI
- signature / trust store / 公開鍵検証
- automatic update
- Python plugin / entry_points discovery

順序としては:

```
v1.5: package 作成 / 検証          ← 本リリース
v1.6: local zip install (検討)
v1.7+: remote registry / signature (慎重に判断)
```

## 関連 docs

- [`extension_publishing_checklist.md`](extension_publishing_checklist.md) ── 配布前チェックリスト
- [`extension_integrity.md`](extension_integrity.md)
- [`extension_install.md`](extension_install.md)
- [`extension_registry_overlay.md`](extension_registry_overlay.md)
- [`error_taxonomy.md`](error_taxonomy.md)
