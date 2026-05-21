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
    # v0.5.1: polling wait (wait_for_condition / wait_for_stable) で
    # 副作用なく定期的に呼び出してよい query かどうかのヒント。
    # True の場合のみ、polling wait の command として推奨される。
    # False/未指定の場合は polling 可能だが警告メッセージに含められる。
    polling_safe: bool = False


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

    v0.5.0-rc1 から下記のステップ型をサポート:
    - **command step**: `command` を指定 (従来通り)、機器コマンドを実行
    - **wait step**: `wait: { seconds: N }` を指定、N 秒待機

    v0.5.1 で polling 系を追加:
    - **wait_until step**:        `wait_until: { timestamp: ... | seconds_from_now: ... }`
    - **wait_for_condition step**:`wait_for_condition: {...}`
    - **wait_for_stable step**:   `wait_for_stable: {...}`

    いずれか一つを必ず指定する (複数指定はエラー)。

    YAML 例:
        steps:
          - { command: "set_voltage", args: { voltage: 5 } }
          - wait: { seconds: 60 }
          - wait_for_stable:
              instrument: temp1
              command: measure_temperature
              tolerance: 0.2
              window_s: 60
              interval_s: 5
              timeout_s: 1800
          - { command: "measure_temperature" }
    """
    # command step フィールド (従来)
    command: str | None = None              # YAML commands のキーを参照
    args: dict[str, Any] = Field(default_factory=dict)
    # args の値は文字列の場合 "$varname" や "$var * 1.1" のような式評価が可能
    result_as: str | None = None            # 後続ステップから ${steps.<result_as>} で参照 (v0.6.0+ で実装)
    description: str = ""
    # wait step フィールド (v0.5.0-rc1)
    wait: dict[str, Any] | None = None      # 例: {"seconds": 60}
    # v0.5.1: polling / 絶対待機
    wait_until: dict[str, Any] | None = None
    wait_for_condition: dict[str, Any] | None = None
    wait_for_stable: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _exactly_one_step_type(self) -> "RecipeStep":
        flags = {
            "command":            self.command is not None and self.command != "",
            "wait":               self.wait is not None,
            "wait_until":         self.wait_until is not None,
            "wait_for_condition": self.wait_for_condition is not None,
            "wait_for_stable":    self.wait_for_stable is not None,
        }
        active = [k for k, v in flags.items() if v]
        if len(active) > 1:
            raise ValueError(
                f"RecipeStep には command / wait / wait_until / wait_for_condition / "
                f"wait_for_stable のうち 1 つだけを指定してください (検出: {active})"
            )
        if not active:
            raise ValueError(
                "RecipeStep には command / wait / wait_until / wait_for_condition / "
                "wait_for_stable のいずれかが必須です"
            )

        if flags["wait"]:
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

        # polling 系の dict 内容は recipe_to_plan で IR モデルに変換時に検証される。
        # ここでは必須キーの存在のみ最小限チェック。
        if flags["wait_for_condition"]:
            wfc = self.wait_for_condition
            for k in ("instrument", "command", "condition_expr"):
                if k not in wfc:
                    raise ValueError(f"wait_for_condition には '{k}' が必須です")

        if flags["wait_for_stable"]:
            wfs = self.wait_for_stable
            for k in ("instrument", "command", "tolerance", "window_s"):
                if k not in wfs:
                    raise ValueError(f"wait_for_stable には '{k}' が必須です")

        if flags["wait_until"]:
            wu = self.wait_until
            has_ts = "timestamp" in wu and wu["timestamp"]
            has_sec = "seconds_from_now" in wu and wu["seconds_from_now"] is not None
            if has_ts and has_sec:
                raise ValueError(
                    "wait_until: timestamp と seconds_from_now は排他です"
                )
            if not (has_ts or has_sec):
                raise ValueError(
                    "wait_until: timestamp または seconds_from_now のいずれかが必須です"
                )

        return self

    @property
    def step_type(self) -> str:
        """このステップ種別を返す (実行エンジン用)。"""
        if self.wait is not None: return "wait"
        if self.wait_until is not None: return "wait_until"
        if self.wait_for_condition is not None: return "wait_for_condition"
        if self.wait_for_stable is not None: return "wait_for_stable"
        return "command"


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
    # v0.5.0.2 追加: 機器固有の安全停止シーケンス
    # cancel_mode="safe_shutdown" や emergency_stop で使用される。
    # 各ステップは RecipeStep と同じ形式 (command または wait)。
    # 指定がない場合は JobManager._best_effort_safe_shutdown が
    # set_output OFF / set_voltage 0 を試みる (power_supply 系のみ妥当)。
    safe_shutdown: list[RecipeStep] = Field(default_factory=list)

    @property
    def display_name(self) -> str:
        return f"{self.metadata.manufacturer} {self.metadata.model}"
