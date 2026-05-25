"""v1.11: Split Rehearsal (v2.0 git filter-repo 前の dry-run)

`module_ownership.yaml` と `split_manifest.yaml` を読み、tmp directory
に `lab_executor_candidate/` ツリーを生成する。本体 package には混ぜず、
release artifact にも含めない (テスト中に tmp で生成 → 検査 → 削除)。

実行例:

    python -m visa_mcp.dev.split_rehearsal --out tmp/lab_executor_candidate

確認項目 (テスト側で検証):
  1. candidate tree が import 可能 (但し import rewrite 後)
  2. candidate 内 module が `import visa_mcp` していない (rewrite 後)
  3. pyvisa 非依存で import できる

v1.11 では candidate は **正式 namespace ではない** (v2.0 で
`lab-executor-mcp` repo へ git filter-repo で移送する際に正式名に
書き換わる)。
"""
from __future__ import annotations
import argparse
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml


_THIS = Path(__file__).resolve()
REPO_ROOT = _THIS.parent.parent.parent.parent
SRC_ROOT = REPO_ROOT / "src" / "visa_mcp"
MANIFEST = REPO_ROOT / "docs" / "separation" / "module_ownership.yaml"
SPLIT_MANIFEST = REPO_ROOT / "docs" / "separation" / "split_manifest.yaml"

# import 文の rewrite ルール (lab-executor 候補 module 内のみ)
#   visa_mcp.<lab-executor module>   → lab_executor_candidate.<...>
#   visa_mcp.<backend / shared module> → そのまま (PyVisaBackend 等)
# v1.11 では「book-keeping のみ」: 実際に書き換えると tests が
# 二重に走るため、生成 candidate 内のみ rewrite する。
REWRITE_PREFIX_FROM = "visa_mcp."
REWRITE_PREFIX_TO = "lab_executor_candidate."


def _load_manifest() -> dict[str, Any]:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def _module_to_relpath(module: str) -> Path:
    """visa_mcp.foo.bar → visa_mcp/foo/bar.py (file が存在しなければ
    visa_mcp/foo/bar/__init__.py を返す)"""
    parts = module.split(".")
    file_path = SRC_ROOT.parent / Path(*parts).with_suffix(".py")
    if file_path.exists():
        return file_path
    init_path = SRC_ROOT.parent / Path(*parts) / "__init__.py"
    if init_path.exists():
        return init_path
    return file_path  # 存在しなくても返す (呼び出し側で skip)


def _classify_lab_executor_modules(
    manifest: dict[str, Any],
) -> set[str]:
    """lab-executor-mcp owner の module 名集合を返す
    (split / shared / visa-mcp は含まない)"""
    out: set[str] = set()
    for mod, info in (manifest.get("modules") or {}).items():
        owner = (info or {}).get("owner")
        if owner == "lab-executor-mcp":
            out.add(mod)
    return out


def _rewrite_import_text(src_text: str, lab_modules: set[str]) -> str:
    """source code 内の `visa_mcp.<lab module>` を
    `lab_executor_candidate.<lab module>` に置換。
    backend / shared / visa-mcp owner は維持。"""
    out_lines: list[str] = []
    pat = re.compile(
        r"\b(visa_mcp(?:\.[A-Za-z_][A-Za-z0-9_]*)+)\b"
    )
    for line in src_text.splitlines(keepends=True):

        def _sub(m):
            target = m.group(1)
            # `visa_mcp.foo.bar` のうち、prefix が lab-executor module
            # と一致するもの (or 子 module) を書き換える
            for lab in lab_modules:
                if target == lab or target.startswith(lab + "."):
                    return target.replace(
                        REWRITE_PREFIX_FROM, REWRITE_PREFIX_TO, 1,
                    )
            return target

        out_lines.append(pat.sub(_sub, line))
    return "".join(out_lines)


def generate_candidate(out_dir: Path) -> dict[str, Any]:
    """tmp directory に lab_executor_candidate/ ツリーを生成。
    Returns: summary dict (counts, skipped, etc.)"""
    out_dir = out_dir.resolve()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = _load_manifest()
    lab_modules = _classify_lab_executor_modules(manifest)
    copied: list[str] = []
    skipped: list[str] = []

    for mod in sorted(lab_modules):
        src_path = _module_to_relpath(mod)
        if not src_path.exists():
            skipped.append(mod)
            continue
        # `visa_mcp.foo.bar` → `lab_executor_candidate/foo/bar.py`
        rel_parts = mod.split(".")[1:]  # drop `visa_mcp`
        if src_path.name == "__init__.py":
            dst_path = out_dir.joinpath(*rel_parts, "__init__.py")
        else:
            dst_path = out_dir.joinpath(
                *rel_parts[:-1],
                rel_parts[-1] + ".py",
            )
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        text = src_path.read_text(encoding="utf-8")
        rewritten = _rewrite_import_text(text, lab_modules)
        dst_path.write_text(rewritten, encoding="utf-8")
        copied.append(mod)

    # ensure root __init__.py
    root_init = out_dir / "__init__.py"
    if not root_init.exists():
        root_init.write_text(
            '"""lab_executor_candidate (v1.11 split rehearsal, NOT public API)\n'
            '\n'
            'v2.0 で `lab-executor-mcp` 新 repo へ git filter-repo + path '
            'rename で移送される予定。v1.11 では tmp directory に生成し、'
            'import / pyvisa 非依存性を CI で検証するためにのみ存在する。\n'
            '"""\n',
            encoding="utf-8",
        )

    return {
        "out_dir": str(out_dir),
        "lab_executor_module_count": len(lab_modules),
        "copied_count": len(copied),
        "skipped_count": len(skipped),
        "skipped": skipped,
        "rewrite_rule": (
            f"{REWRITE_PREFIX_FROM}<lab-executor module> -> "
            f"{REWRITE_PREFIX_TO}<...>"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m visa_mcp.dev.split_rehearsal",
        description=(
            "v1.11: v2.0 git filter-repo 前の split rehearsal。"
            "lab-executor owner module を tmp/ に copy + import rewrite。"
        ),
    )
    parser.add_argument(
        "--out", default="tmp/lab_executor_candidate",
        help="生成先 directory (default: tmp/lab_executor_candidate)",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    summary = generate_candidate(Path(args.out))
    if args.json:
        import json as _json
        print(_json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"wrote: {summary['out_dir']}")
        print(f"  lab-executor modules: "
              f"{summary['lab_executor_module_count']}")
        print(f"  copied: {summary['copied_count']}")
        print(f"  skipped: {summary['skipped_count']}")
        if summary["skipped"]:
            for s in summary["skipped"]:
                print(f"    - {s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
