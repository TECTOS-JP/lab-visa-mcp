from __future__ import annotations
from fastmcp import FastMCP
from visa_mcp.session_manager import SessionManager
from visa_mcp.visa_manager import VisaError


def register_tools(mcp: FastMCP, session_mgr: SessionManager) -> None:

    @mcp.tool()
    async def list_resources(query: str = "?*::INSTR") -> dict:
        """
        接続されている全 VISA リソースを列挙する。
        GPIB アドレス（GPIB0::N::INSTR）とシリアルポート（ASRL3::INSTR）を含む。

        query: VISA リソースフィルタ文字列（デフォルト: 全機器）。
            interface 別に絞り込み可:
            - "USB?*"   : USB のみ
            - "GPIB?*"  : GPIB のみ
            - "TCPIP?*" : TCP/IP のみ
            - "ASRL?*"  : シリアル のみ

        全件列挙が一部 interface (例: GPIB) の異常で失敗する場合は、
        query を絞って試すか、`discover_resources_safe` を使うこと。
        本 tool は VISA resource の列挙のみで、`*IDN?` / `query` /
        `write` は一切送らない。
        """
        try:
            resources = await session_mgr._visa.list_resources(query)
            return {"success": True, "data": {"resources": resources, "count": len(resources)}}
        except VisaError as e:
            return {"success": False, "error": type(e).__name__, "message": str(e)}

    @mcp.tool()
    async def identify_instrument(resource_name: str) -> dict:
        """
        指定の VISA リソースに *IDN? クエリを送り機器を識別する。
        登録済み YAML 定義と照合し、利用可能なコマンド一覧を返す。
        resource_name: VISA リソース文字列（例: "GPIB0::1::INSTR", "ASRL3::INSTR"）
        """
        try:
            session = await session_mgr.identify(resource_name)
            # v2.3.4: persist 結果を expose (Codex v2.3.3 P1)。
            data = session.to_dict()
            data["persisted"] = (
                session.persisted if session.persisted is not None
                else True)
            if session.persist_error:
                data["persist_error"] = session.persist_error
            return {"success": True, "data": data}
        except VisaError as e:
            return {"success": False, "error": type(e).__name__, "message": str(e), "resource_name": resource_name}

    @mcp.tool()
    async def probe_resource(
        resource_name: str,
        timeout_ms: int = 3000,
    ) -> dict:
        """
        v2.1.0: VISA resource を open / close するだけの安全な疎通確認。

        **`*IDN?` / `query` / `write` は一切送らない**。`open_resource`
        → 属性読み取り (interface_type / resource_class) → `close` まで
        の最小限。VI_ERROR_SYSTEM_ERROR 等の structured error を返す
        (raise しない)。

        resource_name: VISA リソース文字列 (例:
            "USB0::0x0B3E::0x1029::ZM000463::INSTR")
        timeout_ms: open 後に設定する timeout (default 3000ms)

        Returns:
            success / data.{opened, closed, query_performed,
            write_performed, interface_type, resource_class,
            timeout_ms} / error.{error_class, type, code, message}
        """
        return await session_mgr._visa.probe_resource(
            resource_name, timeout_ms=timeout_ms,
        )

    @mcp.tool()
    async def discover_resources_safe(
        queries: list[str] | None = None,
    ) -> dict:
        """
        v2.1.0: 複数 VISA query を個別に試行し、部分成功を返す
        discovery tool。全件列挙が一部 interface (例: GPIB) の異常で
        失敗しても、他 (USB / TCPIP) の結果を捨てない。

        queries: 試行する VISA filter のリスト。default は
            ["USB?*", "GPIB?*", "ASRL?*", "TCPIP?*"]。

        Returns:
            success: 1 つでも query が成功すれば True
            partial_success: 一部成功 + 一部失敗
            data.resources: 全成功 query の resource を集約
            data.queries: query 別の success / resources / error
            data.successful_interfaces / failed_interfaces
            recommended_next_actions: GPIB 異常時等の推奨対応

        `*IDN?` / `query` / `write` は一切送らない。
        """
        return await session_mgr._visa.discover_resources_safe(
            queries=queries,
        )

    @mcp.tool()
    async def identify_all_instruments(
        query: str = "?*::INSTR",
    ) -> dict:
        """
        全 VISA リソースに *IDN? クエリを送り一括識別する。
        識別できた機器と未識別機器の一覧を返す。

        query: VISA リソースフィルタ (v2.1.0 で追加)。一部 interface
            だけ識別したい場合に使う。例: "USB?*" で USB のみ。
            全件列挙が GPIB 異常で失敗する環境では、`USB?*` などに
            絞ると安全。
        """
        try:
            resources = await session_mgr._visa.list_resources(query)
        except VisaError as e:
            return {"success": False, "error": type(e).__name__, "message": str(e)}

        identified = []
        unidentified = []

        for resource_name in resources:
            try:
                session = await session_mgr.identify(resource_name)
                if session.definition is not None:
                    identified.append(session.to_dict())
                else:
                    unidentified.append(session.to_dict())
            except VisaError as e:
                unidentified.append({
                    "resource_name": resource_name,
                    "error": type(e).__name__,
                    "message": str(e),
                })

        return {
            "success": True,
            "data": {
                "identified": identified,
                "unidentified": unidentified,
                "total": len(resources),
            },
        }

    @mcp.tool()
    async def list_identified_instruments() -> dict:
        """
        現在のセッションで識別済みの機器一覧と、
        各機器で利用可能なコマンド名を返す。
        v2.3.0: bindings は process 再起動を跨いで永続化される。
        起動時に SessionStore から auto-restore された sessions も含む。
        """
        sessions = session_mgr.list_sessions()
        return {"success": True, "data": {"sessions": sessions, "count": len(sessions)}}

    @mcp.tool()
    async def clear_persisted_binding(resource_name: str) -> dict:
        """**(experimental, v2.3.0)** 永続化された binding を削除する。

        process 再起動後も復元されないよう、~/.visa-mcp/sessions.json
        (または VISA_MCP_SESSION_STORE) からも除去する。同時に
        in-memory session も clear する。

        v2.3.3: `removed_from_store` を `SessionStore.remove()` の
        戻り値 (disk 再読込後の実際の削除結果) で判定する
        (Codex v2.3.2 レビュー P2)。これにより別 process が同じ
        sessions.json に追加した record も正しく検出できる。

        Args:
            resource_name: 削除する VISA resource (例: "GPIB0::2::INSTR")

        Returns:
            data.removed: bool (in-memory または store に存在して削除した場合 True)
            data.removed_from_in_memory: bool (in-memory session があったか)
            data.removed_from_store: bool (store.remove() が True を返したか)
            data.resource_name: 対象 resource
            data.remaining_sessions: 現在残っている in-memory session 数
        """
        outcome = session_mgr.clear_session(resource_name)
        in_mem = bool(outcome.get("removed_from_in_memory"))
        store_removed = bool(outcome.get("removed_from_store"))
        store_error = outcome.get("store_error")
        # v2.3.4: store 削除エラー (lock timeout 等) があれば
        # tool 自体を success=False で返す (Codex v2.3.3 P2)。
        # 再起動後に binding が復活する可能性を caller に通知。
        if store_error:
            return {
                "success": False,
                "error": "PersistedBindingClearFailed",
                "message": (
                    f"in-memory session は clear したが store からの "
                    f"削除に失敗しました ({store_error})。"
                    f"再起動後 binding が復活する可能性があります。"),
                "data": {
                    "removed": in_mem,
                    "removed_from_in_memory": in_mem,
                    "removed_from_store": False,
                    "store_error": store_error,
                    "resource_name": resource_name,
                    "remaining_sessions": len(session_mgr.list_sessions()),
                },
            }
        return {
            "success": True,
            "data": {
                "removed": in_mem or store_removed,
                "removed_from_in_memory": in_mem,
                "removed_from_store": store_removed,
                "store_error": None,
                "resource_name": resource_name,
                "remaining_sessions": len(session_mgr.list_sessions()),
            },
        }

    @mcp.tool()
    async def list_commands(resource_name: str) -> dict:
        """
        識別済み機器の利用可能なコマンド一覧と説明を返す。
        execute_named_command で使用可能な command_name を確認するために使う。
        resource_name: VISA リソース文字列
        """
        session = session_mgr.get_session(resource_name)
        if session is None:
            return {
                "success": False,
                "error": "SessionNotFound",
                "message": f"{resource_name} はまだ識別されていません。identify_instrument を先に実行してください。",
            }
        if session.definition is None:
            return {
                "success": False,
                "error": "NoDefinitionFound",
                "message": f"{resource_name} の YAML 定義が見つかりませんでした（IDN: {session.idn_response!r}）。汎用コマンドは query_instrument / send_command で送信できます。",
            }

        commands = {}
        for name, cmd in session.definition.commands.items():
            params = [
                {"name": p.name, "type": p.type, "required": p.required, "description": p.description}
                for p in cmd.parameters
            ]
            commands[name] = {
                "description": cmd.description,
                "type": cmd.type,
                "parameters": params,
                "returns": {"type": cmd.returns.type, "unit": cmd.returns.unit},
            }

        return {
            "success": True,
            "data": {
                "resource_name": resource_name,
                "instrument": session.definition.display_name,
                "commands": commands,
            },
        }

    @mcp.tool()
    async def list_available_definitions() -> dict:
        """
        instruments/ にロード済みの全機器定義を一覧する。
        bind_definition の引数（manufacturer / model）を確認するために使う。
        """
        defs = session_mgr._registry.list_definitions()
        return {"success": True, "data": {"definitions": defs, "count": len(defs)}}

    @mcp.tool()
    async def bind_definition(
        resource_name: str,
        manufacturer: str,
        model: str,
    ) -> dict:
        """
        *IDN? 非対応の機器に対し、resource_name と機器定義を手動で紐付ける。
        identify_instrument で識別できない古い機器（Yokogawa 7563 等）で使用する。
        resource_name: VISA リソース文字列（例: "GPIB0::1::INSTR"）
        manufacturer: list_available_definitions で確認できるメーカー名
        model: list_available_definitions で確認できるモデル名
        """
        session = session_mgr.bind_manually(resource_name, manufacturer, model)
        if session is None:
            available = session_mgr._registry.list_definitions()
            return {
                "success": False,
                "error": "DefinitionNotFound",
                "message": f"'{manufacturer}' / '{model}' に一致する定義が見つかりません。",
                "available_definitions": available,
            }
        # v2.3.4: persist 結果を response に含める (Codex v2.3.3 P1)。
        # in-memory session 作成は成功しているが、永続化が lock timeout
        # 等で失敗した場合は `persisted=False` + 詳細を返す。
        # tool 自体は in-memory bind 成功なので success=True 維持。
        data = session.to_dict()
        data["persisted"] = (
            session.persisted if session.persisted is not None
            else True  # store 無効環境では in-memory only として True 扱い
        )
        if session.persist_error:
            data["persist_error"] = session.persist_error
        return {"success": True, "data": data}

    @mcp.tool()
    async def reload_definitions() -> dict:
        """
        instruments/ フォルダの YAML 定義ファイルを再読み込みする。
        新しい機器定義ファイルを追加した後に呼び出す。

        v2.3.2: persisted bindings (~/.visa-mcp/sessions.json) は
        保持される。reload 後 in-memory session を捨てて新 definition
        で store から再 restore する。100 台規模で reload しても
        保存済み binding は失われない (Codex v2.3.1 レビュー P1)。
        """
        count = session_mgr._registry.reload()
        before = len(session_mgr.list_sessions())
        session_mgr.reload_in_memory_sessions()
        after = len(session_mgr.list_sessions())
        return {
            "success": True,
            "data": {
                "message": (
                    f"{count} 件の定義を再ロードしました。"
                    f"persisted bindings は保持されました "
                    f"(before={before}, restored={after})。"
                ),
                "definition_count": count,
                "sessions_before_reload": before,
                "sessions_after_reload": after,
            },
        }
