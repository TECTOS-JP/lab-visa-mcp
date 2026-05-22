# visa-mcp

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**MCP server for controlling GPIB / USB / Serial / LAN instruments via PyVISA.**

LLM（Claude Code / Claude Desktop など MCP 対応クライアント）から、SCPI 計測器と非 SCPI 計測器の両方を統一的に操作できるサーバーです。マニュアルから抽出したコマンドを YAML で定義すれば、機器固有の知識なしに自然言語で計測を自動化できます。

📝 **記事**
- [v0.3.0: 計測器を「指示で動かす」から「手順ごと預ける」へ](https://note.com/kkondou_tectos/n/nb23422933286) — Recipes / 応答パーサ / 安全制約
- [v0.1.0: Claude から計測器を動かす ── 設計と実機検証](https://note.com/kkondou_tectos/n/n3fba2f27c31c) — 設計思想と Yokogawa 7563 救出記

## 特徴

- 🔌 **VISA 経由のあらゆるインタフェース対応**: GPIB / USB / RS-232C / LAN (VXI-11, HiSLIP)
- 📋 **YAML で機器コマンド定義**: SCPI/独自プロトコル問わず宣言的に定義
- 🔍 **`*IDN?` 自動識別** + **手動バインディング** (旧世代非SCPI機器対応)
- ✅ **型・範囲・enum 検証**: 安全に SCPI コマンドを構築
- 🛡️ **安全制約システム** (v0.2.0): 絶対最大定格・前提条件・自然言語注意事項を YAML で宣言、3 段階の安全モード (`strict` / `advisory` / `permissive`)、override 機構、監査ログ
- 🍳 **Recipe (典型ワークフロー)** (v0.3.0): 複数コマンドの安全な順序を YAML で宣言、`$var * 1.1` のような式評価対応、安全制約と完全統合
- 🔎 **応答の構造化パース** (v0.3.0): ベンダ独自フォーマット (例: Yokogawa 7563 の `NTKC+00027.2E+0`) を正規表現で構造化辞書に変換
- 🗂️ **動作状態・物理インタフェース定義** (v0.3.0): 起動シーケンス・モード・端子情報を YAML で宣言、LLM に共有
- ⏱️ **Job モデル + wait step** (v0.5.0): recipe をバックグラウンド実行、状態機械 (queued / running / waiting / completed / failed / cancelled / timeout / interrupted)、SQLite 永続化、3 段階キャンセル、`recommended_next_actions` で LLM への次手提示
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

## 提供される MCP ツール（42 個 / raw 系は別途オプトイン）

### 識別・情報

| ツール | 用途 |
|-------|------|
| `list_resources` | 接続中の VISA リソースを列挙 |
| `identify_instrument` | `*IDN?` で機器を識別し定義をバインド |
| `identify_all_instruments` | 全リソースを一括識別 |
| `list_identified_instruments` | 既に識別済みのセッション一覧 |
| `bind_definition` | `*IDN?` 非対応機器に定義を手動バインド |
| `list_available_definitions` | ロード済みの YAML 定義一覧 |
| `list_commands` | 識別済み機器の利用可能コマンド表示 |
| `get_instrument_info` | 機器仕様・安全制約・recipes 等を一括取得 |
| `list_safety_constraints` | 安全制約のみを抽出 |
| `reload_definitions` | 定義ファイルを再読込 |

### 同期実行

| ツール | 用途 |
|-------|------|
| `execute_named_command` | 型安全に名前付きコマンドを実行 |
| `validate_operation` | 実行せずに事前検証 (dry-run) |
| `list_recipes` | 利用可能な典型ワークフロー一覧 |
| `execute_recipe` | 複数コマンドの安全な順次実行 |

### **Job (バックグラウンド実行)** ★v0.5.0 新規

| ツール | 用途 |
|-------|------|
| `start_recipe_job` | recipe を Job として登録、即 job_id 返却 |
| `start_wait_job` | 単発 wait ジョブ (seconds / until / condition / stable_value) を起動 ★v0.5.1 |
| `get_job_status` | Job の現在状態 + polling/group 進捗 |
| `get_job_result` | 完了/失敗時の完全結果 + 次手候補 |
| `list_jobs` | Job 一覧 (status / owner / limit で絞り込み) |
| `cancel_job` | Job キャンセル (immediate / after_current_step / safe_shutdown) |

### **Group / Map (並列実行)** ★v0.6.0 新規

| ツール | 用途 |
|-------|------|
| `list_groups` | `instrument_groups` 一覧 |
| `list_experiment_units` | `experiment_units` 一覧 |
| `start_group_query_job` | グループ全機器に同じ query を並列実行 |
| `start_map_recipe_job` | 異なる条件で各 unit に recipe を並列実行 (100 サンプル等) |

### **状態・モニタ (self-awareness + persistence)** ★v0.7.0 新規

| ツール | 用途 |
|-------|------|
| `describe_instrument` | 機器の能力サマリ (identity / capabilities / state_keys / recommended_usage) |
| `get_state` | `state_query` 定義に従って機器の現在状態を取得 (cache 対応) |
| `get_last_measurement` | 測定値キャッシュから最新値 (古ければ自動再取得) |
| `start_monitor` | 機器を定期測定する Monitor Job を起動 (`monitor_data` に保存) |
| `stop_monitor` | Monitor Job を停止 |
| `get_monitor_data` | Monitor の時系列データを取得 (大量データ向け別ツール、limit≤10000) |
| `prune_monitor_data` | Monitor Job データを削除 (monitor_id 指定 / older_than_days 指定) ★v0.7.0.1 |

### **Experiment DSL (LLM 向け実験計画)** ★v0.8.0 新規

| ツール | 用途 |
|-------|------|
| `validate_experiment_plan` | DSL plan を検証 (resource/command/safety/verify/sweep 上限など 15 項目) |
| `dry_run_plan` | 実機 I/O 無しで rendered SCPI + safety + verify summary を返す |
| `start_experiment_job` | validate → compile → persist → Job 実行 (experiment_plans に保存) |
| `save_experiment_template` | 再利用可能 DSL テンプレートを SQLite に保存 |
| `list_experiment_templates` | 保存済みテンプレート一覧 ★v0.8.0.1 |
| `get_experiment_template` | 指定 name のテンプレート (plan JSON 含む) ★v0.8.0.1 |
| `start_experiment_job_from_template` | template に override (name/unit/bindings/parameters/owner) を適用して実行 (dry_run 対応) ★v0.8.3 |

### **Observation (実験ビュー)** ★v0.8.2 新規

| ツール | 用途 |
|-------|------|
| `get_experiment_timeline` | Job 内の時系列イベント (kind / severity / title / summary、monitor_sample デフォルト除外) |
| `get_job_live_view` | 実行中 Job の集約 (current_phase enum / active_waits / latest_measurements / recent_errors) |
| `get_job_summary` | 完了 Job の構造化要約 (key_results / failures / verify_summary / recommended_next_actions) |

### 取り込み

| ツール | 用途 |
|-------|------|
| `extract_pdf_commands` | PDF マニュアルからコマンド候補を抽出 |

加えて、環境変数 `VISA_MCP_ENABLE_RAW_COMMANDS=1` で **未検証の任意 SCPI** を送る危険ツールを 2 個追加可能（`unsafe_send_command` / `unsafe_query_instrument`、strict モードでは登録されない）。詳細は [docs/safety.md](docs/safety.md)。

Job モデルの詳細 (state machine / cancel mode / timeout / 再起動セマンティクス / recommended_next_actions) は [docs/jobs.md](docs/jobs.md) を参照。

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
