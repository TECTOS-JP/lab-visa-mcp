from __future__ import annotations
import asyncio
import logging
from functools import partial

logger = logging.getLogger(__name__)

# pyvisa はオプション扱い（テスト時はモック可能）
try:
    import pyvisa
    import pyvisa.errors
    _PYVISA_AVAILABLE = True
except ImportError:
    _PYVISA_AVAILABLE = False


class VisaError(RuntimeError):
    pass

class VisaConnectionError(VisaError):
    pass

class VisaTimeoutError(VisaError):
    pass


class VisaManager:
    """
    pyvisa.ResourceManager のシングルトンラッパー。
    PyVISA のブロッキング呼び出しを asyncio.run_in_executor でラップして提供する。
    NI-VISA バックエンドのみ使用（フォールバックなし）。
    """

    def __init__(self, bus_manager=None) -> None:
        if not _PYVISA_AVAILABLE:
            raise VisaError(
                "pyvisa がインストールされていません。`pip install pyvisa` を実行してください。"
            )
        self._rm: pyvisa.ResourceManager | None = None
        # v0.4.0: リソース単位の排他ロック (同一機器への同時アクセスを直列化)
        self._locks: dict[str, asyncio.Lock] = {}
        # v0.6.0: bus 単位 semaphore (VISA I/O 中のみ保持)
        # circular import 回避のため型注釈は遅延、引数で受ける
        self._bus_manager = bus_manager

    def set_bus_manager(self, bus_manager) -> None:
        """ランタイムで BusManager を差し替え (system_config reload 対応)"""
        self._bus_manager = bus_manager

    def _get_lock(self, resource_name: str) -> asyncio.Lock:
        """resource_name ごとの asyncio.Lock を返す (なければ生成)。
        異なる機器への並列アクセスは妨げず、同一機器のみ逐次保証する。
        """
        lock = self._locks.get(resource_name)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[resource_name] = lock
        return lock

    def _get_rm(self) -> "pyvisa.ResourceManager":
        if self._rm is None:
            try:
                self._rm = pyvisa.ResourceManager()  # NI-VISA（Windowsデフォルト）
                logger.info("NI-VISA ResourceManager を初期化しました。")
            except Exception as e:
                raise VisaConnectionError(f"NI-VISA の初期化に失敗しました: {e}") from e
        return self._rm

    async def _run(self, func, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(func, *args, **kwargs))

    def backend_info(self) -> dict:
        """v2.4.0: 現在の VISA backend 情報を返す (診断用)。

        - `pyvisa_version`: pyvisa のバージョン
        - `backend`: ResourceManager の backend 識別子 (例: "@ni", "@py")
          取得できない場合は None
        - `available`: pyvisa が import できているか
        """
        info: dict = {
            "available": _PYVISA_AVAILABLE,
            "pyvisa_version": None,
            "backend": None,
        }
        if not _PYVISA_AVAILABLE:
            return info
        try:
            info["pyvisa_version"] = getattr(pyvisa, "__version__", None)
        except Exception:
            pass
        try:
            rm = self._get_rm()
            # pyvisa ResourceManager.visalib.library_path / spec で backend を推定
            visalib = getattr(rm, "visalib", None)
            if visalib is not None:
                # @ni / @py 等の識別子
                spec = getattr(visalib, "spec", None) or getattr(
                    visalib, "library_path", None)
                info["backend"] = str(spec) if spec else None
        except Exception as e:
            logger.debug("backend_info の backend 取得失敗: %s", e)
        return info

    async def list_resources(self, query: str = "?*::INSTR") -> list[str]:
        def _list():
            rm = self._get_rm()
            try:
                return list(rm.list_resources(query))
            except Exception as e:
                raise VisaError(f"リソース列挙に失敗しました: {e}") from e

        return await self._run(_list)

    async def query(
        self,
        resource_name: str,
        command: str,
        timeout_ms: int = 5000,
        read_termination: str = "\n",
        write_termination: str = "\n",
    ) -> str:
        def _query():
            rm = self._get_rm()
            try:
                res = rm.open_resource(resource_name)
                res.timeout = timeout_ms
                res.read_termination = read_termination
                res.write_termination = write_termination
                try:
                    return res.query(command)
                finally:
                    res.close()
            except pyvisa.errors.VisaIOError as e:
                if "timeout" in str(e).lower():
                    raise VisaTimeoutError(
                        f"{resource_name} がタイムアウトしました（{timeout_ms}ms）: {e}"
                    ) from e
                raise VisaConnectionError(
                    f"{resource_name} への接続に失敗しました: {e}"
                ) from e
            except Exception as e:
                raise VisaError(f"クエリ中にエラーが発生しました: {e}") from e

        # v0.6.0: bus semaphore → resource lock → I/O の順 (deadlock 回避)
        if self._bus_manager is not None:
            async with self._bus_manager.acquire(resource_name):
                async with self._get_lock(resource_name):
                    return await self._run(_query)
        else:
            async with self._get_lock(resource_name):
                return await self._run(_query)

    async def write(
        self,
        resource_name: str,
        command: str,
        timeout_ms: int = 5000,
        read_termination: str = "\n",
        write_termination: str = "\n",
    ) -> None:
        def _write():
            rm = self._get_rm()
            try:
                res = rm.open_resource(resource_name)
                res.timeout = timeout_ms
                res.read_termination = read_termination
                res.write_termination = write_termination
                try:
                    res.write(command)
                finally:
                    res.close()
            except pyvisa.errors.VisaIOError as e:
                if "timeout" in str(e).lower():
                    raise VisaTimeoutError(
                        f"{resource_name} がタイムアウトしました（{timeout_ms}ms）: {e}"
                    ) from e
                raise VisaConnectionError(
                    f"{resource_name} への接続に失敗しました: {e}"
                ) from e
            except Exception as e:
                raise VisaError(f"コマンド送信中にエラーが発生しました: {e}") from e

        if self._bus_manager is not None:
            async with self._bus_manager.acquire(resource_name):
                async with self._get_lock(resource_name):
                    await self._run(_write)
        else:
            async with self._get_lock(resource_name):
                await self._run(_write)

    async def probe_resource(
        self,
        resource_name: str,
        timeout_ms: int = 3000,
    ) -> dict:
        """v2.1.0: VISA resource を open/close するだけの安全な probe。

        **`*IDN?` / `query` / `write` は一切送らない**。open / 属性
        読み取り / close まで。VI_ERROR_SYSTEM_ERROR 等の structured
        error を返す。

        Returns:
            success / error 構造を含む dict。raise しない。
        """
        result: dict = {
            "success": False,
            "data": {
                "operation": "open_close_only",
                "resource_name": resource_name,
                "opened": False,
                "closed": False,
                "query_performed": False,
                "write_performed": False,
                "timeout_ms": timeout_ms,
            },
        }

        def _probe():
            rm = self._get_rm()
            res = None
            try:
                res = rm.open_resource(resource_name)
                opened = True
                interface_type = None
                resource_class = None
                try:
                    res.timeout = timeout_ms
                except Exception:
                    pass
                try:
                    interface_type = getattr(res, "interface_type", None)
                except Exception:
                    interface_type = None
                try:
                    resource_class = getattr(res, "resource_class", None)
                except Exception:
                    resource_class = None
                return {
                    "opened": opened,
                    "interface_type": interface_type,
                    "resource_class": resource_class,
                }
            finally:
                if res is not None:
                    try:
                        res.close()
                        result["data"]["closed"] = True
                    except Exception:
                        pass

        try:
            info = await self._run(_probe)
            result["success"] = True
            result["data"]["opened"] = bool(info.get("opened"))
            result["data"]["interface_type"] = info.get("interface_type")
            result["data"]["resource_class"] = info.get("resource_class")
        except Exception as e:
            # structured error。pyvisa.errors.VisaIOError があれば code
            # を含める
            err = {
                "error_class": "visa_open_resource_failed",
                "type": type(e).__name__,
                "message": str(e),
            }
            code = getattr(e, "error_code", None)
            if code is None and hasattr(e, "args") and e.args:
                cause = getattr(e, "__cause__", None)
                if cause is not None:
                    code = getattr(cause, "error_code", None)
            if code is not None:
                err["code"] = int(code)
            # v2.1.1: VI_ERROR_RSRC_NFOUND を専用 error_class へ昇格 +
            # device 切断系の next actions を提示
            msg_upper = str(e).upper()
            is_rsrc_nfound = (
                (code == -1073807343)
                or ("VI_ERROR_RSRC_NFOUND" in msg_upper)
            )
            if is_rsrc_nfound:
                err["error_class"] = "visa_resource_not_found"
                result["recommended_next_actions"] = [
                    "Run list_resources(query=\"USB?*\") (or the "
                    "matching interface filter) to check if the "
                    "resource is still enumerated.",
                    "Verify device power and USB / GPIB cable.",
                    "Open NI MAX and confirm the resource name "
                    "appears under the right interface.",
                    "If the device was recently disconnected and "
                    "reconnected, the VISA resource name may have "
                    "changed; re-enumerate first.",
                ]
            result["error"] = err
        return result

    @staticmethod
    def _classify_probe_status(probe_result: dict) -> str:
        """v2.5.0: probe_resource の結果を status enum に分類する。
        "ok" | "not_found" | "timeout" | "error"
        """
        if probe_result.get("success"):
            return "ok"
        err = probe_result.get("error") or {}
        ec = err.get("error_class", "")
        code = err.get("code")
        msg_upper = str(err.get("message", "")).upper()
        if ec == "visa_resource_not_found":
            return "not_found"
        if (
            "VI_ERROR_TMO" in msg_upper
            or "TIMEOUT" in msg_upper
            or (code is not None and int(code) == -1073807339)
        ):
            return "timeout"
        return "error"

    async def probe_all_safe(
        self,
        resource_names: list[str],
        *,
        timeout_ms: int = 3000,
        concurrency: int = 8,
    ) -> dict:
        """v2.5.0: 複数 resource を個別に probe して resource 単位の
        health check 結果を返す (100 台規模の一括診断)。

        各 resource は `probe_resource` (open/close のみ、`*IDN?` や
        query/write は送らない) で診断し、status enum
        ("ok"/"not_found"/"timeout"/"error") + elapsed_ms を返す。
        1 台のエラーが他の結果を捨てさせない (部分成功)。

        Args:
            resource_names: probe する VISA resource のリスト
            timeout_ms: 各 probe の timeout
            concurrency: 同時 probe 数 (GPIB バス保護のため控えめに)

        Returns:
            data.results: [{resource_name, status, elapsed_ms,
                            interface_type, resource_class, error}, ...]
            data.status_counts: {ok:N, error:N, timeout:N, not_found:N}
            data.all_ok: bool
            partial_success: 一部 ok + 一部 失敗
        """
        import time as _time

        if not resource_names:
            return {
                "success": True,
                "partial_success": False,
                "data": {
                    "results": [],
                    "status_counts": {},
                    "all_ok": True,
                    "total": 0,
                },
            }

        sem = asyncio.Semaphore(max(1, int(concurrency)))

        async def _one(resource: str) -> dict:
            async with sem:
                t0 = _time.monotonic()
                try:
                    pr = await self.probe_resource(
                        resource, timeout_ms=timeout_ms)
                except Exception as e:
                    # probe_resource は raise しない設計だが念のため
                    pr = {
                        "success": False,
                        "error": {
                            "error_class": "probe_internal_error",
                            "type": type(e).__name__,
                            "message": str(e),
                        },
                    }
                elapsed = round((_time.monotonic() - t0) * 1000.0, 1)
                status = self._classify_probe_status(pr)
                data = pr.get("data") or {}
                return {
                    "resource_name": resource,
                    "status": status,
                    "elapsed_ms": elapsed,
                    "interface_type": data.get("interface_type"),
                    "resource_class": data.get("resource_class"),
                    "error": pr.get("error"),
                }

        results = await asyncio.gather(
            *[_one(r) for r in resource_names])

        status_counts: dict[str, int] = {}
        for r in results:
            s = r["status"]
            status_counts[s] = status_counts.get(s, 0) + 1
        ok_count = status_counts.get("ok", 0)
        all_ok = ok_count == len(results)
        any_ok = ok_count > 0
        any_fail = ok_count < len(results)

        recommended: list[str] = []
        if status_counts.get("not_found"):
            recommended.append(
                "Some resources were not found (device disconnected or "
                "renamed). Re-run discover_resources_safe to re-enumerate.")
        if status_counts.get("timeout"):
            recommended.append(
                "Some resources timed out. The controller may be busy or "
                "a device is holding the bus; retry or power-cycle.")
        if status_counts.get("error"):
            recommended.append(
                "Some resources returned VISA errors. Check NI MAX and "
                "cabling for the failing resources.")

        return {
            "success": any_ok,
            "partial_success": any_ok and any_fail,
            "data": {
                "results": results,
                "status_counts": status_counts,
                "all_ok": all_ok,
                "total": len(results),
                "concurrency": int(concurrency),
                "diagnostic_schema_version": "2.5",
            },
            "recommended_next_actions": recommended,
        }

    async def discover_resources_safe(
        self,
        queries: list[str] | None = None,
    ) -> dict:
        """v2.1.0: query ごとに `list_resources` を個別実行し、
        部分成功を返す。一部 interface (例: GPIB) が異常でも、
        他 (USB) の結果は捨てない。
        """
        if not queries:
            queries = ["USB?*", "GPIB?*", "ASRL?*", "TCPIP?*"]

        def _interface_of(q: str) -> str:
            q_upper = q.upper()
            for prefix in ("USB", "GPIB", "TCPIP", "ASRL", "PXI",
                            "VXI", "FIREWIRE"):
                if q_upper.startswith(prefix):
                    return prefix
            return q_upper

        import time as _time

        per_query: list[dict] = []
        all_resources: list[dict] = []
        successful: list[str] = []
        failed: list[str] = []
        timed_out: list[str] = []

        for q in queries:
            iface = _interface_of(q)
            # v2.4.0: per-query diagnostic schema。
            # status enum: "ok" | "empty" | "timeout" | "error"
            # 旧 `success`/`resources`/`error` も残す (後方互換)。
            t0 = _time.monotonic()
            entry: dict = {
                "query": q, "interface": iface,
                "success": False, "resources": [], "error": None,
                "status": "error", "elapsed_ms": None,
            }
            try:
                resources = await self.list_resources(q)
                entry["success"] = True
                entry["resources"] = list(resources)
                entry["status"] = "ok" if resources else "empty"
                successful.append(iface)
                for r in resources:
                    all_resources.append({
                        "resource_name": r, "query": q,
                        "interface": iface,
                    })
            except Exception as e:
                err = {
                    "error_class": "visa_interface_discovery_failed",
                    "type": type(e).__name__,
                    "message": str(e),
                }
                cause = getattr(e, "__cause__", None)
                code = getattr(cause, "error_code", None) \
                    if cause else None
                if code is not None:
                    err["code"] = int(code)
                # v2.4.0: timeout を error と区別する。
                # VisaTimeoutError / VI_ERROR_TMO / "timeout" 文言で判定。
                is_timeout = (
                    isinstance(e, VisaTimeoutError)
                    or "VI_ERROR_TMO" in str(e).upper()
                    or "TIMEOUT" in str(e).upper()
                    or (code is not None and int(code) == -1073807339)
                )
                if is_timeout:
                    entry["status"] = "timeout"
                    err["error_class"] = "visa_interface_discovery_timeout"
                    timed_out.append(iface)
                else:
                    entry["status"] = "error"
                    failed.append(iface)
                entry["error"] = err
            finally:
                entry["elapsed_ms"] = round(
                    (_time.monotonic() - t0) * 1000.0, 1)
            per_query.append(entry)

        any_success = bool(successful)
        # timeout も failure 側として扱う (partial_success 判定用)
        any_failure = bool(failed) or bool(timed_out)
        partial = any_success and any_failure
        # v2.1.1: 「list_resources は成功しているが resource が 1 件も
        # 列挙されていない」状態を検出 (device 切断 / 電源 / driver
        # 未登録などの environment 起因が多い)
        total_resources = len(all_resources)
        empty_with_success = any_success and total_resources == 0

        recommended: list[str] = []
        if empty_with_success:
            recommended.append(
                "All queried interfaces returned 0 resources. The "
                "VISA backend is responding but no instruments are "
                "currently enumerated.")
            recommended.append(
                "Check device power and cable for the expected "
                "instrument(s).")
            recommended.append(
                "Open NI MAX and confirm the resource appears under "
                "Devices and Interfaces.")
            recommended.append(
                "If the device was recently re-plugged, the resource "
                "name may have changed; re-run discover_resources_safe "
                "after waiting a few seconds.")
        if any_failure:
            recommended.append(
                "Try list_resources(query=\"USB?*\") to isolate USB "
                "resources.")
            if "GPIB" in failed or "GPIB" in timed_out:
                recommended.append(
                    "Check NI-488.2 / GPIB controller if GPIB "
                    "discovery fails.")
            recommended.append(
                "Check NI MAX for the failing interface.")
            recommended.append(
                "Run pyvisa-info and verify the active VISA backend.")
        if timed_out:
            recommended.append(
                "One or more interfaces timed out. The controller may "
                "be busy or a device is holding the bus; retry after a "
                "few seconds or power-cycle the affected interface.")

        # v2.4.0: backend 情報 + interface 別 status 集計を追加。
        try:
            backend = self.backend_info()
        except Exception:
            backend = {"available": _PYVISA_AVAILABLE}

        # v2.4.1: interface ごとの status 集計 (Codex v2.4.0 レビュー P2)。
        # 同一 interface に複数 query があるとき、最後の結果で上書きすると
        # error が ok に隠れる。severity (error > timeout > empty > ok) で
        # worst を採用し、status 別カウントも併記する。
        _SEVERITY = {"error": 3, "timeout": 2, "empty": 1, "ok": 0}
        interface_status: dict[str, str] = {}
        interface_status_detail: dict[str, dict[str, int]] = {}
        for entry in per_query:
            iface = entry["interface"]
            st = entry["status"]
            # severity 優先で worst を保持
            cur = interface_status.get(iface)
            if cur is None or _SEVERITY.get(st, 0) > _SEVERITY.get(cur, 0):
                interface_status[iface] = st
            # 詳細カウント
            d = interface_status_detail.setdefault(iface, {})
            d[st] = d.get(st, 0) + 1

        return {
            "success": any_success,
            "partial_success": partial,
            "empty_with_success": empty_with_success,
            "data": {
                "resources": all_resources,
                "resource_count": total_resources,
                "queries": per_query,
                "successful_interfaces": successful,
                "failed_interfaces": failed,
                # v2.4.0 追加 (後方互換、既存 key は不変)
                "timed_out_interfaces": timed_out,
                "interface_status": interface_status,
                # v2.4.1: severity 優先集計 + status 別カウント
                "interface_status_detail": interface_status_detail,
                "backend": backend,
                "diagnostic_schema_version": "2.4.1",
            },
            "recommended_next_actions": recommended,
        }

    def close(self) -> None:
        if self._rm is not None:
            try:
                self._rm.close()
            except Exception:
                pass
            self._rm = None
