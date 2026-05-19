# visa-mcp

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**MCP server for controlling GPIB / USB / Serial / LAN instruments via PyVISA.**

LLM（Claude Code / Claude Desktop など MCP 対応クライアント）から、SCPI 計測器と非 SCPI 計測器の両方を統一的に操作できるサーバーです。マニュアルから抽出したコマンドを YAML で定義すれば、機器固有の知識なしに自然言語で計測を自動化できます。

📝 **背景・設計思想・実機検証の詳細**: [Note 記事「Claude から計測器を動かす ── visa-mcp の設計と実機検証」](https://note.com/kkondou_tectos/n/n3fba2f27c31c)

## 特徴

- 🔌 **VISA 経由のあらゆるインタフェース対応**: GPIB / USB / RS-232C / LAN (VXI-11, HiSLIP)
- 📋 **YAML で機器コマンド定義**: SCPI/独自プロトコル問わず宣言的に定義
- 🔍 **`*IDN?` 自動識別** + **手動バインディング** (旧世代非SCPI機器対応)
- ✅ **型・範囲・enum 検証**: 安全に SCPI コマンドを構築
- 📄 **PDF マニュアル取り込み**: pdfplumber でコマンド候補を自動抽出
- ⚡ **非同期実装**: FastMCP + asyncio で複数機器並行制御

## 動作確認済み機器

| メーカー | モデル | インタフェース | プロトコル |
|---------|--------|--------------|-----------|
| Kikusui | PMX35-3A 直流安定化電源 | USB | SCPI |
| Yokogawa | 7563 6桁ディジタルマルチ温度計 | GPIB | 独自（非SCPI） |

## クイックスタート

### 1. インストール

前提: Python 3.10+ / NI-VISA または互換 VISA ライブラリ（Keysight IO Libraries Suite / PyVISA-Py 等）

```bash
git clone https://github.com/TECTOS-JP/visa-mcp.git
cd visa-mcp
pip install -e .
```

### 2. Claude Desktop に登録

`%APPDATA%\Claude\claude_desktop_config.json`（Windows）または `~/Library/Application Support/Claude/claude_desktop_config.json`（macOS）に追記：

```json
{
  "mcpServers": {
    "visa-mcp": {
      "command": "python",
      "args": ["-m", "visa_mcp.server"],
      "cwd": "<path-to-visa-mcp>"
    }
  }
}
```

Claude Desktop を再起動。

### 3. 動作確認

Claude に話しかける：

> 「visa-mcp に接続されている計測器を一覧してください」
>
> 「USB0::0x... を identify_instrument で識別して、5V 出力するように設定してください」

## 提供される MCP ツール（12 個）

| ツール | 用途 |
|-------|------|
| `list_resources` | 接続中の VISA リソースを列挙 |
| `identify_instrument` | `*IDN?` で機器を識別し定義をバインド |
| `identify_all_instruments` | 全リソースを一括識別 |
| `list_identified_instruments` | 既に識別済みのセッション一覧 |
| `bind_definition` | `*IDN?` 非対応機器に定義を手動バインド |
| `list_available_definitions` | ロード済みの YAML 定義一覧 |
| `list_commands` | 識別済み機器の利用可能コマンド表示 |
| `execute_named_command` | 型安全に名前付きコマンドを実行 |
| `query_instrument` | 任意の SCPI クエリを送信 |
| `send_command` | 任意の SCPI コマンドを送信（write） |
| `reload_definitions` | 定義ファイルを再読込 |
| `extract_pdf_commands` | PDF マニュアルからコマンド候補を抽出 |

詳細は [docs/mcp_tools_reference.md](docs/mcp_tools_reference.md) を参照。

## 新しい機器を追加する

`instruments/_template.yaml` をコピーしてカスタマイズします：

```yaml
metadata:
  manufacturer: "YourVendor"
  model: "Model123"
  description: "DC Power Supply"

identification:
  manufacturer_match: "YOURVENDOR"
  model_regex: "Model123"

connection:
  default_timeout_ms: 3000
  read_termination: "\n"
  write_termination: "\n"

commands:
  set_voltage:
    scpi: "VOLT {voltage}"
    type: "write"
    description: "出力電圧を設定"
    parameters:
      - name: voltage
        type: "float"
        range: [0, 30]
```

詳細は [docs/adding_instruments.md](docs/adding_instruments.md) を参照。`examples/instruments/` に実例（PMX35-3A / 7563）を収録しています。

## アーキテクチャ

```
┌──────────────────────┐
│  Claude / MCP Client │
└──────────┬───────────┘
           │ MCP (stdio)
┌──────────▼───────────┐
│   FastMCP Server     │  ← src/visa_mcp/server.py
│  ┌────────────────┐  │
│  │ Tool Handlers  │  │  ← src/visa_mcp/tools/
│  ├────────────────┤  │
│  │ Session Mgr    │  │  ← セッション・定義紐付け
│  ├────────────────┤  │
│  │ VISA Manager   │  │  ← PyVISA 非同期ラッパー
│  └────────────────┘  │
└──────────┬───────────┘
           │ VISA
┌──────────▼───────────┐
│  Instrument (GPIB/   │
│   USB/Serial/LAN)    │
└──────────────────────┘
```

## 開発

```bash
# 開発依存込みインストール
pip install -e ".[dev]"

# テスト実行
pytest

# サーバー単独起動（デバッグ用）
python -m visa_mcp.server
```

## ライセンス

MIT License — 詳細は [LICENSE](LICENSE) を参照。

## 注意事項

- **計測器マニュアル PDF はリポジトリに含まれていません**。各メーカーの公式サイトからダウンロードしてください。
- **電源 / 高電圧機器を扱う場合は安全保護機能（OVP / OCP 等）を必ず設定してから出力 ON してください**。本ソフトウェアは安全機能の代替ではありません。
- LLM が誤ったコマンドを送信する可能性があります。**接続機器・配線・被測定物の安全範囲は人間が責任を持って確認してください**。

## Acknowledgments

- [PyVISA](https://github.com/pyvisa/pyvisa) — Python VISA wrapper
- [FastMCP](https://github.com/jlowin/fastmcp) — MCP server framework
- [Model Context Protocol](https://modelcontextprotocol.io/) — Anthropic
