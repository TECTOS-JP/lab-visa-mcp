# template_override (v0.8.3, experimental)

`save_experiment_template` で保存した template を
`start_experiment_job_from_template(name, override=...)` で部分上書き
実行する例。LLM が template + 差分だけで Plan を再利用できる。

## 流れ

1. `save_experiment_template(name="voltage_sweep_basic", plan=<template.json>)`
2. `start_experiment_job_from_template(name="voltage_sweep_basic",
   override=<override.json>, dry_run=true, include_expanded_plan=true)` で
   override 適用後の Plan を確認
3. dry_run=false で実行

## 許可される override キー

- `name`        新しい Job 名
- `unit`        実験対象 unit を差し替え (役割は同じ前提)
- `bindings`    role → alias の dict (key 単位の override)
- `parameters`  Plan.variables への shallow merge ({voltage} など)
- `owner`       Job owner (Plan 本体には影響しない)

## 拒否される (`error_class=validation`,
`sub_class=template_override_invalid`)

- `steps` 直接上書き
- `dsl_version` 変更
- `variables` 全体置換 (`parameters` 経由のみ可)
- `description` 直接上書き

steps を変更したいなら、`get_experiment_template` で template を取り出し、
編集してから `start_experiment_job(plan=...)` を使うこと。

## expanded_plan 例 (`include_expanded_plan=true`)

```json
{
  "dsl_version": "0.8",
  "name": "run_from_template_001",
  "unit": "unit002",
  "bindings": { "dmm": "dmm_backup_001" },
  "variables": { "voltage": 3.5, "wait_s": 1.0 },
  "steps": [ ... template と同じ ... ]
}
```

## Job metadata に残る `template_source`

```json
{
  "template_name": "voltage_sweep_basic",
  "template_version": "0.8",
  "override_json": { ... },
  "override_keys": ["name", "unit", "bindings.dmm",
                    "parameters.voltage", "parameters.wait_s"]
}
```

これは `experiment_plans.compiled_summary.template_source` と
`jobs.parameters_json.template_source` の両方に保存される。
