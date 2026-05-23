"""
v1.8: visa-mcp CLI

Usage:
  visa-mcp validate instrument <path>
  visa-mcp validate system <path>
  visa-mcp validate plan <path>
  visa-mcp validate benchmark <path>
  visa-mcp validate registry <path>
  visa-mcp validate schemas
  visa-mcp validate extension <path-to-extension.yaml> [--strict]   # v1.2 / strict v1.4
  visa-mcp extension install <path-to-extension.yaml>      # v1.3
  visa-mcp extension install <path-to-pack.visa-mcp-ext.zip>  # v1.6
  visa-mcp extension list [--json]                         # v1.3
  visa-mcp extension uninstall <extension_id> [--dry-run]  # v1.3 / dry-run v1.4
  visa-mcp extension validate-installed [--json]           # v1.3
  visa-mcp extension check [<extension_id>] [--strict]     # v1.4
  visa-mcp extension inspect <extension_id> [--json]       # v1.4
  visa-mcp extension package <extension.yaml>              # v1.5
      [--output <dir>] [--strict] [--json]
  visa-mcp extension verify-package <zip-path> [--json]    # v1.5
  visa-mcp extension catalog [--installed | --packages <dir>]  # v1.6
  visa-mcp extension inspect-package <zip-path> [--json]   # v1.6
  visa-mcp extension init <pack_name>                      # v1.7
      [--id <ext-id>] [--template <name>] [--author <name>] [--force]
  visa-mcp extension package <ext.yaml> --dry-run [--json] # v1.7
  visa-mcp extension doctor <ext.yaml> [--strict] [--json] # v1.7
  visa-mcp instrument scaffold <category> --output <file>  # v1.8
      [--manufacturer "..."] [--model "..."] [--force]
  visa-mcp extension add-instrument <pack_dir>             # v1.8
      --id <id> --category <category>
      [--manufacturer "..."] [--model "..."] [--dry-run] [--force]
  visa-mcp registry overlay [--source builtin|extension]   # v1.4
  visa-mcp serve

各 subcommand は --json で機械可読出力を返す (CI / 自動化向け)。
"""
from __future__ import annotations
import argparse
from pathlib import Path
import json
import sys
from pathlib import Path
from typing import Any


def _fmt_human(rep: dict[str, Any]) -> str:
    lines = []
    icon = {"ok": "[OK]", "warning": "[WARN]", "error": "[ERR]"}.get(
        rep.get("status", ""), "[?]")
    lines.append(f"{icon} {rep.get('file', '?')}")
    if rep.get("schema"):
        lines.append(f"  schema: {rep['schema']}")
    for e in rep.get("errors") or []:
        lines.append(
            f"  ERROR  {e.get('error_class', 'error')}: {e.get('message', '')}"
        )
    for w in rep.get("warnings") or []:
        lines.append(
            f"  WARN   {w.get('warning_class', 'warning')}: {w.get('message', '')}"
        )
    return "\n".join(lines)


def _emit(reports: list[dict[str, Any]], as_json: bool) -> int:
    if as_json:
        print(json.dumps(
            {"reports": reports}, ensure_ascii=False, indent=2, default=str,
        ))
    else:
        for r in reports:
            print(_fmt_human(r))
    # 終了コード: error 1件でもあれば 1、warning のみは 0
    for r in reports:
        if r.get("status") == "error":
            return 1
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    from visa_mcp import registry as reg

    target = args.target
    path = Path(args.path) if args.path else None

    if target == "instrument":
        rep = reg.validate_instrument_file(path).to_dict()
        return _emit([rep], args.json)
    if target == "system":
        rep = reg.validate_system_config_file(path).to_dict()
        return _emit([rep], args.json)
    if target == "plan":
        rep = reg.validate_plan_file(path).to_dict()
        return _emit([rep], args.json)
    if target == "benchmark":
        rep = reg.validate_benchmark_task_file(path).to_dict()
        return _emit([rep], args.json)
    if target == "registry":
        reps = [r.to_dict() for r in reg.validate_registry(path)]
        return _emit(reps, args.json)
    if target == "extension":
        # v1.2: extension manifest (definition pack) validation
        # v1.4: --strict 対応
        from visa_mcp.extension import validate_extension_file
        strict = bool(getattr(args, "strict", False))
        rep = validate_extension_file(path, strict=strict).to_dict()
        return _emit([rep], args.json)
    if target == "schemas":
        # schemas/*.schema.json がすべて pretty-printed + preview metadata を
        # 持っているか確認
        from visa_mcp.registry import ValidationReport
        schemas_dir = (Path(args.path) if args.path
                       else Path(__file__).parent.parent.parent / "schemas")
        reps: list[dict[str, Any]] = []
        for p in sorted(schemas_dir.glob("*.schema.json")):
            rep = ValidationReport(file=str(p), schema=p.name)
            try:
                text = p.read_text(encoding="utf-8")
                if "\r" in text:
                    rep.warnings.append({
                        "warning_class": "schema_has_cr",
                        "message": "CR characters found (expect LF-only)",
                    })
                if "\n" not in text:
                    rep.warnings.append({
                        "warning_class": "schema_single_line",
                        "message": (
                            "schema が 1 行に潰れています。pretty-print されて "
                            "いるか確認してください"
                        ),
                    })
                data = json.loads(text)
                if data.get("x-visa-mcp-status") not in (
                    "preview", "stable",
                ):
                    rep.warnings.append({
                        "warning_class": "missing_preview_metadata",
                        "message": (
                            "x-visa-mcp-status (preview/stable) が無い"
                        ),
                    })
            except Exception as e:
                rep.errors.append({
                    "error_class": "schema_invalid",
                    "message": str(e),
                })
                rep.status = "error"
            if rep.errors:
                rep.status = "error"
            elif rep.warnings:
                rep.status = "warning"
            reps.append(rep.to_dict())
        return _emit(reps, args.json)

    print(f"unknown target: {target}", file=sys.stderr)
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="visa-mcp",
        description="visa-mcp utility CLI (validate / lint)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    val = sub.add_parser("validate",
                          help="validate instrument / DSL plan / benchmark task / registry")
    val.add_argument(
        "target",
        choices=["instrument", "system", "plan", "benchmark", "registry",
                 "schemas", "extension"],
        help="検証対象",
    )
    val.add_argument(
        "path", nargs="?",
        help="ファイル / ディレクトリ path (schemas 時は省略可)",
    )
    val.add_argument(
        "--json", action="store_true", help="JSON 出力 (CI 向け)",
    )
    val.add_argument(
        "--strict", action="store_true",
        help="(v1.4) strict mode: warning を error 化 (registry 掲載検査向け)",
    )
    val.set_defaults(func=cmd_validate)

    serve = sub.add_parser("serve", help="MCP server を起動 (default)")
    serve.set_defaults(func=cmd_serve)

    # v1.3: extension management
    ext = sub.add_parser(
        "extension",
        help="(v1.3) definition pack install / list / uninstall",
    )
    ext_sub = ext.add_subparsers(dest="ext_command", required=True)

    ext_install = ext_sub.add_parser(
        "install",
        help=(
            "extension.yaml または .visa-mcp-ext.zip を local user 領域へ "
            "install (v1.6 で zip にも対応)"
        ),
    )
    ext_install.add_argument(
        "path",
        help=(
            "extension.yaml の path、または "
            ".visa-mcp-ext.zip / *.zip の path (v1.6+)"
        ),
    )
    ext_install.add_argument(
        "--force", action="store_true",
        help="同 extension_id が既存でも上書き install",
    )
    ext_install.add_argument("--json", action="store_true",
                              help="JSON 出力 (CI 向け)")
    ext_install.set_defaults(func=cmd_extension)

    ext_list = ext_sub.add_parser("list", help="installed extensions 一覧")
    ext_list.add_argument("--json", action="store_true")
    ext_list.set_defaults(func=cmd_extension)

    ext_un = ext_sub.add_parser("uninstall", help="extension を取り除く")
    ext_un.add_argument("extension_id", help="extension_id を指定")
    ext_un.add_argument("--json", action="store_true")
    ext_un.add_argument(
        "--dry-run", action="store_true",
        help="(v1.4) 削除せず、削除対象の path / overlay id を表示",
    )
    ext_un.set_defaults(func=cmd_extension)

    ext_val = ext_sub.add_parser(
        "validate-installed",
        help="built-in registry + installed extensions の overlay 整合検証",
    )
    ext_val.add_argument("--json", action="store_true")
    ext_val.set_defaults(func=cmd_extension)

    # v1.4: integrity check
    ext_chk = ext_sub.add_parser(
        "check",
        help="(v1.4) installed extension の integrity (sha256 drift) を検査",
    )
    ext_chk.add_argument(
        "extension_id", nargs="?",
        help="特定 extension のみ。省略時は全 installed を検査",
    )
    ext_chk.add_argument(
        "--strict", action="store_true",
        help="warning を error に格上げ",
    )
    ext_chk.add_argument("--json", action="store_true")
    ext_chk.set_defaults(func=cmd_extension)

    # v1.4: inspect
    ext_ins = ext_sub.add_parser(
        "inspect",
        help="(v1.4) installed extension の詳細を表示",
    )
    ext_ins.add_argument("extension_id")
    ext_ins.add_argument("--json", action="store_true")
    ext_ins.set_defaults(func=cmd_extension)

    # v1.5: package
    ext_pkg = ext_sub.add_parser(
        "package",
        help="(v1.5) definition pack を配布可能 zip にまとめる",
        description=(
            "definition pack を <extension_id>-<version>.visa-mcp-ext.zip "
            "にまとめる。zip 内に package_manifest.json と "
            "checksums.sha256 を生成し、配布側で verify-package による "
            "整合性検証を可能にする。"
        ),
        epilog=(
            "例:\n"
            "  visa-mcp extension package ./mypack/extension.yaml\n"
            "  visa-mcp extension package ./mypack/extension.yaml "
            "--output dist/\n"
            "  visa-mcp extension package ./mypack/extension.yaml "
            "--strict --json\n\n"
            "strict mode:\n"
            "  - support_level=verified で validation_evidence が空 "
            "→ error\n"
            "  - pack に README.md が無い → error\n"
            "  - registry_entries の id/path/vendor/model/category/"
            "support_level 必須\n"
            "  - registry_entries.path が pack 外 → error\n"
            "  - registry support_level と instrument metadata "
            "support_level が不一致 → error"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ext_pkg.add_argument("path", help="extension.yaml の path")
    ext_pkg.add_argument(
        "--output", default=None,
        help="出力 directory (default: <pack_dir>/dist)",
    )
    ext_pkg.add_argument(
        "--strict", action="store_true",
        help=(
            "strict validation を通してから package 化 (registry 掲載 / CI / "
            "release 前検査向け)"
        ),
    )
    ext_pkg.add_argument(
        "--json", action="store_true", help="JSON 出力 (CI 向け)",
    )
    ext_pkg.add_argument(
        "--dry-run", action="store_true",
        help=(
            "(v1.7) zip を作らず、含まれる予定 file / manifest preview "
            "/ checksums 数を表示"
        ),
    )
    ext_pkg.set_defaults(func=cmd_extension)

    # v1.6: catalog (installed / package directory)
    ext_cat = ext_sub.add_parser(
        "catalog",
        help=(
            "(v1.6) installed pack または package directory を catalog "
            "形式で一覧化 (選定 / 比較用)"
        ),
    )
    grp = ext_cat.add_mutually_exclusive_group()
    grp.add_argument(
        "--installed", action="store_true",
        help="installed pack を catalog 化 (default)",
    )
    grp.add_argument(
        "--packages", default=None, metavar="DIR",
        help="指定 directory 配下の .visa-mcp-ext.zip を catalog 化",
    )
    ext_cat.add_argument("--json", action="store_true")
    ext_cat.set_defaults(func=cmd_extension)

    # v1.6: inspect-package (install せずに zip 中身を読む)
    ext_ip = ext_sub.add_parser(
        "inspect-package",
        help=(
            "(v1.6) zip package を install せずに catalog / contents / "
            "quality_signals を表示"
        ),
    )
    ext_ip.add_argument("zip_path", help="検査対象の .visa-mcp-ext.zip")
    ext_ip.add_argument("--json", action="store_true")
    ext_ip.set_defaults(func=cmd_extension)

    # v1.5: verify-package
    ext_vp = ext_sub.add_parser(
        "verify-package",
        help="(v1.5) package zip の整合性を検証",
        description=(
            "package zip の整合性を検証する。zip slip / 絶対 path / "
            "checksum mismatch / executable_code=true / "
            "extension.yaml re-validation を全て通れば status=ok。"
        ),
        epilog=(
            "例:\n"
            "  visa-mcp extension verify-package "
            "dist/tectos.mock.basic-0.1.0.visa-mcp-ext.zip\n"
            "  visa-mcp extension verify-package dist/xxx.zip --json\n\n"
            "検査項目:\n"
            "  - zip として読める\n"
            "  - すべての member が zip slip safe\n"
            "  - extension.yaml / package_manifest.json / "
            "checksums.sha256 必須\n"
            "  - package_manifest.executable_code=true を error 化\n"
            "  - checksums.sha256 と zip 内 sha256 を照合\n"
            "  - package_manifest.files[*].sha256 と実 file を照合\n"
            "  - tmp 展開して validate_extension_file を再実行"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ext_vp.add_argument(
        "zip_path", help="検証対象の .visa-mcp-ext.zip path",
    )
    ext_vp.add_argument(
        "--json", action="store_true", help="JSON 出力 (CI 向け)",
    )
    ext_vp.set_defaults(func=cmd_extension)

    # v1.7: init (scaffold a new pack)
    ext_init = ext_sub.add_parser(
        "init",
        help="(v1.7) 空の definition pack を scaffold する",
    )
    ext_init.add_argument("pack_name", help="生成する directory 名")
    ext_init.add_argument(
        "--target-dir", default=None,
        help="親ディレクトリ (default: cwd)",
    )
    ext_init.add_argument(
        "--id", dest="extension_id", default=None,
        help="reverse-DNS extension_id (default: local.<pack_name>)",
    )
    ext_init.add_argument(
        "--template", default="minimal",
        choices=["minimal", "mock_basic", "instrument_pack"],
        help="scaffold template (default: minimal)",
    )
    ext_init.add_argument(
        "--author", default="",
        help="catalog.authors / author に入れる名前",
    )
    ext_init.add_argument(
        "--force", action="store_true",
        help="既存 directory があっても上書き",
    )
    ext_init.add_argument("--json", action="store_true")
    ext_init.set_defaults(func=cmd_extension)

    # v1.7: doctor (authoring 向けまとめ確認)
    ext_doc = ext_sub.add_parser(
        "doctor",
        help=(
            "(v1.7) validate + strict + package dry-run + catalog "
            "/ README / license / verified evidence を一括確認"
        ),
    )
    ext_doc.add_argument("path", help="extension.yaml の path")
    ext_doc.add_argument(
        "--strict", action="store_true",
        help="strict-only error も本体 errors に格上げ",
    )
    ext_doc.add_argument("--json", action="store_true")
    ext_doc.set_defaults(func=cmd_extension)

    # v1.8: extension add-instrument
    ext_ai = ext_sub.add_parser(
        "add-instrument",
        help=(
            "(v1.8) pack に instrument YAML + registry entry を追加"
        ),
    )
    ext_ai.add_argument("pack_dir", help="extension.yaml を含む pack dir")
    ext_ai.add_argument(
        "--id", dest="instrument_id", required=True,
        help="registry id (小文字英数 + _ / -)",
    )
    ext_ai.add_argument(
        "--category", required=True,
        choices=["power_supply", "dmm", "temperature_meter",
                  "generic_scpi"],
        help="instrument scaffold category",
    )
    ext_ai.add_argument("--manufacturer", default="TODO")
    ext_ai.add_argument("--model", default="TODO")
    ext_ai.add_argument(
        "--dry-run", action="store_true",
        help="変更予定だけ表示して file は触らない",
    )
    ext_ai.add_argument(
        "--force", action="store_true",
        help=(
            "既存 instrument file を上書き (registry id 重複は force "
            "でも拒否)"
        ),
    )
    ext_ai.add_argument("--json", action="store_true")
    ext_ai.set_defaults(func=cmd_extension)

    # v1.8: instrument scaffold (top-level instrument subcommand)
    inst = sub.add_parser(
        "instrument",
        help="(v1.8) instrument definition authoring",
    )
    inst_sub = inst.add_subparsers(dest="inst_command", required=True)
    inst_sc = inst_sub.add_parser(
        "scaffold",
        help=(
            "(v1.8) category 別 template から instrument YAML を生成 "
            "(support_level: draft 固定)"
        ),
    )
    inst_sc.add_argument(
        "category",
        choices=["power_supply", "dmm", "temperature_meter",
                  "generic_scpi"],
    )
    inst_sc.add_argument(
        "--output", required=True,
        help="生成先 file path (e.g. instruments/kikusui_pmx.yaml)",
    )
    inst_sc.add_argument("--manufacturer", default="TODO")
    inst_sc.add_argument("--model", default="TODO")
    inst_sc.add_argument(
        "--force", action="store_true",
        help="既存 file を上書き",
    )
    inst_sc.add_argument("--json", action="store_true")
    inst_sc.set_defaults(func=cmd_instrument)

    # v1.4: registry overlay
    reg = sub.add_parser(
        "registry",
        help="(v1.4) registry の表示 / overlay 検査",
    )
    reg_sub = reg.add_subparsers(dest="reg_command", required=True)
    reg_ov = reg_sub.add_parser(
        "overlay",
        help="built-in + installed extension の overlay registry を表示",
    )
    reg_ov.add_argument(
        "--source", choices=["builtin", "extension"], default=None,
        help="表示を一方の source に絞る",
    )
    reg_ov.add_argument("--json", action="store_true")
    reg_ov.set_defaults(func=cmd_registry)

    return parser


def cmd_serve(args: argparse.Namespace) -> int:
    from visa_mcp.server import main as server_main
    server_main()
    return 0


def cmd_extension(args: argparse.Namespace) -> int:
    """v1.3: extension install / list / uninstall / validate-installed"""
    from visa_mcp.extension_install import (
        install_definition_pack, list_installed_packs,
        uninstall_definition_pack, load_overlay_registry,
    )
    sub = args.ext_command

    if sub == "install":
        # v1.6: zip path も受け付ける (.visa-mcp-ext.zip / .zip)
        src = Path(args.path)
        is_zip = src.suffix.lower() == ".zip" or src.name.endswith(
            ".visa-mcp-ext.zip"
        )
        if is_zip:
            from visa_mcp.extension_install import (
                install_definition_pack_from_zip,
            )
            res = install_definition_pack_from_zip(
                args.path, force=args.force,
            )
            schema_name = "extension_install_zip (v1.6)"
        else:
            res = install_definition_pack(args.path, force=args.force)
            schema_name = "extension_install (v1.3)"
        data = res.to_dict()
        return _emit_extension({
            "status": data["status"],
            "file": str(args.path),
            "schema": schema_name,
            "errors": data["errors"],
            "warnings": data["warnings"],
            "extension_id": data["extension_id"],
            "version": data["version"],
            "install_path": data["install_path"],
        }, args.json)

    if sub == "list":
        packs = list_installed_packs()
        if args.json:
            print(json.dumps(
                {"installed_extensions": packs}, ensure_ascii=False,
                indent=2, default=str,
            ))
        else:
            if not packs:
                print("(no installed extensions)")
            else:
                for p in packs:
                    print(
                        f"  {p.get('extension_id')} "
                        f"v{p.get('version')}  →  {p.get('path')}"
                    )
        return 0

    if sub == "uninstall":
        if getattr(args, "dry_run", False):
            from visa_mcp.extension_integrity import uninstall_dry_run
            data = uninstall_dry_run(args.extension_id)
            if args.json:
                print(json.dumps({"reports": [data]},
                                  ensure_ascii=False, indent=2, default=str))
            else:
                if data.get("status") == "error":
                    for e in data.get("errors", []):
                        print(f"[ERR]  {e.get('error_class')}: "
                              f"{e.get('message')}")
                    return 1
                print(f"[DRY]  uninstall {data['extension_id']}")
                print(f"  would remove path : {data['would_remove_path']}")
                print(f"  file count        : "
                      f"{data['would_remove_file_count']}")
                if data["would_remove_overlay_ids"]:
                    print(f"  overlay ids       : "
                          f"{data['would_remove_overlay_ids']}")
            return 0 if data.get("status") != "error" else 1

        res = uninstall_definition_pack(args.extension_id)
        return _emit_extension({
            "status": res.get("status", "error"),
            "file": args.extension_id,
            "schema": "extension_uninstall (v1.3)",
            "errors": res.get("errors", []),
            "warnings": [],
            "extension_id": args.extension_id,
            "removed_path": res.get("removed_path"),
        }, args.json)

    if sub == "validate-installed":
        # built-in registry も同時に overlay 統合
        builtin = (Path(__file__).parent.parent.parent / "registry"
                   / "INDEX.yaml")
        rep = load_overlay_registry(builtin if builtin.exists() else None)
        if args.json:
            print(json.dumps(
                {"overlay_registry": rep.to_dict()},
                ensure_ascii=False, indent=2, default=str,
            ))
        else:
            icon = {"ok": "[OK]", "warning": "[WARN]",
                    "error": "[ERR]"}.get(rep.status, "[?]")
            print(f"{icon} overlay registry  status={rep.status}  "
                  f"entries={len(rep.entries)}")
            for e in rep.errors:
                print(f"  ERROR  {e.get('error_class')}: {e.get('message')}")
            for w in rep.warnings:
                print(f"  WARN   {w.get('warning_class')}: "
                      f"{w.get('message')}")
        return 0 if rep.status != "error" else 1

    if sub == "check":
        from visa_mcp.extension_integrity import (
            check_installed_extension, check_all_installed_extensions,
        )
        strict = bool(getattr(args, "strict", False))
        if args.extension_id:
            reps = [check_installed_extension(args.extension_id,
                                              strict=strict)]
        else:
            reps = check_all_installed_extensions(strict=strict)
        reports = [r.to_dict() for r in reps]
        if args.json:
            print(json.dumps({"reports": reports},
                              ensure_ascii=False, indent=2, default=str))
        else:
            if not reports:
                print("(no installed extensions)")
            for r in reports:
                icon = {"ok": "[OK]", "warning": "[WARN]",
                        "error": "[ERR]"}.get(r["status"], "[?]")
                print(f"{icon} {r['extension_id']} v{r['version']}  "
                      f"integrity={r['integrity']}  "
                      f"files={r['files_checked']}")
                for e in r["errors"]:
                    print(f"  ERROR  {e.get('error_class')}: "
                          f"{e.get('message')}")
                for w in r["warnings"]:
                    print(f"  WARN   {w.get('warning_class')}: "
                          f"{w.get('message')}")
                for a in r["recommended_actions"]:
                    print(f"  fix?   {a['action']}: {a['command']}")
        return 0 if all(r["status"] != "error" for r in reports) else 1

    if sub == "inspect":
        from visa_mcp.extension_integrity import inspect_installed_extension
        rep = inspect_installed_extension(args.extension_id).to_dict()
        if args.json:
            print(json.dumps({"report": rep},
                              ensure_ascii=False, indent=2, default=str))
        else:
            print(f"extension_id   : {rep['extension_id']}")
            print(f"version        : {rep['version']}")
            print(f"installed_at   : {rep['installed_at']}")
            print(f"source_path    : {rep['source_path']}")
            print(f"visa_mcp_ver   : {rep['visa_mcp_version']}")
            print(f"install_path   : {rep['install_path']}")
            print(f"integrity      : {rep['integrity']}")
            print(f"contents       : {rep['contents_summary']}")
            if rep["registry_entry_ids"]:
                print(f"registry ids   : {rep['registry_entry_ids']}")
            for w in rep["warnings"]:
                print(f"  WARN  {w.get('warning_class')}: "
                      f"{w.get('message')}")
        return 0 if rep["integrity"] != "invalid" else 1

    if sub == "catalog":
        from visa_mcp.extension_catalog import (
            list_catalog_installed, list_catalog_packages,
        )
        if args.packages:
            rep = list_catalog_packages(args.packages)
        else:
            rep = list_catalog_installed()
        data = rep.to_dict()
        if args.json:
            print(json.dumps({"catalog": data}, ensure_ascii=False,
                              indent=2, default=str))
        else:
            icon = {"ok": "[OK]", "warning": "[WARN]",
                    "error": "[ERR]"}.get(data["status"], "[?]")
            print(f"{icon} catalog  count={data['count']}")
            for e in data["extensions"]:
                sl = e.get("support_level_summary") or {}
                qs = e.get("quality_signals") or {}
                print(f"  - {e['extension_id']} v{e['version']}")
                summ = (e.get("catalog") or {}).get("summary", "")
                if summ:
                    print(f"      summary: {summ}")
                print(f"      support_level: "
                      f"verified={sl.get('verified', 0)} "
                      f"tested={sl.get('tested', 0)} "
                      f"experimental={sl.get('experimental', 0)} "
                      f"draft={sl.get('draft', 0)}")
                print(f"      signals: readme={qs.get('has_readme')} "
                      f"license={qs.get('has_catalog_license')} "
                      f"evidence={qs.get('has_validation_evidence')}")
            for e in data["errors"]:
                print(f"  ERROR  {e.get('error_class')}: "
                      f"{e.get('message')}")
            for w in data["warnings"]:
                print(f"  WARN   {w.get('warning_class')}: "
                      f"{w.get('message')}")
        return 0 if data["status"] != "error" else 1

    if sub == "inspect-package":
        from visa_mcp.extension_catalog import inspect_package
        data = inspect_package(args.zip_path)
        if args.json:
            print(json.dumps({"inspect_package": data},
                              ensure_ascii=False, indent=2, default=str))
        else:
            if data["status"] == "error":
                print(f"[ERR] {args.zip_path}")
                for e in data["errors"]:
                    print(f"  ERROR  {e.get('error_class')}: "
                          f"{e.get('message')}")
                return 1
            e = data["entry"]
            sl = e.get("support_level_summary") or {}
            qs = e.get("quality_signals") or {}
            print(f"[OK] {e['extension_id']} v{e['version']}")
            cat = e.get("catalog") or {}
            if cat.get("summary"):
                print(f"  summary    : {cat['summary']}")
            if cat.get("license"):
                print(f"  license    : {cat['license']}")
            if cat.get("tags"):
                print(f"  tags       : {cat['tags']}")
            print(f"  contents   : {e.get('contents_summary')}")
            print(f"  support_lvl: "
                  f"verified={sl.get('verified', 0)} "
                  f"tested={sl.get('tested', 0)} "
                  f"experimental={sl.get('experimental', 0)} "
                  f"draft={sl.get('draft', 0)}")
            print(f"  signals    : readme={qs.get('has_readme')} "
                  f"evidence={qs.get('has_validation_evidence')}")
            for w in data.get("warnings", []):
                print(f"  WARN   {w.get('warning_class')}: "
                      f"{w.get('message')}")
        return 0

    if sub == "package":
        # v1.7: --dry-run は zip を作らず内容 preview のみ返す
        if getattr(args, "dry_run", False):
            from visa_mcp.extension_authoring import package_dry_run
            data = package_dry_run(args.path, strict=args.strict)
            if args.json:
                print(json.dumps({"package_dry_run": data},
                                  ensure_ascii=False, indent=2,
                                  default=str))
            else:
                icon = {"ok": "[OK]", "warning": "[WARN]",
                        "error": "[ERR]"}.get(data["status"], "[?]")
                print(f"{icon} DRY-RUN package "
                      f"{data['extension_id']} v{data['version']}")
                print(f"  package_name  : {data['package_name']}")
                print(f"  file_count    : {data['checksums_preview_count']}")
                print(f"  files included:")
                for fn in data["files_included"][:20]:
                    print(f"    + {fn}")
                if len(data["files_included"]) > 20:
                    print(f"    ... (+{len(data['files_included']) - 20})")
                if data["files_excluded"]:
                    print(f"  files excluded:")
                    for fn in data["files_excluded"][:10]:
                        print(f"    - {fn}")
                for e in data["errors"]:
                    print(f"  ERROR  {e.get('error_class')}: "
                          f"{e.get('message')}")
                for w in data["warnings"]:
                    print(f"  WARN   {w.get('warning_class')}: "
                          f"{w.get('message')}")
            return 0 if data["status"] != "error" else 1

        from visa_mcp.extension_packaging import package_definition_pack
        res = package_definition_pack(
            args.path, output_dir=args.output, strict=args.strict,
        )
        data = res.to_dict()
        if args.json:
            print(json.dumps({"package": data},
                              ensure_ascii=False, indent=2, default=str))
        else:
            icon = "[OK]" if data["status"] == "ok" else "[ERR]"
            print(f"{icon} package {data['extension_id']} "
                  f"v{data['version']}")
            if data["status"] == "ok":
                print(f"  path        : {data['package_path']}")
                print(f"  file count  : {data['file_count']}")
                print(f"  sha256      : {data['package_sha256']}")
            for e in data["errors"]:
                print(f"  ERROR  {e.get('error_class')}: "
                      f"{e.get('message')}")
            for w in data["warnings"]:
                print(f"  WARN   {w.get('warning_class')}: "
                      f"{w.get('message')}")
        return 0 if data["status"] == "ok" else 1

    if sub == "verify-package":
        from visa_mcp.extension_packaging import verify_extension_package
        res = verify_extension_package(args.zip_path)
        data = res.to_dict()
        if args.json:
            print(json.dumps({"verify": data},
                              ensure_ascii=False, indent=2, default=str))
        else:
            icon = {"ok": "[OK]", "warning": "[WARN]",
                    "error": "[ERR]"}.get(data["status"], "[?]")
            print(f"{icon} verify {data['extension_id']} "
                  f"v{data['version']}  files={data['file_count']}")
            for e in data["errors"]:
                print(f"  ERROR  {e.get('error_class')}: "
                      f"{e.get('message')}")
            for w in data["warnings"]:
                print(f"  WARN   {w.get('warning_class')}: "
                      f"{w.get('message')}")
        return 0 if data["status"] != "error" else 1

    if sub == "init":
        from visa_mcp.extension_authoring import init_extension_pack
        res = init_extension_pack(
            args.pack_name,
            target_dir=args.target_dir,
            extension_id=args.extension_id,
            template=args.template,
            author=args.author,
            force=args.force,
        )
        data = res.to_dict()
        if args.json:
            print(json.dumps({"init": data}, ensure_ascii=False,
                              indent=2, default=str))
        else:
            icon = "[OK]" if data["status"] == "ok" else "[ERR]"
            print(f"{icon} init {data['extension_id']} -> "
                  f"{data['pack_path']}")
            for fn in data["files_created"]:
                print(f"  + {fn}")
            for e in data["errors"]:
                print(f"  ERROR  {e.get('error_class')}: "
                      f"{e.get('message')}")
            if data["status"] == "ok":
                print("  next: visa-mcp extension doctor "
                      f"{Path(data['pack_path']) / 'extension.yaml'}")
        return 0 if data["status"] == "ok" else 1

    if sub == "doctor":
        from visa_mcp.extension_authoring import doctor_extension
        rep = doctor_extension(args.path, strict=args.strict)
        data = rep.to_dict()
        if args.json:
            print(json.dumps({"doctor": data}, ensure_ascii=False,
                              indent=2, default=str))
        else:
            icon = {"ok": "[OK]", "warning": "[WARN]",
                    "error": "[ERR]"}.get(data["status"], "[?]")
            s = data["summary"]
            print(f"{icon} doctor {data['extension_id']}  "
                  f"errors={s['errors']} warnings={s['warnings']}  "
                  f"ready_to_package={s['ready_to_package']}  "
                  f"ready_for_registry_review="
                  f"{s['ready_for_registry_review']}")
            # v1.7.1 P1: 3 グループに分類表示
            #   Errors / Warnings (品質) / Strict-only issues
            #   (--strict が無い場合の strict_would_fail を抜き出す)
            print("  Errors (block package):")
            if data["errors"]:
                for e in data["errors"]:
                    print(f"    ERROR  [{e.get('stage')}] "
                          f"{e.get('error_class')}: "
                          f"{e.get('message')}")
            else:
                print("    (none)")
            strict_warns = [
                w for w in data["warnings"]
                if w.get("warning_class") == "strict_would_fail"
            ]
            normal_warns = [
                w for w in data["warnings"]
                if w.get("warning_class") != "strict_would_fail"
            ]
            print("  Warnings (quality):")
            if normal_warns:
                for w in normal_warns:
                    print(f"    WARN   [{w.get('stage')}] "
                          f"{w.get('warning_class')}: "
                          f"{w.get('message')}")
            else:
                print("    (none)")
            print("  Strict-only issues (must fix before "
                  "registry / publishing):")
            if strict_warns:
                for w in strict_warns:
                    inner = w.get("error_class", "")
                    print(f"    STRICT [{w.get('stage')}] "
                          f"{inner}: {w.get('message')}")
            else:
                print("    (none)")
            if data["recommended_actions"]:
                print("  Recommended actions:")
                for a in data["recommended_actions"]:
                    print(f"    fix?   {a['action']}: {a['reason']}")
        return 0 if data["status"] != "error" else 1

    if sub == "add-instrument":
        from visa_mcp.instrument_authoring import add_instrument_to_pack
        res = add_instrument_to_pack(
            args.pack_dir,
            instrument_id=args.instrument_id,
            category=args.category,
            manufacturer=args.manufacturer,
            model=args.model,
            dry_run=args.dry_run,
            force=args.force,
        )
        data = res.to_dict()
        if args.json:
            print(json.dumps({"add_instrument": data},
                              ensure_ascii=False, indent=2, default=str))
        else:
            icon = "[OK]" if data["status"] == "ok" else "[ERR]"
            mark = "DRY-RUN " if data["dry_run"] else ""
            print(f"{icon} {mark}add-instrument {args.instrument_id} "
                  f"({args.category}) -> {args.pack_dir}")
            if data["instrument_file"]:
                print(f"  instrument_file : {data['instrument_file']}")
            print(f"  contents_updated: "
                  f"{data['extension_contents_updated']}")
            print(f"  registry_added  : {data['registry_entry_added']}")
            if data["changes_preview"]:
                print(f"  preview         : {data['changes_preview']}")
            for e in data["errors"]:
                print(f"  ERROR  {e.get('error_class')}: "
                      f"{e.get('message')}")
            for w in data["warnings"]:
                print(f"  WARN   {w.get('warning_class')}: "
                      f"{w.get('message')}")
        return 0 if data["status"] == "ok" else 1

    print(f"unknown extension sub-command: {sub}", file=sys.stderr)
    return 2


def cmd_instrument(args: argparse.Namespace) -> int:
    """v1.8: instrument scaffold (top-level)"""
    if args.inst_command == "scaffold":
        from visa_mcp.instrument_authoring import (
            scaffold_instrument_definition,
        )
        res = scaffold_instrument_definition(
            args.category,
            output=args.output,
            manufacturer=args.manufacturer,
            model=args.model,
            force=args.force,
        )
        data = res.to_dict()
        if args.json:
            print(json.dumps({"scaffold": data},
                              ensure_ascii=False, indent=2, default=str))
        else:
            icon = "[OK]" if data["status"] == "ok" else "[ERR]"
            print(f"{icon} scaffold {args.category} -> "
                  f"{data['output_path']}")
            for e in data["errors"]:
                print(f"  ERROR  {e.get('error_class')}: "
                      f"{e.get('message')}")
            for w in data["warnings"]:
                print(f"  WARN   {w.get('warning_class')}: "
                      f"{w.get('message')}")
            if data["status"] == "ok":
                print("  next: visa-mcp validate instrument "
                      f"{data['output_path']}")
        return 0 if data["status"] == "ok" else 1

    print(f"unknown instrument sub-command: {args.inst_command}",
          file=sys.stderr)
    return 2


def cmd_registry(args: argparse.Namespace) -> int:
    """v1.4: registry overlay 表示"""
    from visa_mcp.extension_install import load_overlay_registry
    if args.reg_command == "overlay":
        builtin = (Path(__file__).parent.parent.parent / "registry"
                   / "INDEX.yaml")
        rep = load_overlay_registry(builtin if builtin.exists() else None)
        data = rep.to_dict()
        if args.source:
            data["entries"] = [
                e for e in data["entries"]
                if (e.get("source") or {}).get("kind") == args.source
            ]
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2,
                              default=str))
        else:
            icon = {"ok": "[OK]", "warning": "[WARN]",
                    "error": "[ERR]"}.get(data["status"], "[?]")
            print(f"{icon} overlay registry  status={data['status']}  "
                  f"entries={len(data['entries'])}  "
                  f"builtin={data['builtin_count']}  "
                  f"extension={data['extension_count']}")
            for e in data["entries"]:
                src = e.get("source") or {}
                if src.get("kind") == "extension":
                    src_str = (f"extension({src.get('extension_id')}@"
                                f"{src.get('extension_version')})")
                else:
                    src_str = "builtin"
                print(f"  {e['id']:30s} {e.get('vendor', ''):15s} "
                      f"{e.get('model', ''):15s} {src_str}")
            for er in data["errors"]:
                print(f"  ERROR  {er.get('error_class')}: "
                      f"{er.get('message')}")
        return 0 if data["status"] != "error" else 1

    print(f"unknown registry sub-command: {args.reg_command}",
          file=sys.stderr)
    return 2


def _emit_extension(rep: dict, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"reports": [rep]},
                          ensure_ascii=False, indent=2, default=str))
    else:
        print(_fmt_human(rep))
        if rep.get("install_path"):
            print(f"  installed: {rep['install_path']}")
        if rep.get("removed_path"):
            print(f"  removed: {rep['removed_path']}")
    return 0 if rep.get("status") != "error" else 1


def main() -> int:
    # 互換性: 引数なしで visa-mcp と呼ばれた場合は serve として扱う
    if len(sys.argv) == 1:
        from visa_mcp.server import main as server_main
        server_main()
        return 0
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
