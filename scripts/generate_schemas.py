"""v0.8.1: Pydantic モデルから JSON Schema preview を生成。

出力: schemas/dsl.schema.json / schemas/instrument.schema.json /
      schemas/system_config.schema.json

各 schema には preview status を示すメタフィールドを付与:
  - "$id": preview URL
  - "x-visa-mcp-status": "preview"
  - "x-compatibility": "subject-to-change-before-v1.0"
"""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).parent.parent
SCHEMAS_DIR = ROOT / "schemas"
SRC = ROOT / "src"


def _ensure_path() -> None:
    p = str(SRC)
    if p not in sys.path:
        sys.path.insert(0, p)


def _add_preview_metadata(schema: dict, schema_id: str, title: str) -> dict:
    """v1.0: preview → stable へ昇格。

    schema core (top-level + 標準 step / 標準 metadata field) は v1.x 互換
    保証対象。experimental fields (template_source / resume metadata /
    audit/lock 関連) は docs/v1_stability_policy.md で別途明示。
    """
    schema["$id"] = (
        f"https://tectos-jp.github.io/visa-mcp/schemas/{schema_id}.schema.v1.json"
    )
    schema["title"] = title
    schema["x-visa-mcp-status"] = "stable"
    schema["x-compatibility"] = "v1.x-compatible"
    schema["description"] = (
        schema.get("description", "")
        + " (v1.0 stable. 詳細互換保証は docs/v1_stability_policy.md 参照。"
        " 一部 experimental fields は v1.x 内で変更可能。)"
    )
    return schema


def main() -> int:
    _ensure_path()
    SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)

    from visa_mcp.dsl.schema import ExperimentPlan
    dsl_schema = ExperimentPlan.model_json_schema()
    _add_preview_metadata(
        dsl_schema, "dsl", "Experiment DSL ExperimentPlan (v1.0 stable)",
    )
    (SCHEMAS_DIR / "dsl.schema.json").write_text(
        json.dumps(dsl_schema, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("generated: schemas/dsl.schema.json")

    from visa_mcp.models.instrument_def import InstrumentDefinition
    inst_schema = InstrumentDefinition.model_json_schema()
    _add_preview_metadata(
        inst_schema, "instrument", "Instrument YAML Definition (v1.0 stable)",
    )
    (SCHEMAS_DIR / "instrument.schema.json").write_text(
        json.dumps(inst_schema, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("generated: schemas/instrument.schema.json")

    # v0.9.2: BenchmarkTask schema を追加 (Ecosystem 準備)
    from visa_mcp.testing.benchmark_task import BenchmarkTask
    bench_schema = BenchmarkTask.model_json_schema()
    _add_preview_metadata(
        bench_schema, "benchmark_task",
        "Benchmark Task (incl. repair tasks) (v1.0 stable)",
    )
    (SCHEMAS_DIR / "benchmark_task.schema.json").write_text(
        json.dumps(bench_schema, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("generated: schemas/benchmark_task.schema.json")

    # v1.2: ExtensionManifest schema (experimental, definition pack manifest)
    # v1.2.1: title / description から「stable」表現を完全除去
    from visa_mcp.extension import ExtensionManifest
    ext_schema = ExtensionManifest.model_json_schema()
    ext_schema["$id"] = (
        "https://tectos-jp.github.io/visa-mcp/schemas/"
        "extension_manifest.schema.v1.json"
    )
    ext_schema["title"] = (
        "Extension Manifest (definition pack) (v1.2 experimental)"
    )
    ext_schema["x-visa-mcp-status"] = "experimental"
    ext_schema["x-compatibility"] = "subject-to-change-within-v1.x"
    ext_schema["description"] = (
        ext_schema.get("description", "")
        + " (EXPERIMENTAL: definition pack manifest introduced in v1.2."
        " The shape may change within v1.x. Not a stable plugin API.)"
    )
    (SCHEMAS_DIR / "extension_manifest.schema.json").write_text(
        json.dumps(ext_schema, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("generated: schemas/extension_manifest.schema.json")

    from visa_mcp.system_config import SystemConfig
    sysconf_schema = SystemConfig.model_json_schema()
    _add_preview_metadata(
        sysconf_schema, "system_config",
        "System Configuration (_system.yaml) (v1.0 stable)",
    )
    (SCHEMAS_DIR / "system_config.schema.json").write_text(
        json.dumps(sysconf_schema, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("generated: schemas/system_config.schema.json")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
