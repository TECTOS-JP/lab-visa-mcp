"""
v0.6.0 BusManager ── バス単位の同時アクセス制限

VisaManager の query/write 直前で acquire され、I/O 終了時に release。
**Job 全体ではなく VISA 通信中のみ** 保持される (実装方針 #5)。

設計:
  - bus 名 → asyncio.Semaphore (max_concurrency)
  - 未登録 bus は self._unbounded (大きな値) を返す (デフォルト 1024)
  - resource_name → bus 推定: SystemConfig.bus_of()

resource_name のみが与えられた場合 (legacy session、system_config 未登録の機器) は、
GPIB なら bus 名 "GPIB0" 等を推定し、それ以外は None (semaphore なし) を返す。

Deadlock 回避:
  bus semaphore は VISA I/O のみで保持され、ネストして取らない。
  resource lock (ResourceScheduler) は Job 全体、bus semaphore は通信瞬間のみで
  保持タイミングが完全に分離しているため deadlock しない。
"""
from __future__ import annotations
import asyncio
import contextlib
import logging
from typing import AsyncIterator

from lab_visa_mcp.system_config import SystemConfig

logger = logging.getLogger(__name__)


class BusManager:
    def __init__(self, system_config: SystemConfig | None = None) -> None:
        self._system_config = system_config or SystemConfig()
        # bus 名 → Semaphore (lazy 生成)
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        # 統計 (debug 用)
        self._acquired_counts: dict[str, int] = {}

    def set_system_config(self, system_config: SystemConfig) -> None:
        """ランタイムで system_config をスワップ (reload 対応)

        **注意 (v0.6.0.1)**: 既存セマフォは保持される (リファレンスを抱えている
        呼び出し元があるため)。よって reload 後に bus の max_concurrency を
        変更しても、**既存 bus 名のセマフォは古い並列数のまま残る**。
        変更を確実に反映するにはサーバ再起動が必要。
        新規 bus 名 (まだ acquire されていないもの) は次回 acquire 時に
        新しい設定で生成される。
        """
        # 既存セマフォは保持 (リファレンスを抱えている呼び出し元があるため)
        self._system_config = system_config

    def bus_for_resource(self, resource_name: str) -> str | None:
        """resource_name の所属 bus 名 (なければ None)"""
        return self._system_config.bus_of(resource_name)

    def _get_sem(self, bus_name: str) -> asyncio.Semaphore:
        """bus 名に対する Semaphore を取得 (lazy)"""
        if bus_name not in self._semaphores:
            cfg = self._system_config.buses.get(bus_name)
            if cfg is not None:
                max_c = cfg.max_concurrency
            elif bus_name.upper().startswith("GPIB"):
                # GPIB はデフォルト 1
                max_c = 1
            else:
                # 不明な bus は大きめ (実質制限なし)
                max_c = 64
            self._semaphores[bus_name] = asyncio.Semaphore(max_c)
            logger.debug("BusManager: created semaphore for bus=%s, max=%d", bus_name, max_c)
        return self._semaphores[bus_name]

    @contextlib.asynccontextmanager
    async def acquire(self, resource_name: str) -> AsyncIterator[str | None]:
        """
        resource_name の所属 bus semaphore を acquire/release。

        bus が判定できなければ semaphore を取らず素通し (None を yield)。
        """
        bus = self.bus_for_resource(resource_name)
        if bus is None:
            yield None
            return
        sem = self._get_sem(bus)
        async with sem:
            self._acquired_counts[bus] = self._acquired_counts.get(bus, 0) + 1
            try:
                yield bus
            finally:
                pass

    def snapshot(self) -> dict:
        """テスト・debug 用に状態スナップショット"""
        return {
            "buses": {
                name: {
                    "max_concurrency": (
                        self._system_config.buses[name].max_concurrency
                        if name in self._system_config.buses
                        else (1 if name.upper().startswith("GPIB") else 64)
                    ),
                    "currently_acquired_count": (
                        # Python の Semaphore は内部 _value 公開していないので
                        # 取得済み回数のみを概算で返す
                        self._acquired_counts.get(name, 0)
                    ),
                }
                for name in self._semaphores
            },
        }
