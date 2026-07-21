"""v2.3.1: instrument YAML 定義ディレクトリの resolver (副作用なし).

v2.1.4-2.2.1 までは `server.py` に `_resolve_instruments_dir()` が
あり、resolver の単体テストでも `from lab_visa_mcp import server`
すると JobManager / JobStore まで初期化される副作用があった
(Codex v2.2.1 レビュー指摘)。

このモジュールは resolver だけを切り出した純粋ロジック層で、
import しても外部状態を一切変更しない。`server.py` は引き続き
ここから import して使う (後方互換)。

優先順 (v2.1.5 で確定):
  1. `$VISA_MCP_INSTRUMENTS_DIR` 環境変数 (運用上書き)
  2. `<repo>/instruments` (利用者の運用配置 / `_system.yaml`)
  3. `<repo>/examples/instruments` (開発リポジトリのサンプル)
  4. `<pkg>/builtin_instruments` (wheel 同梱、最後の fallback)

注 (v2.1.6 以降):
- 2 / 3 の dev path 判定では `<repo>/pyproject.toml` が無いと
  dev リポジトリとみなさない (wheel install 環境で
  `<venv>/Lib/instruments` を拾うのを防ぐため)。
- `_*.yaml` のみのディレクトリは「instrument YAML 無し」扱い
  (`_system.example.yaml` / `_template.yaml` などを skip)。
"""
from __future__ import annotations
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _has_instrument_yaml(d: Path) -> bool:
    """`_` 始まりでない `*.yaml` を 1 つ以上含むか。"""
    if not d.is_dir():
        return False
    return any(
        p.name and not p.name.startswith("_")
        for p in d.glob("*.yaml")
    )


def resolve_instruments_dir(server_file: str | os.PathLike[str]) -> Path:
    """instrument YAML 定義のロード先を優先順で決定する純粋関数。

    Args:
        server_file: 通常は `server.py` の `__file__`。test では
            任意の path に差し替えられる。これにより
            `monkeypatch.setattr(srv_mod, "__file__", ...)` の代わりに
            関数引数で挙動を切り替えられ、import 副作用が無くなる。

    Returns:
        instrument YAML を含む (はずの) ディレクトリ Path。
        見つからなければ builtin の path を返す
        (registry 側で 0 件 + warning となる)。
    """
    env = os.environ.get("VISA_MCP_INSTRUMENTS_DIR", "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return p
        logger.warning(
            "VISA_MCP_INSTRUMENTS_DIR=%s は存在しません。fallback 探索に移ります",
            env)

    here = Path(server_file).resolve()
    repo_root = here.parent.parent.parent

    # v2.1.6: dev リポジトリ判定に pyproject.toml の存在を要求。
    # wheel install (`<venv>/Lib/...`) では pyproject.toml が無いので
    # builtin に確実に落ちる。
    is_dev_repo = (repo_root / "pyproject.toml").is_file()

    if is_dev_repo:
        # 利用者の運用配置 > 開発リポジトリ examples
        for cand in (
            repo_root / "instruments",
            repo_root / "examples" / "instruments",
        ):
            if _has_instrument_yaml(cand):
                return cand

    # wheel-installed default
    builtin = here.parent / "builtin_instruments"
    if builtin.is_dir():
        return builtin
    return builtin  # 不在でも builtin path を返す (registry で 0 件)
