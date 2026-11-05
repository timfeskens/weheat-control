"""Fail-closed request journal: propagate disk errors, fsync before mutation."""

import json
import os
import tempfile
from pathlib import Path

from homeassistant.core import HomeAssistant

from .api import Json, WeheatError, json_value, object_value


class OperationJournal:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        if not entry_id.isalnum():
            raise WeheatError("Invalid journal identity")
        self.path = Path(hass.config.path(".storage", f"weheat_control.{entry_id}.operation"))

    def _read(self) -> dict[str, Json] | None:
        try:
            text = self.path.read_text()
        except FileNotFoundError:
            return None
        data = object_value(json_value(json.loads(text)))
        if data.get("version") != 1:
            raise WeheatError("Unsupported operation journal version; refusing device writes")
        return object_value(data.get("data"))

    async def async_load(self) -> dict[str, Json] | None:
        return await self.hass.async_add_executor_job(self._read)

    def _write(self, operation: dict[str, Json]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f"{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as file:
                json.dump({"version": 1, "data": operation}, file, allow_nan=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    async def async_save(self, operation: dict[str, Json]) -> None:
        # Immutable serialized snapshot; the executor must not see later edits.
        snapshot = object_value(json_value(json.loads(json.dumps(operation, allow_nan=False))))
        await self.hass.async_add_executor_job(self._write, snapshot)
