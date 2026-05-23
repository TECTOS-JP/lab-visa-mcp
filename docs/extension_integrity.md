# Extension Integrity (v1.4, experimental)

合言葉: **「install できる」→「install したものを信頼して使い続けられる」**

v1.3 で `~/.visa-mcp/extensions/<extension_id>/.install_meta.json` に
各 file の **sha256 checksum** を保存するようにした。v1.4 ではそれを
使って、installed definition pack の **drift 検出** と **strict
validation** を CLI に追加する。

> v1.4 でも MCP tool 数は変わらない (Stable 43 / Experimental 7 / 計 50)。
> integrity check は管理操作で、実験中の AI エージェントが頻繁に呼ぶもの
> ではないため CLI 側に閉じている。

## CLI

```bash
# 全 installed extension を check
visa-mcp extension check
visa-mcp extension check --json

# 特定 extension のみ
visa-mcp extension check tectos.mock.basic

# strict mode (warning も error に格上げ; CI / registry 掲載検査向け)
visa-mcp extension check --strict

# 1 pack の詳細表示
visa-mcp extension inspect tectos.mock.basic
visa-mcp extension inspect tectos.mock.basic --json

# overlay registry の表示 (builtin + extension)
visa-mcp registry overlay
visa-mcp registry overlay --source extension
visa-mcp registry overlay --source builtin
visa-mcp registry overlay --json

# uninstall を実行せずに「何が削除されるか」を表示
visa-mcp extension uninstall tectos.mock.basic --dry-run
```

## `extension check` の検査内容

| 項目 | 検査 |
|------|------|
| lockfile entry | `extensions.lock.json` に entry がある |
| install path | install path が存在する |
| `.install_meta.json` | 存在し JSON として読める |
| sha256 drift | 記録された各 file の checksum が一致 |
| missing_file | 記録された file が消えていない |
| extra_file | metadata 外の file が増えていない (warning) |
| extension.yaml | 再 validate (path safety / executable_code / schema) |
| strict | normal 時 warning を error 化 |

## integrity 値

| 値 | 意味 |
|----|------|
| `ok` | drift なし、再 validate も問題なし |
| `modified` | 1 つ以上の file が install 後に変更された |
| `missing_file` | 記録された file が消えている |
| `extra_file` | metadata に無い余剰 file がある (warning レベル) |
| `invalid` | `.install_meta.json` / `extension.yaml` が無い、再 validate 失敗 |

## 出力例 (JSON)

```json
{
  "reports": [
    {
      "status": "error",
      "extension_id": "tectos.mock.basic",
      "version": "0.1.0",
      "install_path": "/home/u/.visa-mcp/extensions/tectos.mock.basic",
      "integrity": "modified",
      "files_checked": 6,
      "errors": [
        {
          "error_class": "extension_checksum_mismatch",
          "message": "instruments/mock_psu.yaml: sha256 mismatch",
          "details": {
            "path": "instruments/mock_psu.yaml",
            "expected": "...",
            "actual": "..."
          }
        }
      ],
      "warnings": [],
      "recommended_actions": [
        {
          "action": "reinstall",
          "command": "visa-mcp extension install /path/to/extension.yaml --force"
        },
        {
          "action": "uninstall",
          "command": "visa-mcp extension uninstall tectos.mock.basic"
        }
      ]
    }
  ]
}
```

drift が出た場合の対応は **AI ではなく人間 / CI** が判断する。
v1.4 では自動 repair / auto-reinstall は実装しない (v1.5+ 候補)。

## strict mode

`visa-mcp validate extension <path> --strict` と
`visa-mcp extension check --strict` で **strict** モードが使える。

`validate extension --strict` で error に格上げされるもの:

| 通常 | strict |
|------|--------|
| `empty_contents` warning | `strict_empty_contents` error |
| `registry_entries_format` warning | `strict_registry_entries_format` error |
| 参照 instrument の `support_level=draft` (warning) | `strict_support_level_draft` error |
| `support_level=verified` で `validation_evidence` 空 | `strict_verified_requires_evidence` error |

`extension check --strict` では、上記に加え整合性 warning
(`extension_extra_file` 等) も error に格上げされる。

## validation_evidence (任意、v1.4 新規)

instrument YAML の `metadata.validation_evidence` (任意 dict) で、
**`support_level=verified` の根拠**を構造化して残せる。

例:

```yaml
metadata:
  manufacturer: "Kikusui"
  model: "PMX-A"
  support_level: "verified"
  validation_evidence:
    tested_by: "TECTOS"
    tested_at: "2026-05-23"
    interface: "USB"
    firmware: "1.23"
    tested_items:
      - identify
      - set_voltage
      - query_voltage
      - verify
      - safe_shutdown
    notes: "Basic voltage output and readback tested."
```

v1.4 では **schema レベルでは subkey 検証しない** (freeform dict)。
ただし strict mode で `support_level=verified` なのに空 dict のときは
error になる。

## v1.4 で対応しない (v1.5+ 候補)

- 自動 repair (`extension repair`)
- リモート install / pull / signature
- Python plugin
- backend plugin
- replay backend 実装
- MCP tool 追加 (integrity 系を MCP 化するかは v1.5+ で判断)

## 関連 docs

- [`extension_install.md`](extension_install.md) — install フロー / lockfile / sha256 metadata
- [`extension_registry_overlay.md`](extension_registry_overlay.md) — overlay registry
- [`extension_policy.md`](extension_policy.md) — v1.2 拡張ポリシー
- [`definition_packs.md`](definition_packs.md) — `extension.yaml` 仕様
- [`registry_contribution.md`](registry_contribution.md) — 機器定義 registry 掲載
