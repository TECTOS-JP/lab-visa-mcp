"""
v1.3.0: Local Definition Pack management (install / list / uninstall + overlay)

合言葉: 「**definition pack を「作れる」から「安全に導入できる」へ**」

- 実行 Python plugin は **未対応** (v1.x 内予定なし)
- リモート install は **未対応** (ローカル path からのみ)
- install 先: `~/.visa-mcp/extensions/<extension_id>/`
- lockfile: `~/.visa-mcp/extensions.lock.json`
- 整合性: 各 file の sha256 を metadata に保存
- duplicate: 同 id + 同 version は `--force` 必須

詳細仕様: `docs/extension_install.md`, `docs/extension_registry_overlay.md`
"""
from __future__ import annotations
import hashlib
import json
import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from visa_mcp.extension import validate_extension_file

logger = logging.getLogger(__name__)


# ============================================================
# Paths
# ============================================================


def default_extensions_dir() -> Path:
    return Path.home() / ".visa-mcp" / "extensions"


def default_lockfile_path() -> Path:
    return Path.home() / ".visa-mcp" / "extensions.lock.json"


# ============================================================
# Lockfile
# ============================================================


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_lockfile(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"installed_extensions": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("lockfile parse failed (%s); starting fresh", path)
        return {"installed_extensions": []}


def _write_lockfile(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


# ============================================================
# Install / list / uninstall
# ============================================================


@dataclass
class InstallResult:
    status: str   # "ok" / "error"
    extension_id: str = ""
    version: str = ""
    install_path: str = ""
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "extension_id": self.extension_id,
            "version": self.version,
            "install_path": self.install_path,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "metadata": self.metadata,
        }


def install_definition_pack(
    extension_yaml_path: str | Path,
    *,
    force: bool = False,
    extensions_dir: Path | None = None,
    lockfile_path: Path | None = None,
) -> InstallResult:
    """v1.3.0: definition pack を local user 領域へ安全に install。

    Steps:
      1. extension.yaml を read + path 安全性検査 (validate_extension_file)
      2. duplicate (extension_id) を lockfile から確認
      3. extension.yaml の dir 全体を staging tmp にコピー
      4. validate (sub-files schema 通過確認)
      5. atomic rename で install_path へ
      6. sha256 metadata 保存
      7. lockfile を更新
    """
    extensions_dir = extensions_dir or default_extensions_dir()
    lockfile_path = lockfile_path or default_lockfile_path()
    result = InstallResult(status="error")

    src = Path(extension_yaml_path).expanduser()
    if not src.exists():
        result.errors.append({
            "error_class": "not_found",
            "message": f"extension.yaml not found: {src}",
        })
        return result

    # 1+4. validate_extension_file (pack 全体 + path 安全)
    val_rep = validate_extension_file(src)
    if val_rep.errors:
        result.errors.extend(val_rep.errors)
        result.errors.append({
            "error_class": "validation",
            "message": "extension pack validation failed; install aborted",
            "details": {"sub_class": "extension_validation_failed"},
        })
        return result
    result.warnings.extend(val_rep.warnings)

    manifest = val_rep.manifest or {}
    ext_id = manifest.get("extension_id")
    version = manifest.get("version")
    if not ext_id or not version:
        result.errors.append({
            "error_class": "validation",
            "message": "manifest に extension_id / version が無い",
        })
        return result
    result.extension_id = ext_id
    result.version = version

    # 2. duplicate チェック
    lock = _read_lockfile(lockfile_path)
    existing = [e for e in lock.get("installed_extensions", [])
                if e.get("extension_id") == ext_id]
    if existing and not force:
        ex = existing[0]
        result.errors.append({
            "error_class": "validation",
            "message": (
                f"extension_id={ext_id!r} は既に install 済み "
                f"(version={ex.get('version')}). 上書きには --force が必要"
            ),
            "details": {
                "sub_class": "extension_duplicate_install",
                "existing_version": ex.get("version"),
                "new_version": version,
            },
        })
        return result

    # 3. staging copy (pack 内全ファイルを tmp にコピー)
    pack_src_dir = src.parent
    install_path = extensions_dir / ext_id
    extensions_dir.mkdir(parents=True, exist_ok=True)
    tmpdir = Path(tempfile.mkdtemp(
        prefix=f"visa-mcp-ext-{ext_id}-", dir=str(extensions_dir),
    ))
    try:
        # pack 内 file をすべて copy (再帰)
        for src_path in pack_src_dir.rglob("*"):
            if src_path.is_file():
                rel = src_path.relative_to(pack_src_dir)
                dst = tmpdir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dst)

        # 5. atomic rename
        if install_path.exists():
            # force 上書き: 既存削除
            shutil.rmtree(install_path)
        tmpdir.replace(install_path)
    except Exception as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        result.errors.append({
            "error_class": "internal",
            "message": f"staging copy failed: {e}",
        })
        return result

    # 6. sha256 metadata
    checksums: dict[str, str] = {}
    for f in install_path.rglob("*"):
        if f.is_file() and f.name != ".install_meta.json":
            rel = str(f.relative_to(install_path)).replace("\\", "/")
            checksums[rel] = hashlib.sha256(f.read_bytes()).hexdigest()

    meta = {
        "extension_id": ext_id,
        "version": version,
        "installed_at": _now_iso(),
        "source_path": str(src),
        "visa_mcp_version": _current_visa_mcp_version(),
        "checksums": checksums,
        "manifest": manifest,
    }
    (install_path / ".install_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    result.metadata = meta
    result.install_path = str(install_path)

    # 7. lockfile 更新 (既存 entry を置換)
    new_entries = [
        e for e in lock.get("installed_extensions", [])
        if e.get("extension_id") != ext_id
    ]
    new_entries.append({
        "extension_id": ext_id,
        "version": version,
        "path": str(install_path),
        "installed_at": meta["installed_at"],
        "visa_mcp_version": meta["visa_mcp_version"],
    })
    _write_lockfile(lockfile_path, {"installed_extensions": new_entries})

    result.status = "ok"
    return result


def list_installed_packs(
    *,
    extensions_dir: Path | None = None,
    lockfile_path: Path | None = None,
) -> list[dict[str, Any]]:
    """install 済み pack 一覧を返す (lockfile ベース)"""
    lockfile_path = lockfile_path or default_lockfile_path()
    lock = _read_lockfile(lockfile_path)
    return list(lock.get("installed_extensions", []))


def uninstall_definition_pack(
    extension_id: str,
    *,
    extensions_dir: Path | None = None,
    lockfile_path: Path | None = None,
) -> dict[str, Any]:
    """指定 extension_id の install を取り消す。返却: result dict"""
    extensions_dir = extensions_dir or default_extensions_dir()
    lockfile_path = lockfile_path or default_lockfile_path()
    lock = _read_lockfile(lockfile_path)
    entries = lock.get("installed_extensions", [])
    target = next((e for e in entries
                    if e.get("extension_id") == extension_id), None)
    if target is None:
        return {
            "status": "error",
            "errors": [{
                "error_class": "not_found",
                "message": f"extension_id={extension_id!r} は install されていません",
            }],
        }

    install_path = Path(target["path"])
    try:
        if install_path.exists():
            shutil.rmtree(install_path)
    except Exception as e:
        return {
            "status": "error",
            "errors": [{
                "error_class": "internal",
                "message": f"uninstall path 削除失敗: {e}",
            }],
        }

    remaining = [e for e in entries if e.get("extension_id") != extension_id]
    _write_lockfile(lockfile_path, {"installed_extensions": remaining})
    return {
        "status": "ok",
        "extension_id": extension_id,
        "removed_path": str(install_path),
    }


# ============================================================
# Overlay registry
# ============================================================


@dataclass
class OverlayEntry:
    id: str
    vendor: str
    model: str
    category: str
    support_level: str
    path: str          # 絶対 path
    source: dict[str, Any]   # {"kind": "builtin"} or {"kind": "extension", ...}


@dataclass
class OverlayValidationReport:
    status: str = "ok"  # ok / warning / error
    entries: list[OverlayEntry] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "entries": [
                {
                    "id": e.id, "vendor": e.vendor, "model": e.model,
                    "category": e.category, "support_level": e.support_level,
                    "path": e.path, "source": e.source,
                }
                for e in self.entries
            ],
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "builtin_count": sum(
                1 for e in self.entries if e.source.get("kind") == "builtin"
            ),
            "extension_count": sum(
                1 for e in self.entries if e.source.get("kind") == "extension"
            ),
        }


def load_overlay_registry(
    builtin_index_path: str | Path | None,
    *,
    extensions_dir: Path | None = None,
    lockfile_path: Path | None = None,
) -> OverlayValidationReport:
    """built-in registry (INDEX.yaml) + installed extensions の
    registry_entries を **overlay** として統合し、duplicate id を error
    として検出する。

    優先順位 (v1.3 では duplicate を error にするだけで明示 override 無し):
      1. built-in registry IDと extension registry IDが衝突 → error
      2. extension 同士の id 衝突 → error
    """
    rep = OverlayValidationReport()

    # built-in
    if builtin_index_path is not None:
        bpath = Path(builtin_index_path)
        if bpath.exists():
            try:
                raw = yaml.safe_load(bpath.read_text(encoding="utf-8")) or {}
                for item in raw.get("instruments") or []:
                    rep.entries.append(OverlayEntry(
                        id=item.get("id", ""),
                        vendor=item.get("vendor", ""),
                        model=item.get("model", ""),
                        category=item.get("category", ""),
                        support_level=item.get("support_level", ""),
                        path=str((bpath.parent / item.get("path", "")).resolve()),
                        source={"kind": "builtin"},
                    ))
            except Exception as e:
                rep.errors.append({
                    "error_class": "schema_invalid",
                    "message": f"built-in INDEX.yaml parse failed: {e}",
                })

    # extensions
    for pack in list_installed_packs(
        extensions_dir=extensions_dir, lockfile_path=lockfile_path,
    ):
        ext_id = pack.get("extension_id", "")
        ext_ver = pack.get("version", "")
        pack_path = Path(pack["path"])
        # extension.yaml を読み、contents.registry_entries を解決
        manifest_path = pack_path / "extension.yaml"
        if not manifest_path.exists():
            rep.warnings.append({
                "warning_class": "extension_missing_manifest",
                "message": (
                    f"installed pack '{ext_id}' に extension.yaml が無い"
                ),
            })
            continue
        try:
            mf = yaml.safe_load(
                manifest_path.read_text(encoding="utf-8"),
            ) or {}
        except Exception:
            continue
        contents = (mf.get("contents") or {})
        for rel in (contents.get("registry_entries") or []):
            entry_file = pack_path / rel
            if not entry_file.exists():
                continue
            try:
                edata = yaml.safe_load(
                    entry_file.read_text(encoding="utf-8"),
                ) or {}
            except Exception:
                continue
            for item in edata.get("instruments") or []:
                rep.entries.append(OverlayEntry(
                    id=item.get("id", ""),
                    vendor=item.get("vendor", ""),
                    model=item.get("model", ""),
                    category=item.get("category", ""),
                    support_level=item.get("support_level", ""),
                    path=str((pack_path / item.get("path", "")).resolve()),
                    source={
                        "kind": "extension",
                        "extension_id": ext_id,
                        "extension_version": ext_ver,
                    },
                ))

    # duplicate id 検出
    seen: dict[str, OverlayEntry] = {}
    for e in rep.entries:
        if not e.id:
            continue
        if e.id in seen:
            other = seen[e.id]
            rep.errors.append({
                "error_class": "validation",
                "message": (
                    f"overlay registry に duplicate id={e.id!r}: "
                    f"{other.source} と {e.source}"
                ),
                "details": {
                    "sub_class": "overlay_registry_duplicate_id",
                    "id": e.id,
                    "sources": [other.source, e.source],
                },
            })
        else:
            seen[e.id] = e

    if rep.errors:
        rep.status = "error"
    elif rep.warnings:
        rep.status = "warning"
    return rep


def _current_visa_mcp_version() -> str:
    try:
        from visa_mcp import __version__
        return __version__
    except Exception:
        return "unknown"
