from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator


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
    format: str = ""                       # v0.3.0: response_formats のキーを参照


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


# ===== Recipe / 物理インタフェース / 動作状態 (v0.3.0) =====

class RecipeStep(BaseModel):
    """
    recipe の 1 ステップ。

    v0.5.0-rc1 から下記 2 種類のステップ型をサポート:
    - **command step**: `command` を指定 (従来通り)、機器コマンドを実行
    - **wait step**: `wait: { seconds: N }` を指定、N 秒待機 (新規)

    どちらか一方を必ず指定する (両方指定や両方未指定はエラー)。

    YAML 例:
        steps:
          - { command: "set_voltage", args: { voltage: 5 } }
          - wait: { seconds: 60 }
          - { command: "measure_voltage" }
    """
    # command step フィールド (従来)
    command: str | None = None              # YAML commands のキーを参照
    args: dict[str, Any] = Field(default_factory=dict)
    # args の値は文字列の場合 "$varname" や "$var * 1.1" のような式評価が可能
    result_as: str | None = None            # 後続ステップから ${steps.<result_as>} で参照 (v0.6.0+ で実装)
    description: str = ""
    # wait step フィールド (v0.5.0-rc1)
    wait: dict[str, Any] | None = None      # 例: {"seconds": 60}

    @model_validator(mode="after")
    def _exactly_one_step_type(self) -> "RecipeStep":
        has_command = self.command is not None and self.command != ""
        has_wait = self.wait is not None
        if has_command and has_wait:
            raise ValueError(
                "RecipeStep には command と wait の両方を指定できません (どちらか一方のみ)"
            )
        if not has_command and not has_wait:
            raise ValueError(
                "RecipeStep には command または wait のいずれかを指定する必要があります"
            )
        # wait の中身を最小限検証
        # seconds は数値リテラル、または "$var" / "$var * 1.1" 形式の式文字列を許容。
        # 式の場合の実値検証は recipe_executor.recipe_to_plan で式評価時に行う。
        if has_wait:
            if "seconds" not in self.wait:
                raise ValueError("wait step には seconds が必須です")
            sec = self.wait["seconds"]
            if isinstance(sec, str):
                if not sec.startswith("$"):
                    raise ValueError(
                        f"wait.seconds は数値、または '$' で始まる式文字列である必要があります: {sec!r}"
                    )
            else:
                try:
                    sec_f = float(sec)
                    if sec_f < 0:
                        raise ValueError("wait.seconds は 0 以上である必要があります")
                except (TypeError, ValueError) as e:
                    raise ValueError(f"wait.seconds は数値である必要があります: {e}")
        return self

    @property
    def step_type(self) -> str:
        """このステップが command か wait かを返す (実行エンジン用)。"""
        return "wait" if self.wait is not None else "command"


class RecipeDefinition(BaseModel):
    """複数コマンドを安全な順序で実行する典型ワークフロー"""
    description: str = ""
    parameters: list[ParameterDefinition] = Field(default_factory=list)
    steps: list[RecipeStep] = Field(default_factory=list)


class PhysicalTerminal(BaseModel):
    """物理端子の情報"""
    label: str
    type: str = ""                          # banana_jack / BNC / GPIB-24pin / USB-B 等
    color: str = ""                         # red / black / yellow 等
    max_voltage_to_gnd: float | None = None
    description: str = ""


class PhysicalInterface(BaseModel):
    """物理コネクタ・端子情報"""
    front_panel: list[PhysicalTerminal] = Field(default_factory=list)
    rear_panel: list[PhysicalTerminal] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class OperationalMode(BaseModel):
    """動作モード (例: CV/CC、Local/Remote)"""
    name: str
    description: str = ""
    indicator: str = ""                     # 状態を確認する SCPI クエリ・bit など


class OperationalStates(BaseModel):
    """状態機械・推奨手順"""
    startup_sequence: list[str] = Field(default_factory=list)
    shutdown_sequence: list[str] = Field(default_factory=list)
    modes: list[OperationalMode] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


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
    # v0.3.0 追加
    recipes: dict[str, RecipeDefinition] = Field(default_factory=dict)
    operational_states: OperationalStates = Field(default_factory=OperationalStates)
    physical_interface: PhysicalInterface = Field(default_factory=PhysicalInterface)

    @property
    def display_name(self) -> str:
        return f"{self.metadata.manufacturer} {self.metadata.model}"
