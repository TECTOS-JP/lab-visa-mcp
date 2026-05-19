from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field


class ParameterDefinition(BaseModel):
    name: str
    description: str = ""
    type: Literal["integer", "float", "string", "enum"] = "string"
    required: bool = True
    range: list[float] | None = None       # integer/float 用 [min, max]
    choices: list[str] | None = None       # enum 用
    default: Any = None


class ReturnDefinition(BaseModel):
    type: Literal["none", "integer", "float", "boolean", "string"] = "string"
    unit: str = ""
    description: str = ""


class CommandDefinition(BaseModel):
    scpi: str
    type: Literal["query", "write"] = "query"
    description: str = ""
    parameters: list[ParameterDefinition] = Field(default_factory=list)
    returns: ReturnDefinition = Field(default_factory=ReturnDefinition)
    timeout_ms: int | None = None          # 省略時は connection.default_timeout_ms を使用


class IdentificationConfig(BaseModel):
    manufacturer_match: str = ""           # 大文字・部分一致
    model_regex: str = ""                  # 正規表現


class SerialConfig(BaseModel):
    baud_rate: int = 9600
    data_bits: int = 8
    parity: Literal["N", "E", "O"] = "N"
    stop_bits: float = 1
    flow_control: Literal["none", "xon_xoff", "rts_cts"] = "none"


class ConnectionConfig(BaseModel):
    default_timeout_ms: int = 5000
    read_termination: str = "\n"
    write_termination: str = "\n"
    serial: SerialConfig = Field(default_factory=SerialConfig)


class MetadataConfig(BaseModel):
    manufacturer: str
    model: str
    description: str = ""
    manual_ref: str = ""
    category: str = ""                     # power_supply / multimeter / oscilloscope 等


# ===== 安全制約 (v0.2.0) =====

class RatingItem(BaseModel):
    """値制約: rated/absolute_max/recommended_max を持つ単項目"""
    rated: float | None = None              # メーカ仕様値
    absolute_max: float | None = None       # 絶対最大定格 (越えると重大警告 / strict でブロック)
    recommended_max: float | None = None    # 推奨上限 (越えると注意警告のみ)
    absolute_min: float | None = None       # 下限 (符号付きパラメータ用)
    recommended_min: float | None = None
    unit: str = ""
    description: str = ""


class PreconditionCheck(BaseModel):
    """状態・順序制約: 特定コマンド実行前に満たすべき条件"""
    command: str                            # 対象コマンド名 ("set_output" など)
    when: dict[str, Any] = Field(default_factory=dict)  # パラメータ条件 {"state": ["ON", "1"]}
    requires: list[dict[str, str]] = Field(default_factory=list)
    # requires 例: [{"has_been_called": "set_voltage_protection"}]
    severity: Literal["low", "medium", "high"] = "medium"
    reason: str = ""


class HardwareProtection(BaseModel):
    """機器側の保護機能 (情報共有のみ)"""
    name: str
    description: str = ""
    related_command: str = ""               # 関連する MCP コマンド名


class SafetyConfig(BaseModel):
    """安全制約セクション"""
    ratings: dict[str, RatingItem] = Field(default_factory=dict)
    # ratings 例: {"voltage": RatingItem(rated=35, absolute_max=36.75), ...}
    preconditions: list[PreconditionCheck] = Field(default_factory=list)
    cautions: list[str] = Field(default_factory=list)
    # cautions: 自然言語の禁止行為・注意事項リスト
    hardware_protections: list[HardwareProtection] = Field(default_factory=list)


# ===== 機器仕様 (v0.2.0, 簡易版) =====

class SpecificationConfig(BaseModel):
    """機器仕様 (LLM への情報提供用、自由形式)"""
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    measurement: dict[str, Any] = Field(default_factory=dict)
    other: dict[str, Any] = Field(default_factory=dict)


# ===== 応答フォーマット (v0.2.0) =====

class ResponseFormat(BaseModel):
    """機器固有の応答フォーマット定義"""
    pattern: str                            # 正規表現 (named groups 推奨)
    description: str = ""
    fields: dict[str, dict[str, str]] = Field(default_factory=dict)
    # fields 例: {"unit": {"C": "celsius", "K": "kelvin"}}


# ===== ルート定義 =====

class InstrumentDefinition(BaseModel):
    metadata: MetadataConfig
    identification: IdentificationConfig = Field(default_factory=IdentificationConfig)
    connection: ConnectionConfig = Field(default_factory=ConnectionConfig)
    commands: dict[str, CommandDefinition] = Field(default_factory=dict)
    # v0.2.0 追加
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    specifications: SpecificationConfig = Field(default_factory=SpecificationConfig)
    response_formats: dict[str, ResponseFormat] = Field(default_factory=dict)

    @property
    def display_name(self) -> str:
        return f"{self.metadata.manufacturer} {self.metadata.model}"
