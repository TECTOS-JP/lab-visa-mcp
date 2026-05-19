from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime

from visa_mcp.instrument_registry import InstrumentRegistry
from visa_mcp.models.instrument_def import InstrumentDefinition
from visa_mcp.visa_manager import VisaManager
from visa_mcp.utils.idn_matcher import parse_idn

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
    def __init__(self, visa_mgr: VisaManager, registry: InstrumentRegistry) -> None:
        self._visa = visa_mgr
        self._registry = registry
        self._sessions: dict[str, InstrumentSession] = {}

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
        return session

    def get_session(self, resource_name: str) -> InstrumentSession | None:
        return self._sessions.get(resource_name)

    def list_sessions(self) -> list[dict]:
        return [s.to_dict() for s in self._sessions.values()]

    def clear_session(self, resource_name: str) -> None:
        self._sessions.pop(resource_name, None)

    def clear_all(self) -> None:
        self._sessions.clear()
