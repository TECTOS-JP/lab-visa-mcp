from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from visa_mcp.instrument_registry import InstrumentRegistry
from visa_mcp.models.instrument_def import InstrumentDefinition
from visa_mcp.visa_manager import VisaManager
from visa_mcp.utils.idn_matcher import parse_idn

if TYPE_CHECKING:
    from visa_mcp.session_store import SessionStore

logger = logging.getLogger(__name__)


@dataclass
class InstrumentSession:
    resource_name: str
    idn_response: str
    idn_parsed: dict
    definition: InstrumentDefinition | None
    identified_at: datetime = field(default_factory=datetime.now)
    # v0.2.0: 安全制約の前提条件チェック用にコマンド実行履歴を保持
    command_history: list[str] = field(default_factory=list)

    def record_command(self, command_name: str) -> None:
        """成功したコマンド名を履歴に追加 (preconditions チェック用)"""
        self.command_history.append(command_name)

    def to_dict(self) -> dict:
        return {
            "resource_name": self.resource_name,
            "idn_response": self.idn_response,
            "manufacturer": self.idn_parsed.get("manufacturer", ""),
            "model": self.idn_parsed.get("model", ""),
            "serial": self.idn_parsed.get("serial", ""),
            "firmware": self.idn_parsed.get("firmware", ""),
            "definition_loaded": self.definition is not None,
            "definition_name": self.definition.display_name if self.definition else None,
            "available_commands": list(self.definition.commands.keys()) if self.definition else [],
            "identified_at": self.identified_at.isoformat(),
        }


class SessionManager:
    def __init__(
        self,
        visa_mgr: VisaManager,
        registry: InstrumentRegistry,
        *,
        store: "SessionStore | None" = None,
    ) -> None:
        """v2.3.0: `store` を渡すと bindings を JSON ファイルに永続化し、
        起動時に auto-restore する。`store=None` なら従来の in-memory only。
        """
        self._visa = visa_mgr
        self._registry = registry
        self._sessions: dict[str, InstrumentSession] = {}
        self._store = store
        if store is not None:
            self._restore_from_store()

    def _restore_from_store(self) -> None:
        """v2.3.0: SessionStore から bindings を読み込み、registry に対して
        definition を再 lookup して in-memory session を構築する。
        definition が見つからない record も skip するが store からは
        消さない (後で registry が更新されたら再 restore される)。
        """
        store = self._store
        if store is None:
            return
        try:
            records = store.load()
        except Exception as e:
            logger.warning("session store からの restore 失敗: %s", e)
            return
        restored = 0
        missing_def = 0
        for resource, rec in records.items():
            manufacturer = rec.get("manufacturer", "")
            model = rec.get("model", "")
            bind_method = rec.get("bind_method", "manual")
            defn = self._registry.get_definition(manufacturer, model)
            if defn is None:
                missing_def += 1
                logger.warning(
                    "restore skip: %s (%s / %s) の definition が "
                    "registry に無い", resource, manufacturer, model)
                continue
            idn_response = rec.get("idn_response", "") or (
                "<restored from persisted binding>")
            try:
                identified_at = datetime.fromisoformat(rec["bound_at"])
            except Exception:
                identified_at = datetime.now()
            session = InstrumentSession(
                resource_name=resource,
                idn_response=idn_response,
                idn_parsed={
                    "manufacturer": manufacturer,
                    "model": model,
                    "serial": rec.get("serial", "") or "",
                    "firmware": rec.get("firmware", "") or "",
                },
                definition=defn,
                identified_at=identified_at,
            )
            self._sessions[resource] = session
            restored += 1
        if restored or missing_def:
            logger.info(
                "session store restore: %d 件復元, %d 件 definition 不在",
                restored, missing_def)

    def bind_manually(
        self,
        resource_name: str,
        manufacturer: str,
        model: str,
    ) -> InstrumentSession | None:
        """
        *IDN? 非対応機器向けに、resource_name と定義を手動で紐付ける。
        定義が見つからない場合は None を返す。
        """
        defn = self._registry.get_definition(manufacturer, model)
        if defn is None:
            return None

        session = InstrumentSession(
            resource_name=resource_name,
            idn_response="<manual binding>",
            idn_parsed={
                "manufacturer": manufacturer,
                "model": model,
                "serial": "",
                "firmware": "",
            },
            definition=defn,
        )
        self._sessions[resource_name] = session
        logger.info("手動バインド: %s → %s", resource_name, defn.display_name)
        # v2.3.0: 永続化
        if self._store is not None:
            try:
                self._store.upsert(
                    resource_name,
                    manufacturer=manufacturer,
                    model=model,
                    bind_method="manual",
                )
            except Exception as e:
                logger.warning("bind_manually の persist 失敗: %s", e)
        return session

    async def identify(self, resource_name: str) -> InstrumentSession:
        """
        *IDN? クエリを送り、YAML定義と照合してセッションを登録する。
        *IDN? に非対応の機器はタイムアウトするため、その場合は idn_response を空文字とする。
        """
        try:
            idn = await self._visa.query(resource_name, "*IDN?", timeout_ms=3000)
        except Exception as e:
            logger.warning("%s の *IDN? 失敗: %s", resource_name, e)
            idn = ""

        parsed = parse_idn(idn) if idn else {}
        defn = self._registry.match_idn(idn) if idn else None

        session = InstrumentSession(
            resource_name=resource_name,
            idn_response=idn,
            idn_parsed=parsed,
            definition=defn,
        )
        self._sessions[resource_name] = session
        logger.info(
            "識別完了: %s → %s",
            resource_name,
            defn.display_name if defn else "未識別",
        )
        # v2.3.0: 永続化 (definition が解決できた場合のみ)
        # registry の YAML metadata 表記 (case 統一) を使って persist
        # する。IDN response の "KIKUSUI" と YAML の "Kikusui" の
        # case mismatch で restore 時に definition lookup が失敗する
        # 問題を回避する。
        if self._store is not None and defn is not None:
            try:
                self._store.upsert(
                    resource_name,
                    manufacturer=defn.metadata.manufacturer,
                    model=defn.metadata.model,
                    bind_method="identify",
                    idn_response=idn,
                )
            except Exception as e:
                logger.warning("identify の persist 失敗: %s", e)
        return session

    def get_session(self, resource_name: str) -> InstrumentSession | None:
        return self._sessions.get(resource_name)

    def list_sessions(self) -> list[dict]:
        return [s.to_dict() for s in self._sessions.values()]

    def clear_session(self, resource_name: str) -> dict:
        """Resource を in-memory + store から削除する。

        v2.3.3: 返り値に `{"removed_from_in_memory": bool,
        "removed_from_store": bool}` を返す。`removed_from_store` は
        `SessionStore.remove()` の戻り値 (disk 再読込後の実際の削除
        結果) なので、別 process が同じ sessions.json に追加した
        record も正しく検出できる (Codex v2.3.2 レビュー P2)。
        """
        in_mem = self._sessions.pop(resource_name, None) is not None
        store_removed = False
        if self._store is not None:
            try:
                store_removed = bool(self._store.remove(resource_name))
            except Exception as e:
                logger.warning("clear_session の persist 失敗: %s", e)
        return {
            "removed_from_in_memory": in_mem,
            "removed_from_store": store_removed,
        }

    def clear_all(self) -> None:
        """**全 binding を消す** (in-memory + persisted store)。

        通常運用ではほぼ呼ばない (admin/test 用)。
        定義 YAML reload のように in-memory だけ捨てたい場合は
        `reload_in_memory_sessions()` を使うこと (v2.3.2)。
        """
        self._sessions.clear()
        if self._store is not None:
            try:
                self._store.clear_all()
            except Exception as e:
                logger.warning("clear_all の persist 失敗: %s", e)

    def clear_in_memory(self) -> None:
        """v2.3.2: persisted store を残したまま in-memory session 辞書
        だけクリアする。definition YAML reload 時の用途。"""
        self._sessions.clear()

    def reload_in_memory_sessions(self) -> None:
        """v2.3.2: definition reload 後に in-memory session を捨てて
        store から再 restore する。新しい definition で binding が
        更新される。persisted store の record は触らない。
        """
        self._sessions.clear()
        if self._store is not None:
            self._restore_from_store()

    # v2.3.0: store accessor (for tests / advanced ops)
    @property
    def store(self):
        return self._store
