"""
v0.9.1+v1.0: 測定結果 export API (3 ツール構成)

- `get_experiment_results` (stable v1.0): MCP 応答で少量確認用 JSON
- `export_experiment_results` (stable v1.0): CSV / JSONL ファイル出力
- `export_experiment_bundle` (experimental v1.0): 再現性 bundle (zip)
  with manifest + checksums。`import_*` は v1.1+ 候補。

設計原則:
- MCP 応答に大量データを返さない (LLM context 浪費を避ける)
- 大量データは default export dir (`~/.visa-mcp/exports/`) 配下のファイルへ
- monitor_data はデフォルト除外 (詳細時系列は既存 get_monitor_data に任せる)
- bundle は **再検証・共有・監査・記事化** のためのパッケージ
  (別環境での完全再現実行は v1.x 内ではサポートしない)
"""
from __future__ import annotations
import csv
import hashlib
import io
import json
import logging
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from visa_mcp.job import JobManager
from visa_mcp.response_envelope import make_envelope, make_error

logger = logging.getLogger(__name__)


# 既定 export ディレクトリ
DEFAULT_EXPORT_DIR = Path.home() / ".visa-mcp" / "exports"

# 結果行の標準 columns (v0.9.1, v1.0 で凍結候補)
RESULT_COLUMNS = (
    "timestamp",
    "target_id",
    "instrument",
    "measurement",
    "value",
    "unit",
    "step_index",
    "step_path",
)


def _safe_export_path(
    output_path: str | None, *, default_filename: str,
    overwrite: bool,
) -> tuple[Path | None, dict | None]:
    """output_path を検証して安全な絶対パスに正規化する。

    Returns:
        (path, error_dict)
        path: 安全な絶対パス (失敗時 None)
        error_dict: 失敗理由 (成功時 None)
    """
    base = DEFAULT_EXPORT_DIR.resolve()
    base.mkdir(parents=True, exist_ok=True)

    if output_path is None or not output_path.strip():
        # v0.9.1: default パスでも existence チェックを通す
        default_path = (base / default_filename).resolve()
        if default_path.exists() and not overwrite:
            return (None, {
                "error_class": "invalid_export_path",
                "message": (
                    f"既定 export path がすでに存在します。overwrite=true を指定するか "
                    f"output_path を別名にしてください: {default_path}"
                ),
                "recoverable": True,
                "details": {"existing_path": str(default_path)},
                # v0.9.1.1: recommended_next_actions (レビュー指摘 P1)
                "recommended_next_actions": [
                    {"action": "set_overwrite_true",
                     "reason": "前回の export を意図的に上書きする"},
                    {"action": "choose_different_output_path",
                     "reason": "別 path を指定して前回の export を保持する"},
                ],
            })
        return (default_path, None)

    candidate = Path(output_path).expanduser()
    # default_dir 配下に強制
    try:
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            resolved = (base / candidate).resolve()
    except (OSError, ValueError) as e:
        return (None, {
            "error_class": "invalid_export_path",
            "message": f"output_path を解決できません: {e}",
            "recoverable": True,
        })

    # path traversal 検出: resolved が base の subpath でない
    try:
        resolved.relative_to(base)
    except ValueError:
        return (None, {
            "error_class": "invalid_export_path",
            "message": (
                f"output_path は {base} 配下である必要があります "
                f"(absolute paths / .. による traversal は拒否)"
            ),
            "recoverable": True,
            "details": {"base_dir": str(base), "rejected_path": str(resolved)},
        })

    if resolved.exists() and not overwrite:
        return (None, {
            "error_class": "invalid_export_path",
            "message": (
                f"output_path はすでに存在します。overwrite=true を指定するか "
                f"別のパスを使ってください: {resolved}"
            ),
            "recoverable": True,
            "details": {"existing_path": str(resolved)},
            "recommended_next_actions": [
                {"action": "set_overwrite_true",
                 "reason": "前回の export を意図的に上書きする"},
                {"action": "choose_different_output_path",
                 "reason": "別 path を指定して前回の export を保持する"},
            ],
        })
    return (resolved, None)


def _extract_result_rows(
    job_mgr: JobManager, job_id: str, *, include_monitor: bool = False,
) -> list[dict[str, Any]]:
    """job_steps / target_runs / (オプションで monitor_data) から
    測定結果行を抽出して標準 columns に正規化する。
    """
    store = job_mgr.store
    rows: list[dict[str, Any]] = []

    # job_steps: result/error に measurement / value / unit が入っている可能性
    try:
        steps = store.list_steps(job_id)
    except Exception:
        steps = []
    for s in steps:
        r = s.get("result") or s.get("error") or {}
        if not isinstance(r, dict):
            continue
        # query 系: response_parsed が dict なら個別の measurement / value / unit
        # を抽出。それ以外は command + response_raw を保持。
        ts = s.get("ended_at") or s.get("started_at")
        common = {
            "timestamp": ts,
            "target_id": s.get("target_id"),
            "instrument": r.get("instrument"),
            "step_index": s.get("step_index"),
            "step_path": (r.get("step_path") or
                          (f"steps[{s.get('step_index')}]"
                           if s.get("step_index") is not None else None)),
        }
        # parsed measurement (v0.8.x response_parsed dict)
        parsed = r.get("response_parsed") if isinstance(r, dict) else None
        if isinstance(parsed, dict) and parsed:
            for k, v in parsed.items():
                rows.append({
                    **common,
                    "measurement": k,
                    "value": v,
                    "unit": "",
                })
        else:
            # 数値 raw response or value
            v = r.get("value")
            if v is None:
                v = r.get("response_raw") or r.get("response")
            if v is not None:
                rows.append({
                    **common,
                    "measurement": r.get("command") or s.get("step_type"),
                    "value": v,
                    "unit": r.get("unit", ""),
                })

    # target_runs: 同じく result 内のキー抽出
    try:
        truns = store.list_target_runs(job_id)
    except Exception:
        truns = []
    for t in truns:
        r = t.get("result") or {}
        if not isinstance(r, dict):
            continue
        ts = t.get("ended_at") or t.get("started_at")
        # target_runs.result は target ごとの最終結果。measurement key を直接
        # 持つこともあれば、step_results を内包することもある。
        for key, val in r.items():
            if key in ("step_results", "success", "summary"):
                continue
            if isinstance(val, (int, float, str, bool)):
                rows.append({
                    "timestamp": ts,
                    "target_id": t.get("target_id"),
                    "instrument": None,
                    "measurement": key,
                    "value": val,
                    "unit": "",
                    "step_index": None,
                    "step_path": None,
                })

    # monitor_data (オプション、monitor_id == job_id 前提の慣用に従う)
    # 注: monitor_data は monitor_id でキー付けされており、Job との関連は
    # 既存実装で monitor_id を job 経由で渡す慣習。MVP では job_id を monitor_id
    # として直接照会する (start_monitor が同名で動く想定の最小実装)。
    if include_monitor:
        try:
            mcount = store.count_monitor_data(job_id)
            md = store.list_monitor_data(job_id, limit=min(mcount, 100000)
                                          if mcount else 100000)
        except Exception:
            md = []
        for m in md:
            rows.append({
                "timestamp": m.get("timestamp"),
                "target_id": None,
                "instrument": m.get("instrument"),
                "measurement": m.get("measurement") or m.get("key"),
                "value": m.get("value"),
                "unit": m.get("unit", ""),
                "step_index": None,
                "step_path": None,
            })

    return rows


def register_tools(mcp: FastMCP, job_mgr: JobManager) -> None:

    @mcp.tool()
    async def get_experiment_results(
        job_id: str,
        limit: int = 1000,
        offset: int = 0,
        include_monitor_data: bool = False,
    ) -> dict:
        """**(experimental, v0.9.1)** Job の測定結果を少量確認用 JSON で取得

        job_steps / target_runs に保存された最終的な測定結果を、標準 columns
        (timestamp / target_id / instrument / measurement / value / unit /
        step_index / step_path) に正規化して返す。

        - **monitor_data はデフォルト除外** (大量データを混ぜないため)。
          詳細時系列は `get_monitor_data` を使ってください。
        - limit 上限 10000 (`get_monitor_data` と同じクランプ)。
        - 大量データは `export_experiment_results` (CSV/JSONL) を使ってください。

        Returns: data.{columns, rows, pagination}
        """
        try:
            job_mgr.get(job_id)
        except Exception:
            return make_envelope(
                "error",
                errors=[make_error("not_found",
                    f"job not found: {job_id}", recoverable=False)],
            )

        # limit クランプ
        if limit <= 0:
            limit = 1000
        clamp_warning = None
        if limit > 10000:
            clamp_warning = f"limit={limit} は上限 10000 にクランプ"
            limit = 10000
        if offset < 0:
            offset = 0

        try:
            all_rows = _extract_result_rows(
                job_mgr, job_id, include_monitor=include_monitor_data,
            )
        except Exception as e:
            return make_envelope(
                "error",
                errors=[make_error("internal", str(e), recoverable=False)],
            )

        total = len(all_rows)
        end = offset + limit
        page = all_rows[offset:end]
        has_more = end < total

        data: dict = {
            "job_id": job_id,
            "columns": list(RESULT_COLUMNS),
            "rows": page,
            "pagination": {
                "limit": limit, "offset": offset,
                "returned": len(page), "total": total,
                "has_more": has_more,
            },
            "include_monitor_data": include_monitor_data,
        }
        if clamp_warning:
            data["clamp_warning"] = clamp_warning
        return make_envelope("ok", data=data, job_id=job_id)

    @mcp.tool()
    async def export_experiment_results(
        job_id: str,
        format: str = "csv",
        include_monitor_data: bool = False,
        output_path: str = "",
        overwrite: bool = False,
    ) -> dict:
        """**(experimental, v0.9.1)** Job の測定結果を CSV / JSONL ファイル出力

        format: "csv" | "jsonl"
        include_monitor_data: True で monitor_data も含める
        output_path: 既定 `~/.visa-mcp/exports/` 配下。絶対パスや .. は拒否。
        overwrite: True で既存ファイルを上書き

        安全策 (P0):
          - default export dir 配下のみ許可
          - path traversal (.. / 絶対パス) 拒否 (invalid_export_path)
          - 既存ファイル overwrite=False で拒否

        Returns: data.{job_id, format, path, rows, sha256, include_monitor_data}
        """
        try:
            job_mgr.get(job_id)
        except Exception:
            return make_envelope(
                "error",
                errors=[make_error("not_found",
                    f"job not found: {job_id}", recoverable=False)],
            )

        if format not in ("csv", "jsonl"):
            # v0.9.1.1: sub_class から独立 error_class に昇格 (レビュー指摘 P1)
            return make_envelope(
                "error",
                errors=[make_error(
                    "unsupported_export_format",
                    f"unsupported export format: {format!r} (csv/jsonl のみ対応)",
                    recoverable=True,
                    details={"requested_format": format,
                             "supported_formats": ["csv", "jsonl"]},
                    recommended_next_actions=[
                        {"action": "use_csv_format",
                         "reason": "format='csv' で再試行"},
                        {"action": "use_jsonl_format",
                         "reason": "format='jsonl' で再試行"},
                    ],
                )],
            )

        default_name = f"{job_id}_results.{format}"
        path, err = _safe_export_path(
            output_path or None, default_filename=default_name,
            overwrite=overwrite,
        )
        if err is not None:
            return make_envelope(
                "error",
                errors=[make_error(
                    err["error_class"], err["message"],
                    recoverable=err.get("recoverable", True),
                    details=err.get("details"),
                    recommended_next_actions=err.get(
                        "recommended_next_actions"),
                )],
            )
        assert path is not None

        try:
            rows = _extract_result_rows(
                job_mgr, job_id, include_monitor=include_monitor_data,
            )
        except Exception as e:
            return make_envelope(
                "error",
                errors=[make_error("export_failed", str(e), recoverable=False)],
            )

        # 出力
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if format == "csv":
                buf = io.StringIO()
                writer = csv.DictWriter(buf, fieldnames=list(RESULT_COLUMNS))
                writer.writeheader()
                for r in rows:
                    writer.writerow({k: r.get(k, "") for k in RESULT_COLUMNS})
                text = buf.getvalue()
            else:  # jsonl
                lines = []
                for r in rows:
                    obj = {k: r.get(k) for k in RESULT_COLUMNS}
                    lines.append(json.dumps(obj, ensure_ascii=False, default=str))
                text = "\n".join(lines) + ("\n" if lines else "")
            data_bytes = text.encode("utf-8")
            path.write_bytes(data_bytes)
        except Exception as e:
            return make_envelope(
                "error",
                errors=[make_error("export_failed", str(e), recoverable=False)],
            )

        sha = hashlib.sha256(data_bytes).hexdigest()

        return make_envelope(
            "ok",
            data={
                "job_id": job_id,
                "format": format,
                "path": str(path),
                "rows": len(rows),
                "size_bytes": len(data_bytes),
                "sha256": sha,
                "include_monitor_data": include_monitor_data,
                "columns": list(RESULT_COLUMNS),
            },
            job_id=job_id,
        )

    # ====================================================
    # v1.0: export_experiment_bundle (experimental)
    # ====================================================

    @mcp.tool()
    async def export_experiment_bundle(
        job_id: str,
        output_path: str = "",
        include_monitor_data: bool = False,
        include_audit: bool = False,
        overwrite: bool = False,
    ) -> dict:
        """**(experimental, v1.0)** Job の実験記録を再現性 bundle (zip) へ

        bundle の目的: **再検証・共有・監査・記事化**。
        ⚠ v1.x では別環境での完全再現実行 (import / replay) はサポートしない。

        bundle 内容:
        ```
        bundle.zip
        ├── manifest.json          (bundle_version / visa_mcp_version / contents / checksums)
        ├── plan.json              (DSL plan original)
        ├── compiled_summary.json  (compile summary)
        ├── job_record.json        (jobs テーブル row)
        ├── job_summary.json       (build_run_summary)
        ├── timeline.jsonl         (job_events 全件)
        ├── results.jsonl          (測定結果)
        ├── results.csv            (同上 CSV 版)
        ├── monitor_data.jsonl     (include_monitor_data=True のみ)
        └── audit.jsonl            (include_audit=True のみ)
        ```

        manifest.contents 各エントリには SHA-256 が含まれる。

        path 安全策は `export_experiment_results` と同等
        (default dir / path traversal 拒否 / overwrite=False 既定)。
        """
        try:
            rec = job_mgr.get(job_id)
        except Exception:
            return make_envelope(
                "error",
                errors=[make_error("not_found",
                    f"job not found: {job_id}", recoverable=False)],
            )

        default_name = f"{job_id}_bundle.zip"
        path, err = _safe_export_path(
            output_path or None, default_filename=default_name,
            overwrite=overwrite,
        )
        if err is not None:
            return make_envelope(
                "error",
                errors=[make_error(
                    err["error_class"], err["message"],
                    recoverable=err.get("recoverable", True),
                    details=err.get("details"),
                )],
            )
        assert path is not None

        store = job_mgr.store
        try:
            # 各成果物を bytes として用意
            files: dict[str, bytes] = {}

            # plan + compiled_summary
            plan_row = None
            try:
                plan_row = store.get_experiment_plan_for_job(job_id)
            except Exception:
                pass
            if plan_row:
                files["plan.json"] = json.dumps(
                    plan_row.get("original_plan") or {},
                    ensure_ascii=False, indent=2, default=str,
                ).encode("utf-8")
                files["compiled_summary.json"] = json.dumps(
                    plan_row.get("compiled_summary") or {},
                    ensure_ascii=False, indent=2, default=str,
                ).encode("utf-8")

            # job record
            files["job_record.json"] = json.dumps(
                rec.to_dict(), ensure_ascii=False, indent=2, default=str,
            ).encode("utf-8")

            # job summary (build_run_summary)
            try:
                from visa_mcp.observation import build_run_summary
                steps = store.list_steps(job_id)
                target_runs = store.list_target_runs(job_id)
                monitor_count = 0
                try:
                    monitor_count = store.count_monitor_data(job_id)
                except Exception:
                    pass
                summary = build_run_summary(
                    rec.to_dict(), steps, target_runs,
                    monitor_count=monitor_count,
                )
                files["job_summary.json"] = json.dumps(
                    summary, ensure_ascii=False, indent=2, default=str,
                ).encode("utf-8")
            except Exception:
                pass

            # timeline (job_events all)
            try:
                events = store.list_events(job_id, limit=100000)
            except Exception:
                events = []
            timeline_lines = [
                json.dumps(e, ensure_ascii=False, default=str) for e in events
            ]
            files["timeline.jsonl"] = (
                "\n".join(timeline_lines) + ("\n" if timeline_lines else "")
            ).encode("utf-8")

            # results (json rows + csv)
            try:
                rows = _extract_result_rows(
                    job_mgr, job_id, include_monitor=include_monitor_data,
                )
            except Exception:
                rows = []
            files["results.jsonl"] = (
                "\n".join(
                    json.dumps({k: r.get(k) for k in RESULT_COLUMNS},
                                ensure_ascii=False, default=str)
                    for r in rows
                ) + ("\n" if rows else "")
            ).encode("utf-8")
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=list(RESULT_COLUMNS))
            writer.writeheader()
            for r in rows:
                writer.writerow({k: r.get(k, "") for k in RESULT_COLUMNS})
            files["results.csv"] = buf.getvalue().encode("utf-8")

            # monitor_data (optional)
            if include_monitor_data:
                try:
                    mcount = store.count_monitor_data(job_id)
                    md = store.list_monitor_data(
                        job_id, limit=min(max(mcount, 1), 100000),
                    )
                except Exception:
                    md = []
                files["monitor_data.jsonl"] = (
                    "\n".join(
                        json.dumps(m, ensure_ascii=False, default=str)
                        for m in md
                    ) + ("\n" if md else "")
                ).encode("utf-8")

            # audit (optional)
            if include_audit and getattr(job_mgr, "audit", None) is not None:
                try:
                    aud_events, _ = job_mgr.audit.query(
                        job_id=job_id, limit=5000, include_details=True,
                    )
                except Exception:
                    aud_events = []
                files["audit.jsonl"] = (
                    "\n".join(
                        json.dumps(e, ensure_ascii=False, default=str)
                        for e in aud_events
                    ) + ("\n" if aud_events else "")
                ).encode("utf-8")

            # manifest (checksums を計算)
            from visa_mcp import __version__ as VMV
            checksums = {
                name: hashlib.sha256(blob).hexdigest()
                for name, blob in files.items()
            }
            manifest = {
                "bundle_version": "1.0",
                "visa_mcp_version": VMV,
                "job_id": job_id,
                "created_at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds",
                ),
                "include_monitor_data": include_monitor_data,
                "include_audit": include_audit,
                "contents": sorted(files.keys()),
                "checksums": checksums,
                "note": (
                    "再検証 / 共有 / 監査 / 記事化用パッケージ。"
                    "v1.x では別環境での完全再現実行はサポートしない"
                ),
            }
            files["manifest.json"] = json.dumps(
                manifest, ensure_ascii=False, indent=2,
            ).encode("utf-8")

            # zip 出力
            path.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(
                path, "w", compression=zipfile.ZIP_DEFLATED,
            ) as zf:
                for name, blob in files.items():
                    zf.writestr(name, blob)
        except Exception as e:
            logger.exception("export_experiment_bundle 失敗")
            return make_envelope(
                "error",
                errors=[make_error("export_failed", str(e),
                                    recoverable=False)],
            )

        bundle_bytes = path.read_bytes()
        return make_envelope(
            "ok",
            data={
                "job_id": job_id,
                "bundle_version": "1.0",
                "path": str(path),
                "size_bytes": len(bundle_bytes),
                "sha256": hashlib.sha256(bundle_bytes).hexdigest(),
                "contents": sorted(files.keys()),
                "include_monitor_data": include_monitor_data,
                "include_audit": include_audit,
            },
            job_id=job_id,
        )
