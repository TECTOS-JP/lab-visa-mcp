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
                # __cause__ 経由のことが多い
                cause = getattr(e, "__cause__", None)
                if cause is not None:
                    code = getattr(cause, "error_code", None)
            if code is not None:
                err["code"] = int(code)
            result["error"] = err
        return result

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

        per_query: list[dict] = []
        all_resources: list[dict] = []
        successful: list[str] = []
        failed: list[str] = []

        for q in queries:
            iface = _interface_of(q)
            entry: dict = {
                "query": q, "interface": iface,
                "success": False, "resources": [], "error": None,
            }
            try:
                resources = await self.list_resources(q)
                entry["success"] = True
                entry["resources"] = list(resources)
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
                entry["error"] = err
                failed.append(iface)
            per_query.append(entry)

        any_success = bool(successful)
        any_failure = bool(failed)
        partial = any_success and any_failure

        recommended: list[str] = []
        if any_failure:
            recommended.append(
                "Try list_resources(query=\"USB?*\") to isolate USB "
                "resources.")
            if "GPIB" in failed:
                recommended.append(
                    "Check NI-488.2 / GPIB controller if GPIB "
                    "discovery fails.")
            recommended.append(
                "Check NI MAX for the failing interface.")
            recommended.append(
                "Run pyvisa-info and verify the active VISA backend.")

        return {
            "success": any_success,
            "partial_success": partial,
            "data": {
                "resources": all_resources,
                "queries": per_query,
                "successful_interfaces": successful,
                "failed_interfaces": failed,
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
