"""
v0.9.2: instrument 定義 registry + lint + validation helpers (experimental)

`registry/INDEX.yaml` を読み、機器定義 YAML を schema validation + lint で
品質判定する。CLI (`visa-mcp validate ...`) のバックエンド。
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from visa_mcp.models.instrument_def import InstrumentDefinition

logger = logging.getLogger(__name__)


SUPPORT_LEVELS = ("verified", "tested", "experimental", "draft")


# ============================================================
# Registry index
# ============================================================


@dataclass
class RegistryEntry:
    id: str
    vendor: str
    model: str
    category: str
    support_level: str
    path: str


@dataclass
class RegistryIndex:
    instruments: list[RegistryEntry] = field(default_factory=list)
    root: Path | None = None


def load_registry_index(index_path: str | Path) -> RegistryIndex:
    p = Path(index_path)
    if not p.exists():
        raise FileNotFoundError(p)
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    entries: list[RegistryEntry] = []
    for item in raw.get("instruments") or []:
        entries.append(RegistryEntry(
            id=item.get("id", ""),
            vendor=item.get("vendor", ""),
            model=item.get("model", ""),
            category=item.get("category", ""),
            support_level=item.get("support_level", "draft"),
            path=item.get("path", ""),
        ))
    return RegistryIndex(instruments=entries, root=p.parent)


# ============================================================
# Validation + lint
# ============================================================


@dataclass
class ValidationReport:
    """schema validation + lint の結果"""
    file: str
    schema: str = ""
    status: str = "ok"          # "ok" / "warning" / "error"
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "file": self.file,
            "schema": self.schema,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def validate_instrument_file(path: str | Path) -> ValidationReport:
    """機器定義 YAML を Pydantic schema + lint で検証"""
    rep = ValidationReport(file=str(path), schema="instrument.schema.json")
    p = Path(path)
    if not p.exists():
        rep.status = "error"
        rep.errors.append({
            "error_class": "not_found",
            "message": f"file not found: {p}",
        })
        return rep
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        defn = InstrumentDefinition(**raw)
    except Exception as e:
        rep.status = "error"
        rep.errors.append({
            "error_class": "schema_invalid",
            "message": str(e),
        })
        return rep

    # ---- lint warnings ----
    md = defn.metadata
    if md.support_level not in SUPPORT_LEVELS:
        rep.warnings.append({
            "warning_class": "invalid_support_level",
            "message": (
                f"metadata.support_level={md.support_level!r} は "
                f"{list(SUPPORT_LEVELS)} のいずれか推奨"
            ),
            "field_path": "metadata.support_level",
        })
    if md.support_level == "draft":
        rep.warnings.append({
            "warning_class": "support_level_draft",
            "message": (
                "support_level=draft はAIエージェント Plan 生成時に注意推奨。"
                "tested / verified への昇格を検討してください"
            ),
            "field_path": "metadata.support_level",
        })
    if not md.manufacturer or not md.model:
        rep.warnings.append({
            "warning_class": "missing_metadata",
            "message": "metadata.manufacturer / model が必須",
            "field_path": "metadata",
        })

    # safe_shutdown
    has_shutdown = (defn.safe_shutdown is not None
                    and len(defn.safe_shutdown) > 0)
    has_write = any(
        getattr(c, "type", "") == "write" for c in defn.commands.values()
    )
    if has_write and not has_shutdown:
        rep.warnings.append({
            "warning_class": "missing_safe_shutdown",
            "message": (
                "出力系 command (write 型) があるが safe_shutdown が未定義。"
                "cancel / interrupted / resume 前の安全停止に必須"
            ),
            "field_path": "safe_shutdown",
        })

    # state_query
    if not defn.state_query:
        rep.warnings.append({
            "warning_class": "missing_state_query",
            "message": (
                "state_query が空。get_state / describe_instrument / "
                "wait_for_condition の AI 利用に必要"
            ),
            "field_path": "state_query",
        })

    # verify: set 系 write command に verify が無いものを警告
    for name, cmd in defn.commands.items():
        if getattr(cmd, "type", "") == "write" and name.startswith("set"):
            if cmd.verify is None:
                rep.warnings.append({
                    "warning_class": "missing_verify",
                    "message": (
                        f"command '{name}' (write/set 系) に verify が未定義。"
                        f"write 後 read-back の自動確認を推奨"
                    ),
                    "field_path": f"commands.{name}.verify",
                })

    if not rep.errors and rep.warnings:
        rep.status = "warning"
    elif not rep.errors and not rep.warnings:
        rep.status = "ok"
    return rep


REQUIRED_INDEX_FIELDS = ("id", "vendor", "model", "category", "path")


def _validate_index_entries(
    index_path: Path, root: Path,
) -> ValidationReport:
    """v0.9.2.1: INDEX.yaml 自体の品質検証 (必須項目 / 重複 ID / path 存在)"""
    rep = ValidationReport(file=str(index_path),
                            schema="registry_index (v0.9.2.1)")
    try:
        raw = yaml.safe_load(index_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        rep.status = "error"
        rep.errors.append({
            "error_class": "schema_invalid",
            "message": f"INDEX.yaml parse failed: {e}",
        })
        return rep

    entries = raw.get("instruments") or []
    if not isinstance(entries, list):
        rep.status = "error"
        rep.errors.append({
            "error_class": "schema_invalid",
            "message": "INDEX.yaml.instruments は list が必要",
        })
        return rep

    seen_ids: set[str] = set()
    for i, item in enumerate(entries):
        if not isinstance(item, dict):
            rep.errors.append({
                "error_class": "schema_invalid",
                "message": f"entry[{i}] は dict が必要",
            })
            continue
        # 必須項目
        for fld in REQUIRED_INDEX_FIELDS:
            if not item.get(fld):
                rep.errors.append({
                    "error_class": "registry_entry_missing_field",
                    "message": (
                        f"entry[{i}] (id={item.get('id', '?')!r}) に "
                        f"'{fld}' が無い"
                    ),
                    "field_path": f"instruments[{i}].{fld}",
                })
        # 重複 id
        eid = item.get("id")
        if eid:
            if eid in seen_ids:
                rep.errors.append({
                    "error_class": "registry_duplicate_id",
                    "message": f"id={eid!r} が重複",
                    "field_path": f"instruments[{i}].id",
                })
            seen_ids.add(eid)
        # path 存在 + registry 配下
        p = item.get("path")
        if p:
            full = (root / p) if not Path(p).is_absolute() else Path(p)
            if not full.exists():
                rep.errors.append({
                    "error_class": "registry_entry_path_not_found",
                    "message": f"entry[{i}] path={p!r} が存在しません",
                    "field_path": f"instruments[{i}].path",
                })
            else:
                try:
                    full.resolve().relative_to(root.resolve())
                except ValueError:
                    rep.warnings.append({
                        "warning_class": "registry_path_outside_registry",
                        "message": (
                            f"entry[{i}] path={p!r} が registry/ 配下では"
                            f"ありません"
                        ),
                        "field_path": f"instruments[{i}].path",
                    })
        # support_level の語彙チェック (registry 側では error にする)
        sl = item.get("support_level")
        if sl is not None and sl not in SUPPORT_LEVELS:
            rep.errors.append({
                "error_class": "invalid_support_level",
                "message": (
                    f"entry[{i}] support_level={sl!r} は "
                    f"{list(SUPPORT_LEVELS)} のいずれかが必要"
                ),
                "field_path": f"instruments[{i}].support_level",
            })

    if rep.errors:
        rep.status = "error"
    elif rep.warnings:
        rep.status = "warning"
    return rep


def validate_registry(index_path: str | Path) -> list[ValidationReport]:
    """INDEX.yaml に列挙された全機器定義を検証 (INDEX 自身 + 各 entry)"""
    idx = load_registry_index(index_path)
    out: list[ValidationReport] = []
    root = idx.root or Path(index_path).parent
    # v0.9.2.1: INDEX 自体の lint (必須項目 / 重複 / path / support_level)
    index_rep = _validate_index_entries(Path(index_path), root)
    out.append(index_rep)
    for e in idx.instruments:
        defn_path = root / e.path if not Path(e.path).is_absolute() else Path(e.path)
        rep = validate_instrument_file(defn_path)
        rep.file = str(defn_path)
        # entry-level チェック
        if not defn_path.exists():
            # validate_instrument_file が既に error にする
            pass
        else:
            # 申告 support_level と YAML 内 support_level の一致
            try:
                raw = yaml.safe_load(defn_path.read_text(encoding="utf-8")) or {}
                yaml_sl = (raw.get("metadata") or {}).get("support_level")
                if yaml_sl and yaml_sl != e.support_level:
                    rep.warnings.append({
                        "warning_class": "registry_support_level_mismatch",
                        "message": (
                            f"INDEX.yaml の support_level={e.support_level!r} と "
                            f"機器定義 YAML の support_level={yaml_sl!r} が不一致"
                        ),
                        "field_path": "metadata.support_level",
                    })
                    if rep.status == "ok":
                        rep.status = "warning"
            except Exception:
                pass
        out.append(rep)
    return out


# ============================================================
# Plan / Benchmark task validation (CLI 用)
# ============================================================


def validate_plan_file(path: str | Path) -> ValidationReport:
    """DSL plan JSON / YAML をパースして ExperimentPlan として validate

    **重要 (v0.9.2.1 docs 明記)**: ここでの validation は **Pydantic schema 確認**
    のみ。system_config / instrument 定義の参照解決 / safety / resource は
    **行わない**。これらを含む完全 validation は MCP tool
    `validate_experiment_plan` (`validate_and_compile` 経由) を使ってください。
    """
    rep = ValidationReport(file=str(path), schema="dsl.schema.json")
    p = Path(path)
    if not p.exists():
        rep.status = "error"
        rep.errors.append({"error_class": "not_found",
                            "message": f"file not found: {p}"})
        return rep
    try:
        text = p.read_text(encoding="utf-8")
        if p.suffix in (".yaml", ".yml"):
            data = yaml.safe_load(text)
        else:
            import json
            data = json.loads(text)
    except Exception as e:
        rep.status = "error"
        rep.errors.append({"error_class": "schema_invalid",
                            "message": f"parse failed: {e}"})
        return rep
    try:
        from visa_mcp.dsl.schema import ExperimentPlan
        ExperimentPlan(**data)
    except Exception as e:
        rep.status = "error"
        rep.errors.append({"error_class": "schema_invalid",
                            "message": str(e)})
    return rep


def validate_benchmark_task_file(path: str | Path) -> ValidationReport:
    rep = ValidationReport(file=str(path), schema="benchmark_task.schema.json")
    p = Path(path)
    if not p.exists():
        rep.status = "error"
        rep.errors.append({"error_class": "not_found",
                            "message": f"file not found: {p}"})
        return rep
    try:
        from visa_mcp.testing.benchmark_task import load_benchmark_task
        load_benchmark_task(p)
    except Exception as e:
        rep.status = "error"
        rep.errors.append({"error_class": "schema_invalid",
                            "message": str(e)})
    return rep


def validate_system_config_file(path: str | Path) -> ValidationReport:
    rep = ValidationReport(file=str(path), schema="system_config.schema.json")
    p = Path(path)
    if not p.exists():
        rep.status = "error"
        rep.errors.append({"error_class": "not_found",
                            "message": f"file not found: {p}"})
        return rep
    try:
        from visa_mcp.system_config import SystemConfig
        SystemConfig.from_yaml(p)
    except Exception as e:
        rep.status = "error"
        rep.errors.append({"error_class": "schema_invalid",
                            "message": str(e)})
    return rep
