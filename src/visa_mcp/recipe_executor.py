"""
Recipe 実行エンジン (v0.5.0-rc1 で IR ベースに refactor)

設計:
- YAML の `RecipeDefinition` を内部 IR (`experiment_ir.Plan`) に変換
- Plan を `execute_plan()` が walk して実行
- v0.5.0-rc1: CommandStep (従来の機器コマンド) + WaitStep (asyncio.sleep) のみ
- v0.5.1 以降で wait_for_* 系 step が追加されても execute_plan のディスパッチを増やすだけ

外部 API (`execute_recipe`) の戻り値形式は v0.3.0 までと互換性を維持:
- `{"success": bool, "recipe": str, "steps_executed": [...], "step_count": N}`
- 失敗時は `{"success": False, ..., "halted_at_step": idx}`

新しい標準 envelope (`response_envelope.make_envelope`) は v0.5.0+ で新規追加される
MCP ツール (Job 系等) で採用する。既存 `execute_recipe` ツールは後方互換のため従来形式。
"""
from __future__ import annotations
import asyncio
import logging
from typing import Any

from .experiment_ir import CommandStep, Plan, Step, WaitStep
from .models.instrument_def import InstrumentDefinition, RecipeDefinition, RecipeStep
from .step_executor import execute_command_step, execute_wait_step
from .utils.expression import resolve_arg, ExpressionError
from .visa_manager import VisaManager
from .session_manager import InstrumentSession

logger = logging.getLogger(__name__)


# ============================================================
# Recipe → IR Plan 変換
# ============================================================

def recipe_to_plan(recipe: RecipeDefinition, variables: dict[str, Any]) -> Plan:
    """
    YAML の RecipeDefinition + 変数辞書 → IR Plan に変換する。
    args 内の `$var` / `$var * 1.1` 等の式は事前に評価して具体値にする。

    式評価エラーが起きると ExpressionError が propagate するので、呼び出し側で捕捉する。
    """
    plan_steps: list[Step] = []
    for rs in recipe.steps:
        if rs.step_type == "wait":
            seconds_raw = rs.wait["seconds"]
            seconds = float(resolve_arg(seconds_raw, variables))
            plan_steps.append(WaitStep(
                seconds=seconds,
                description=rs.description,
            ))
        else:  # command
            resolved_args = {k: resolve_arg(v, variables) for k, v in rs.args.items()}
            plan_steps.append(CommandStep(
                command=rs.command or "",
                args=resolved_args,
                result_as=rs.result_as,
                description=rs.description,
            ))

    return Plan(
        name=(recipe.description[:80] if recipe.description else "recipe"),
        parameters=dict(variables),
        steps=plan_steps,
    )


# ============================================================
# Plan executor (各 Step type を dispatch)
# ============================================================

async def execute_plan(
    visa: VisaManager,
    session: InstrumentSession,
    plan: Plan,
    recipe_name: str | None = None,
    override_safety: bool = False,
    override_reason: str = "",
) -> dict:
    """
    IR Plan を実行する。返り値の形式は execute_recipe と同じ (後方互換)。
    """
    if session.definition is None:
        return {
            "success": False,
            "recipe": recipe_name or plan.name,
            "error": "NoDefinitionFound",
            "message": "機器定義が読み込まれていません",
            "steps_executed": [],
        }

    step_results: list[dict] = []

    for idx, step in enumerate(plan.steps):
        if isinstance(step, WaitStep):
            result = await execute_wait_step(step)
        elif isinstance(step, CommandStep):
            result = await execute_command_step(
                visa, session, step,
                override_safety=override_safety,
                override_reason=override_reason,
            )
        else:
            # 将来 step type 追加時に備えた fallback
            result = {
                "success": False,
                "error": "UnsupportedStepType",
                "step_type": getattr(step, "type", "unknown"),
                "message": "未対応のステップ型です",
            }

        step_results.append({"step": idx, **result})

        if not result.get("success", False):
            return {
                "success": False,
                "recipe": recipe_name or plan.name,
                "steps_executed": step_results,
                "halted_at_step": idx,
            }

    return {
        "success": True,
        "recipe": recipe_name or plan.name,
        "steps_executed": step_results,
        "step_count": len(step_results),
    }


# ============================================================
# 公開エントリポイント (既存 API、後方互換維持)
# ============================================================
# 個別 step 実行ロジックは v0.5.0.1 で step_executor.py に切り出し済み。
# このモジュールは Recipe 単位の orchestration のみを担当する。

async def execute_recipe(
    visa: VisaManager,
    session: InstrumentSession,
    recipe_name: str,
    parameters: dict[str, Any] | None,
    override_safety: bool = False,
    override_reason: str = "",
) -> dict:
    """
    指定の recipe を実行する。

    v0.5.0-rc1 で内部実装を IR Plan ベースに refactor したが、戻り値形式は v0.3.0/v0.4.x と同一。
    """
    parameters = parameters or {}

    if session.definition is None:
        return {
            "success": False,
            "error": "NoDefinitionFound",
            "message": "機器定義が読み込まれていません",
        }

    recipe: RecipeDefinition | None = session.definition.recipes.get(recipe_name)
    if recipe is None:
        return {
            "success": False,
            "error": "RecipeNotFound",
            "message": f"recipe '{recipe_name}' は定義されていません",
            "available_recipes": list(session.definition.recipes.keys()),
        }

    # パラメータ検証 (簡易: 必須チェックのみ)
    for p in recipe.parameters:
        if p.required and p.name not in parameters and p.default is None:
            return {
                "success": False,
                "error": "MissingParameter",
                "message": f"必須パラメータ '{p.name}' が指定されていません",
            }
    # default 適用
    variables = dict(parameters)
    for p in recipe.parameters:
        if p.name not in variables and p.default is not None:
            variables[p.name] = p.default

    # Recipe → IR Plan 変換
    try:
        plan = recipe_to_plan(recipe, variables)
    except ExpressionError as e:
        return {
            "success": False,
            "recipe": recipe_name,
            "error": "ExpressionError",
            "message": str(e),
            "steps_executed": [],
        }

    # Plan 実行
    return await execute_plan(
        visa, session, plan,
        recipe_name=recipe_name,
        override_safety=override_safety,
        override_reason=override_reason,
    )
