"""
Experiment IR の Step 型定義 (v0.5.0)

discriminator フィールド `type` による Pydantic discriminated union。
v0.5.0-rc1 では CommandStep と WaitStep のみ。今後のバージョンで以下を追加予定:

- QueryStep (v0.5.0+ 内部使用)
- WaitUntilStep / WaitForConditionStep / WaitForStableStep (v0.5.1)
- GroupStep / BarrierStep / StaggerStep (v0.6.x)
- SweepStep / ParallelStep / LoopStep / BranchStep (v0.8.0 DSL)
- SafeShutdownStep (v0.8.0)
"""
from __future__ import annotations
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


class CommandStep(BaseModel):
    """
    YAML 機器定義の名前付きコマンドを 1 回実行するステップ。

    `command` は機器定義の commands.<name> を参照するキー。
    `args` の値は文字列で "$" 始まりなら式評価 (recipe parameter を変数として参照)。
    `result_as` を指定すると後続ステップから ${steps.<result_as>} で参照可能 (v0.6.0+)。
    """
    type: Literal["command"] = "command"
    command: str
    args: dict[str, Any] = Field(default_factory=dict)
    result_as: str | None = None
    description: str = ""


class WaitStep(BaseModel):
    """
    指定秒数だけ待機するステップ (v0.5.0-rc1)。

    Recipe / Job 内部での待機専用。v0.5.1 で wait_until / wait_for_condition /
    wait_for_stable などの条件待機ステップが追加される予定。
    """
    type: Literal["wait"] = "wait"
    seconds: float
    description: str = ""

    def __post_init_post_parse__(self) -> None:
        # Pydantic v2 では model_validator を使うが、ここでは値域チェックを最小限
        if self.seconds < 0:
            raise ValueError(f"WaitStep.seconds は 0 以上である必要があります: {self.seconds}")


# discriminated union: type フィールドで自動的に正しいモデルが選ばれる
Step = Annotated[Union[CommandStep, WaitStep], Field(discriminator="type")]
