import json
import os
import aiofiles
from pathlib import Path
from typing import Any, Optional
import asyncio
import psutil
import time


class StateManager:
    """A simple file-based key-value state manager with system metrics."""

    def __init__(self, state_file: Optional[Path] = None):
        self.state_file = state_file or Path.home() / ".client" / "state.json"
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[Any]:
        """Retrieve a value from the state file."""
        async with self._lock:
            if not self.state_file.exists():
                return None
            async with aiofiles.open(self.state_file, "r") as f:
                try:
                    data = json.loads(await f.read())
                    return data.get(key)
                except json.JSONDecodeError:
                    return None  # Corrupted file, treat as empty

    async def set(self, key: str, value: Any):
        """Set a value in the state file."""
        async with self._lock:
            data = {}
            if self.state_file.exists():
                async with aiofiles.open(self.state_file, "r") as f:
                    try:
                        data = json.loads(await f.read())
                    except json.JSONDecodeError:
                        pass  # Overwrite corrupted file

            data[key] = value
            # Write to a temporary file first for atomicity
            temp_file = self.state_file.with_suffix(".tmp")
            async with aiofiles.open(temp_file, "w") as f:
                await f.write(json.dumps(data, indent=2))

            # Atomic rename
            os.rename(temp_file, self.state_file)

    async def delete(self, key: str):
        """Delete a key from the state file."""
        async with self._lock:
            if not self.state_file.exists():
                return
            async with aiofiles.open(self.state_file, "r") as f:
                data = json.loads(await f.read())

            if key in data:
                del data[key]
                temp_file = self.state_file.with_suffix(".tmp")
                async with aiofiles.open(temp_file, "w") as f:
                    await f.write(json.dumps(data, indent=2))
                os.rename(temp_file, self.state_file)

    async def get_full_status(self) -> dict:
        """Get comprehensive system status including metrics."""
        return {
            "cpu": {
                "percent": psutil.cpu_percent(interval=0.1),
                "count": psutil.cpu_count(),
                "load_avg": os.getloadavg() if hasattr(os, 'getloadavg') else None,
            },
            "memory": {
                "percent": psutil.virtual_memory().percent,
                "total": psutil.virtual_memory().total,
                "available": psutil.virtual_memory().available,
                "used": psutil.virtual_memory().used,
            },
            "disk": {
                "percent": psutil.disk_usage('/').percent,
                "total": psutil.disk_usage('/').total,
                "free": psutil.disk_usage('/').free,
            },
            "network": {
                "bytes_sent": psutil.net_io_counters().bytes_sent,
                "bytes_recv": psutil.net_io_counters().bytes_recv,
            },
            "uptime": time.time() - psutil.boot_time(),
        }

    async def increment_counter(self, key: str, amount: int = 1) -> int:
        """Atomically increment a counter."""
        async with self._lock:
            current = await self.get(key) or 0
            new_value = current + amount
            await self.set(key, new_value)
            return new_value

    async def get_all(self) -> dict:
        """Get all state data."""
        async with self._lock:
            if not self.state_file.exists():
                return {}
            async with aiofiles.open(self.state_file, "r") as f:
                try:
                    return json.loads(await f.read())
                except json.JSONDecodeError:
                    return {}
