from __future__ import annotations

import asyncio
import random
import time
from pathlib import Path

from filelock import FileLock, Timeout

from .storage import Store


class StopRequested(Exception):
    pass


def task_lock(directory: Path) -> FileLock:
    return FileLock(directory / "worker.lock", timeout=0)


def is_active(directory: Path) -> bool:
    lock = task_lock(directory)
    try:
        with lock:
            return False
    except Timeout:
        return True


class Controller:
    def __init__(self, store: Store, run_id: str):
        self.store, self.run_id = store, run_id
        self.stopped = False
        self.paused = False
        self.last_heartbeat = 0.0

    async def checkpoint(self):
        while True:
            desired = self.store.control(self.run_id)
            if self.stopped or desired == "stop":
                raise StopRequested
            if desired != "pause":
                if self.paused:
                    self.store.update_run(self.run_id, status="running")
                    self.paused = False
                if time.monotonic() - self.last_heartbeat > 2:
                    self.store.update_run(self.run_id, status="running")
                    self.last_heartbeat = time.monotonic()
                return
            if not self.paused:
                self.store.update_run(self.run_id, status="paused")
                self.paused = True
            await asyncio.sleep(0.2)

    async def sleep(self, seconds: float):
        # Stop/pause is checked during every delay. Time paused does not spend this delay.
        remaining = seconds
        while remaining > 0:
            await self.checkpoint()
            interval = min(0.2, remaining)
            start = time.monotonic()
            await asyncio.sleep(interval)
            remaining -= time.monotonic() - start
        await self.checkpoint()

    async def delay(self, bounds: tuple[float, float]):
        await self.sleep(random.uniform(*bounds))
