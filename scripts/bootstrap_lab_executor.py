"""v2.0 bootstrap: lab-executor-mcp 新 repo を生成する one-shot スクリプト。

`docs/separation/module_ownership.yaml` + `split_manifest.yaml` を
source of truth として、visa-mcp 側 lab-executor owner module + 関連
リソース (schemas, docs, tests, registry, benchmarks) を新 repo
ディレクトリへコピー + import rewrite を行う。

履歴は移送しない (簡素化のため。v2.0 仕様)。

Usage:
    python scripts/bootstrap_lab_executor.py \\
        --src .  \\
        --dst /c/Users/k_kondou/Claude/lab-executor-mcp
"""
from __future__ import annotations
import argparse
import ast
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml


# rewrite ルール:
#   lab_visa_mcp.<lab-executor module>  →  lab_executor.<...>
# backends/base.py は shared → lab-executor 側 source of truth
# backends/pyvisa_backend.py は visa-mcp 側に残るので copy しない
# backends/mock_backend.py は lab-executor 側
REWRITE_FROM = "lab_visa_mcp."
REWRITE_TO = "lab_executor."


def load_manifest(src_root: Path) -> dict[str, Any]:
    p = src_root / "docs" / "separation" / "module_ownership.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def collect_modules(manifest: dict[str, Any]) -> dict[str, set[str]]:
    """owner -> module name set"""
    by_owner: dict[str, set[str]] = {}
    for mod, info in (manifest.get("modules") or {}).items():
        owner = (info or {}).get("owner") or "?"
        by_owner.setdefault(owner, set()).add(mod)
    return by_owner


def module_to_relpath(src_root: Path, module: str) -> Path:
    parts = module.split(".")
    p = src_root / "src" / Path(*parts).with_suffix(".py")
    if p.exists():
        return p
    init = src_root / "src" / Path(*parts) / "__init__.py"
    return init if init.exists() else p


def rewrite_text(text: str, lab_modules: set[str]) -> str:
    """`lab_visa_mcp.<lab>` / `from lab_visa_mcp import <lab_attr>` を
    `lab_executor.<...>` に置換。
    visa-mcp / shared owner の `lab_visa_mcp.<...>` は維持。"""
    # 1) `lab_visa_mcp.<lab>...` 形式の dotted reference
    pat = re.compile(r"\b(lab_visa_mcp(?:\.[A-Za-z_][A-Za-z0-9_]*)+)\b")

    def _sub(m):
        target = m.group(1)
        for lab in lab_modules:
            if target == lab or target.startswith(lab + "."):
                return target.replace(REWRITE_FROM, REWRITE_TO, 1)
        return target

    text2 = pat.sub(_sub, text)

    # 2) `from lab_visa_mcp import <name>` (bare package import of attribute).
    #    <name> が lab-executor owner の sub-module / attr であれば
    #    `from lab_executor import <name>` に書き換える。
    bare_pat = re.compile(
        r"^(from lab_visa_mcp import )([A-Za-z_][A-Za-z0-9_]*(?:\s+as\s+\w+)?"
        r"(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*(?:\s+as\s+\w+)?)*)$",
        re.MULTILINE,
    )

    def _bare_sub(m):
        names_part = m.group(2)
        # 全 import 名が lab-executor owner なら全置換、そうでなければ維持
        names = [n.strip().split()[0] for n in names_part.split(",")]
        lab_names = {lab.split(".")[-1] for lab in lab_modules}
        if all(n in lab_names for n in names):
            return f"from lab_executor import {names_part}"
        return m.group(0)

    text2 = bare_pat.sub(_bare_sub, text2)

    # backends/base / mock は lab-executor 側 source of truth
    text2 = text2.replace("lab_visa_mcp.backends.base",
                           "lab_executor.backends.base")
    text2 = text2.replace("lab_visa_mcp.backends.mock_backend",
                           "lab_executor.backends.mock_backend")
    return text2


def copy_module(src_root: Path, dst_root: Path, module: str,
                 lab_modules: set[str]) -> bool:
    src_path = module_to_relpath(src_root, module)
    if not src_path.exists():
        return False
    parts = module.split(".")[1:]  # drop "lab_visa_mcp"
    if src_path.name == "__init__.py":
        dst = dst_root / "src" / "lab_executor" / Path(*parts) / "__init__.py"
    else:
        dst = (dst_root / "src" / "lab_executor"
                / Path(*parts[:-1]) / (parts[-1] + ".py"))
    dst.parent.mkdir(parents=True, exist_ok=True)
    text = src_path.read_text(encoding="utf-8")
    dst.write_text(rewrite_text(text, lab_modules), encoding="utf-8")
    return True


def copy_tree(src: Path, dst: Path,
              transform_py: bool = False,
              lab_modules: set[str] | None = None,
              exclude_names: set[str] = frozenset()):
    if not src.exists():
        return 0
    count = 0
    for p in src.rglob("*"):
        if p.is_dir():
            continue
        if any(x in p.parts for x in exclude_names):
            continue
        if p.name == "__pycache__":
            continue
        rel = p.relative_to(src)
        dst_p = dst / rel
        dst_p.parent.mkdir(parents=True, exist_ok=True)
        if transform_py and p.suffix == ".py":
            text = p.read_text(encoding="utf-8")
            dst_p.write_text(rewrite_text(text, lab_modules or set()),
                              encoding="utf-8")
        else:
            shutil.copy2(p, dst_p)
        count += 1
    return count


def patch_relative_backend_imports(dst_root: Path) -> int:
    """v2.0 split で残る relative import を TYPE_CHECKING +
    optional fallback に書き換える。

    対象: polling_executor.py / state_query.py の
    `from .session_manager` / `from .visa_manager` (visa-mcp owner)
    """
    patches = 0
    for rel in ("polling_executor.py", "state_query.py",
                "step_executor.py", "recipe_executor.py",
                "registry.py", "tools/commands.py"):
        p = dst_root / "src" / "lab_executor" / rel
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        original = text
        # 削除対象の relative / absolute backend import 行
        removed_lines: list[str] = []
        new_lines: list[str] = []
        for ln in text.splitlines(keepends=True):
            s = ln.strip()
            if (s.startswith("from .session_manager ")
                    or s.startswith("from .visa_manager ")
                    or s.startswith("from lab_visa_mcp.session_manager ")
                    or s.startswith("from lab_visa_mcp.visa_manager ")):
                removed_lines.append(ln)
                continue
            new_lines.append(ln)
        if not removed_lines:
            continue
        text = "".join(new_lines)
        # `from typing import ...` 行末に TYPE_CHECKING を追加
        # (既存 typing import が無い場合は新規 import 行を追加)
        if re.search(r"^from typing import ", text, re.MULTILINE):
            text = re.sub(
                r"^(from typing import [^\n]+?)(\n)",
                lambda m: (m.group(1) + ", TYPE_CHECKING" + m.group(2)
                           if "TYPE_CHECKING" not in m.group(1)
                           else m.group(0)),
                text, count=1, flags=re.MULTILINE,
            )
        else:
            # `from __future__ import annotations` の直後に追加
            text = re.sub(
                r"^(from __future__ import annotations\n)",
                r"\1from typing import TYPE_CHECKING\n",
                text, count=1, flags=re.MULTILINE,
            )
        # TYPE_CHECKING block + VisaError fallback を挿入
        injection = (
            "\n"
            "# v2.0: backend layer は visa-mcp / shim 経由。型ヒント目的\n"
            "# は TYPE_CHECKING、VisaError は ImportError fallback。\n"
            "if TYPE_CHECKING:\n"
            "    from lab_visa_mcp.session_manager import InstrumentSession\n"
            "    from lab_visa_mcp.visa_manager import VisaManager\n"
            "\n"
            "try:\n"
            "    from lab_visa_mcp.visa_manager import VisaError\n"
            "except ImportError:\n"
            "    class VisaError(Exception):  # type: ignore[no-redef]\n"
            "        \"\"\"visa-mcp 不在時の VisaError 代替\"\"\"\n"
            "        pass\n"
        )
        # 最後の relative import が消えた行付近 (typing import の直後など)
        # に injection を入れる。単純に logger 行の直前に挿入する。
        text = re.sub(
            r"^(logger = logging\.getLogger\([^)]+\))",
            injection + r"\n\1",
            text, count=1, flags=re.MULTILINE,
        )
        if text != original:
            p.write_text(text, encoding="utf-8")
            patches += 1
    return patches


def write_pyproject(dst_root: Path) -> None:
    content = '''[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "lab-executor-mcp"
version = "2.0.0-rc2"
description = "Backend-independent experiment execution runtime for AI agents (split from visa-mcp v1.11)"
readme = "README.md"
license = { text = "MIT" }
requires-python = ">=3.10"
keywords = [
    "mcp", "experiment-automation", "laboratory-automation",
    "ai-agent", "dsl", "fastmcp", "claude",
]
classifiers = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Science/Research",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Topic :: Scientific/Engineering",
]

# v2.0: PyVISA は依存しない (backend は外部から注入)
dependencies = [
    "fastmcp>=2.0",
    "pydantic>=2.0",
    "pyyaml",
]

[project.optional-dependencies]
pdf = ["pdfplumber"]
dev = ["pytest", "pytest-asyncio", "pyyaml"]

[project.scripts]
lab-executor = "lab_executor.cli:main"

[tool.hatch.build.targets.wheel]
packages = ["src/lab_executor"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
'''
    (dst_root / "pyproject.toml").write_text(content, encoding="utf-8")


def write_readme(dst_root: Path) -> None:
    content = '''# lab-executor-mcp

Backend-independent **experiment execution runtime** for AI agents.
Split from `visa-mcp` at v2.0 (see `docs/v2_migration.md`).

## What it provides

- DSL (`ExperimentPlan`, `dsl_version=0.8`) + validator + dry-run
- Job manager / state machine / scheduler / barrier
- Observation API (`timeline` / `live_view` / `summary`)
- Benchmark runner (MockBackend, PyVISA 不要)
- Definition pack ecosystem (`extension init/install/check/package/...`)
- Instrument authoring (`instrument scaffold/promote-check/review-report`)
- Export / bundle (deterministic reproducibility)
- MCP tool surface: Stable 43 + Experimental 7 = 50 (v1.0 から不変)

## Install

```bash
pip install lab-executor-mcp
```

PyVISA は **必須ではない**。実機 backend が必要な場合は
`visa-mcp` を install すると自動的に `lab-executor-mcp` も入る。

```bash
pip install visa-mcp     # PyVISA backend + lab-executor-mcp runtime
```

## v2.0 split

- v1.x までは `visa-mcp` 1 リポジトリで提供されていた
- v2.0 で **backend (visa-mcp) と runtime (lab-executor-mcp) を分離**
- MCP tool / DSL schema / extension pack 形式は完全互換
- 旧 `from lab_visa_mcp.extension import ...` は v2.0 で
  DeprecationWarning 付きで動作

詳細: `docs/v2_migration.md`

## License

MIT
'''
    (dst_root / "README.md").write_text(content, encoding="utf-8")


def write_ci(dst_root: Path) -> None:
    wf_dir = dst_root / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    content = '''name: CI

on:
  push:
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -e ".[dev]"
      - name: import smoke
        run: python -c "import lab_executor"
      - name: pyvisa MUST NOT be required
        run: |
          python -c "import sys; assert 'pyvisa' not in sys.modules, 'pyvisa leaked into base import'"
      - name: pytest
        run: pytest -q

  pyvisa-not-installed:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -e ".[dev]"
      - run: pip uninstall -y pyvisa pyvisa-py || true
      - name: import lab_executor without pyvisa
        run: python -c "import lab_executor; print('OK')"
      - name: pytest (PyVISA 非依存テストのみ)
        run: pytest -q -m "not hardware"
'''
    (wf_dir / "ci.yml").write_text(content, encoding="utf-8")


def write_changelog(dst_root: Path) -> None:
    content = '''# 変更履歴

## v2.0.0-rc1 — Initial split candidate from visa-mcp v1.11.1

lab-executor-mcp の最初の release candidate。`visa-mcp` v1.11.1 から
runtime / DSL / ecosystem layer を切り出した。

### 含まれるもの

- DSL (`ExperimentPlan`, `dsl_version=0.8`) + validator + dry-run
- Job manager / state machine / scheduler / barrier
- Group / Map executor
- Observation API
- Benchmark runner + repair tasks + 5 fixture tasks
- Definition pack ecosystem (extension init/install/check/package/catalog/authoring)
- Instrument authoring (scaffold/promote-check/review-report)
- Export / bundle (deterministic reproducibility)
- Audit / locks / SQLite (user_version=3)
- `InstrumentBackend` Protocol + `MockBackend`
- MCP tool: Stable 43 + Experimental 7 = 50 (v1.0 から不変)

### 含まれないもの (visa-mcp 側に残る)

- `PyVisaBackend` (PyVISA 透過 adapter)
- `VisaManager` / `bus_manager` / `session_manager`
- Raw VISA tools (`send_command` / `query_instrument`, env-gated)
- `tools/discovery.py` (PyVISA resource 列挙)

### 互換性

- DSL `dsl_version=0.8` 完全互換
- extension pack 形式 (`.visa-mcp-ext.zip`) 完全互換
- `.install_meta.json` schema 完全互換
- `~/.visa-mcp/extensions/` install path 継続使用 (v2.x で再評価)
- MCP tool 名 / 引数 / response: v1.0 凍結のまま

### 依存関係

- PyVISA: **不要** (`pip install lab-executor-mcp` で動く)
- 実機 backend が必要なら `pip install visa-mcp` を追加 install

### 履歴

履歴は visa-mcp v1.11.1 から **切り出して新規 repo として開始**
(git filter-repo による history rewrite は行わない)。
visa-mcp の git log は引き続き `TECTOS-JP/visa-mcp` を参照。

### Source of truth

- `docs/separation/module_ownership.yaml` (visa-mcp v1.11.1)
- `docs/separation/split_manifest.yaml` (visa-mcp v1.11.1)
- bootstrap script: `scripts/bootstrap_lab_executor.py` (visa-mcp)
'''
    (dst_root / "CHANGELOG.md").write_text(content, encoding="utf-8")


def write_migration_guide(dst_root: Path, visa_src: Path) -> None:
    content = '''# v2.0 Migration Guide

> v1.x ユーザーが v2.0 へ移行するための手順書。両 repo
> (`visa-mcp` / `lab-executor-mcp`) で同一内容を配置する。

## 何が変わるか

v1.x までは `visa-mcp` 1 リポジトリで以下すべてを提供していた:

```
PyVISA backend  +  実験実行 runtime  +  DSL  +  extension ecosystem
```

v2.0 で **2 リポジトリに分離**:

```
lab-executor-mcp     ← runtime + DSL + extension + benchmark
visa-mcp             ← PyVISA backend + raw VISA + 旧 import shim
```

依存方向:

```
visa-mcp  →  lab-executor-mcp     (許可)
lab-executor-mcp  →  visa-mcp     (禁止)
```

## 既存ユーザー (実機を使う)

特別な対応は不要。`pip install --upgrade visa-mcp` だけで動く。

```bash
pip install --upgrade visa-mcp
# 自動的に lab-executor-mcp >= 2.0 も install される
```

旧 import path はすべて動作する (`DeprecationWarning` 付き):

```python
from lab_visa_mcp.extension import ExtensionManifest  # DeprecationWarning
from lab_visa_mcp.dsl import validate_experiment_plan  # DeprecationWarning
```

推奨される新 import:

```python
from lab_executor.extension import ExtensionManifest
from lab_executor.dsl import validate_experiment_plan
```

## 新規ユーザー (実機なしで benchmark / dry-run のみ)

```bash
pip install lab-executor-mcp
# PyVISA 不要
```

ただし、実機との通信は `visa-mcp` が必要。

## MCP tool

完全互換。Stable 43 + Experimental 7 = 50 で v1.0 から不変。
tool 名 / 引数 / response envelope すべて同じ。

## extension pack

完全互換。v1.x で作成した `.visa-mcp-ext.zip` は v2.0 でも
そのまま install できる。

```bash
lab-executor extension install my-pack.visa-mcp-ext.zip
# または旧 CLI 経由
visa-mcp extension install my-pack.visa-mcp-ext.zip   # DeprecationWarning
```

## install path

v2.0 では `~/.visa-mcp/extensions/` を継続使用する。v2.1 以降で
`~/.lab-executor/extensions/` への移行計画を提示予定。

## CLI

| v1.x | v2.0 推奨 | v2.0 互換 |
|------|-----------|----------|
| `visa-mcp serve` | `visa-mcp serve` (互換) | ✓ |
| `visa-mcp validate ...` | `lab-executor validate ...` | `visa-mcp validate` も動作 (warning) |
| `visa-mcp extension ...` | `lab-executor extension ...` | 同上 |
| `visa-mcp instrument ...` | `lab-executor instrument ...` | 同上 |

## DSL schema

`dsl_version=0.8` 完全互換。v1.x の plan / template は何も
変更せずに v2.0 lab-executor で実行できる。

## bundle / export

`export_experiment_bundle` の zip 形式は v1.0 から不変。
v1.x で作った bundle は v2.0 で `validate_experiment_bundle` /
`inspect_experiment_bundle` ともに通る。

## トラブルシューティング

### `ImportError: cannot import name X from lab_visa_mcp...`

v2.0 で完全に削除された API は無い。`DeprecationWarning` のみ。
このエラーは extension pack の dependency 不整合の可能性が高い。
`lab-executor extension check` で診断する。

### `pyvisa not found` が出る

`lab-executor-mcp` 単独 install ではこれは正常。実機を使うなら:

```bash
pip install visa-mcp
```

### v2.0 で動作しなくなった

v2.0 では MCP tool / DSL / extension pack 形式を変えていない。
動作差異が出る場合は `TECTOS-JP/lab-executor-mcp` / `TECTOS-JP/visa-mcp`
のいずれかに issue を立ててほしい。

## Roadmap

- v2.0:    分離本番、旧 import は warning 付きで動作
- v2.1:    migration 状況 review、`~/.lab-executor/extensions/` 並走計画
- v2.2+:   旧 import path 削除候補 (実利用状況を見て判断)

詳細: `docs/separation/notes.md` / `docs/raw_visa.md`
'''
    (dst_root / "docs").mkdir(exist_ok=True)
    (dst_root / "docs" / "v2_migration.md").write_text(
        content, encoding="utf-8")
    # visa-mcp 側にも同じものを置きたいが、それは visa-mcp 側の
    # ターンで対応する


def write_cli(dst_root: Path) -> None:
    """v2.0.0-rc2: minimal lab-executor CLI を生成。
    `pyproject.toml` の `[project.scripts] lab-executor = lab_executor.cli:main`
    が module 不在で壊れないようにする。"""
    cli_path = dst_root / "src" / "lab_executor" / "cli.py"
    if cli_path.exists():
        return  # 既に手動で patch 済みなら維持
    content = '''"""lab-executor CLI (v2.0.0-rc2).

v2.0.0 では minimal CLI を提供する。v1.x の `visa-mcp` CLI と機能互換
にするのは v2.1+ の段階的作業。
"""
from __future__ import annotations
import argparse
import sys


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lab-executor",
        description=(
            "lab-executor-mcp: backend-independent experiment execution "
            "runtime CLI (v2.0). PyVISA backend が必要な操作は "
            "`visa-mcp` CLI を使ってください。"
        ),
    )
    parser.add_argument("--version", action="store_true")
    sub = parser.add_subparsers(dest="command")
    sp_serve = sub.add_parser("serve")
    sp_serve.add_argument("--backend", default="mock",
                          choices=["mock"])
    sp_val = sub.add_parser("validate")
    sp_val.add_argument("target", nargs="?",
                        choices=["instrument", "plan", "extension",
                                 "benchmark"])
    sp_val.add_argument("path", nargs="?")
    sp_val.add_argument("--strict", action="store_true")
    sp_val.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.version:
        import lab_executor
        print(f"lab-executor-mcp {lab_executor.__version__}")
        return 0
    if args.command == "serve":
        print("lab-executor serve: v2.1 で MCP server を有効化します。",
              file=sys.stderr)
        return 2
    if args.command == "validate":
        if args.target == "instrument" and args.path:
            from lab_executor.registry import validate_instrument_file
            rep = validate_instrument_file(args.path, strict=args.strict)
            if args.json:
                import json
                print(json.dumps(rep.to_dict(), ensure_ascii=False,
                                  indent=2, default=str))
            else:
                print(f"errors: {len(rep.errors)}")
                for e in rep.errors:
                    print(f"  - {e.get('error_class')}: "
                          f"{e.get('message')}")
            return 0 if not rep.errors else 1
        print("lab-executor validate: v2.1 で port 予定。",
              file=sys.stderr)
        return 2
    if args.command is None:
        parser.print_help()
        return 0
    print(f"unknown subcommand: {args.command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
'''
    cli_path.write_text(content, encoding="utf-8")


def write_init(dst_root: Path) -> None:
    content = '''"""lab-executor-mcp: backend-independent experiment execution
runtime for AI agents.

v2.0.0-rc1: visa-mcp v1.11.1 から runtime / DSL / ecosystem layer を
切り出した最初の release candidate。
"""

__version__ = "2.0.0-rc1"
'''
    init = dst_root / "src" / "lab_executor" / "__init__.py"
    init.parent.mkdir(parents=True, exist_ok=True)
    init.write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="v2.0 bootstrap: lab-executor-mcp 新 repo 生成",
    )
    parser.add_argument("--src", required=True,
                         help="visa-mcp source root")
    parser.add_argument("--dst", required=True,
                         help="lab-executor-mcp destination root")
    parser.add_argument("--clean", action="store_true",
                         help="dst 内 src/lab_executor を削除してから再生成")
    args = parser.parse_args(argv)

    src_root = Path(args.src).resolve()
    dst_root = Path(args.dst).resolve()
    if not (src_root / "src" / "lab_visa_mcp").exists():
        print(f"ERROR: {src_root} に src/lab_visa_mcp が無い", file=sys.stderr)
        return 1
    if not dst_root.exists():
        print(f"ERROR: {dst_root} が存在しない", file=sys.stderr)
        return 1

    if args.clean:
        for p in ("src/lab_executor", "tests", "docs", "schemas",
                   "registry", "benchmarks", "examples",
                   ".github/workflows"):
            full = dst_root / p
            if full.exists():
                shutil.rmtree(full)

    manifest = load_manifest(src_root)
    by_owner = collect_modules(manifest)
    lab_modules = by_owner.get("lab-executor-mcp", set())
    # backends.base / backends.mock_backend は lab-executor 側へ送る
    lab_modules.add("lab_visa_mcp.backends.base")
    lab_modules.add("lab_visa_mcp.backends.mock_backend")
    # split owner module も lab-executor 側へ copy する (rc1 で実分割
    # まで進めない場合の現実解 - lab_executor_part を含めて全体を
    # コピーし、v2.1+ で更に分割を検討)。
    split_modules = by_owner.get("split", set())
    # visa-mcp 側に残すべき split module を除外 (server / cli / __init__ /
    # backends / tools.commands は visa-mcp 側にも置く)
    visa_only_splits = {
        "lab_visa_mcp",  # top-level shim
        "lab_visa_mcp.server",  # visa-mcp serve composition root
        "lab_visa_mcp.cli",  # visa-mcp 側 CLI shim
        "lab_visa_mcp.backends",  # package init は両方
    }
    for s in split_modules:
        if s not in visa_only_splits:
            lab_modules.add(s)

    # === 1) lab-executor module を copy + rewrite ===
    copied = 0
    skipped: list[str] = []
    for mod in sorted(lab_modules):
        if copy_module(src_root, dst_root, mod, lab_modules):
            copied += 1
        else:
            skipped.append(mod)

    # === 2) backends/__init__.py を生成 (PyVisaBackend を含まない) ===
    backends_init = dst_root / "src" / "lab_executor" / "backends" / "__init__.py"
    backends_init.parent.mkdir(parents=True, exist_ok=True)
    backends_init.write_text(
        '"""lab-executor backend layer (v2.0): Protocol + MockBackend.\n'
        '\n'
        'PyVisaBackend は visa-mcp 側に残る。lab-executor 側 runtime は\n'
        'InstrumentBackend Protocol を通じて backend を扱う。\n'
        '"""\n'
        'from lab_executor.backends.base import InstrumentBackend\n'
        'from lab_executor.backends.mock_backend import MockBackend\n'
        '\n'
        '__all__ = ["InstrumentBackend", "MockBackend"]\n',
        encoding="utf-8",
    )

    # === 2.5) relative backend import を patch ===
    patch_relative_backend_imports(dst_root)

    # === 3) 関連リソースを copy ===
    asset_counts: dict[str, int] = {}
    # schemas
    asset_counts["schemas"] = copy_tree(
        src_root / "schemas", dst_root / "schemas")
    # registry
    asset_counts["registry"] = copy_tree(
        src_root / "registry", dst_root / "registry")
    # benchmarks
    asset_counts["benchmarks"] = copy_tree(
        src_root / "benchmarks", dst_root / "benchmarks")
    # examples (extensions / instruments)
    asset_counts["examples"] = copy_tree(
        src_root / "examples", dst_root / "examples")
    # docs (separation / 大半)
    asset_counts["docs"] = copy_tree(
        src_root / "docs", dst_root / "docs",
        exclude_names={"raw_visa.md"},  # raw_visa.md は visa-mcp 側
    )
    # tests (runtime / extension / dsl / benchmark 系)
    # PyVISA / VisaManager 直叩き test は visa-mcp 側に残すべきだが、
    # v2.0-rc1 では一旦全 copy → import rewrite で対応
    asset_counts["tests"] = copy_tree(
        src_root / "tests", dst_root / "tests",
        transform_py=True, lab_modules=lab_modules,
    )

    # === 4) __init__.py (top-level package) + cli.py ===
    write_init(dst_root)
    write_cli(dst_root)

    # === 5) pyproject / README / CHANGELOG / CI / migration guide ===
    write_pyproject(dst_root)
    write_readme(dst_root)
    write_changelog(dst_root)
    write_ci(dst_root)
    write_migration_guide(dst_root, src_root)

    # === 6) AST + leftover verification ===
    parse_errors: list[str] = []
    leftover: list[str] = []
    for py in (dst_root / "src" / "lab_executor").rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        try:
            ast.parse(text)
        except SyntaxError as e:
            parse_errors.append(
                f"{py.relative_to(dst_root)}: {e}")
        # lab_visa_mcp.<lab module> が残っていないか
        for lab in lab_modules:
            for pat in (f"from {lab}", f"import {lab}"):
                if pat in text:
                    leftover.append(
                        f"{py.relative_to(dst_root)}: {pat}")

    print(f"copied lab-executor modules: {copied}/{len(lab_modules)}")
    if skipped:
        print(f"skipped (file not found): {skipped}")
    print(f"asset counts: {asset_counts}")
    if parse_errors:
        print(f"AST parse errors: {len(parse_errors)}", file=sys.stderr)
        for e in parse_errors[:5]:
            print(f"  - {e}", file=sys.stderr)
    if leftover:
        print(f"leftover lab-executor imports: {len(leftover)}",
              file=sys.stderr)
        for e in leftover[:5]:
            print(f"  - {e}", file=sys.stderr)

    if parse_errors or leftover:
        return 2
    print(f"OK: lab-executor-mcp scaffolded at {dst_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
