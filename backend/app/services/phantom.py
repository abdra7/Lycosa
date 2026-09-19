"""One local, networkless model process per task. Never imports the task/DB services.

The controller still holds Python/HTTP buffers. Container removal is a lifecycle
guarantee, NOT a claim of physical RAM erasure. No shared Ollama/model server is used.
"""

import asyncio
import json
import os
import re
import uuid
from pathlib import Path

from app.core.config import get_settings
from app.schemas.phantom import PhantomRequest, PhantomResponse


class PhantomError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


async def _command(
    *args: str, data: bytes | None = None, deadline: float = 20
) -> tuple[int, bytes]:
    """Never use a shell, inherit provider secrets, or retain stderr/model diagnostics."""
    proc = await asyncio.create_subprocess_exec(
        "docker",
        *args,
        stdin=asyncio.subprocess.PIPE if data is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "DOCKER_HOST"}},
        limit=300000,
    )
    try:
        async with asyncio.timeout(deadline):
            if data is not None:
                proc.stdin.write(data)
                await proc.stdin.drain()
                proc.stdin.close()
            output = bytearray()
            while chunk := await proc.stdout.read(8192):
                output.extend(chunk)
                if len(output) > 262144:
                    raise PhantomError(502, "Phantom output exceeded its limit")
            await proc.wait()
            return proc.returncode, bytes(output)
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


class PhantomExecutor:
    def __init__(self):
        self.active = 0

    async def execute(self, body: PhantomRequest) -> PhantomResponse:
        settings = get_settings()
        if not settings.phantom_enabled:
            raise PhantomError(503, "Phantom execution is not configured")
        model = settings.phantom_models.get(body.model)
        if (
            not model
            or not Path(model).is_absolute()
            or not await asyncio.to_thread(Path(model).is_file)
        ):
            raise PhantomError(422, "Phantom model alias is not configured")
        # Docker mount syntax is admin-owned but must not allow option injection.
        if any(c in model for c in ",\n\r"):
            raise PhantomError(503, "Invalid Phantom model configuration")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._/:@-]*", settings.phantom_image):
            raise PhantomError(503, "Invalid Phantom image configuration")
        if self.active >= settings.phantom_max_concurrent:
            raise PhantomError(429, "Phantom capacity busy; retry later")
        self.active += 1  # no await between capacity check and reservation
        name = "lycosa-phantom-" + uuid.uuid4().hex
        attempted = False
        try:
            # Force a local daemon: a remote Docker engine violates this boundary.
            code, info = await _command(
                "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"
            )
            endpoint = json.loads(info) if code == 0 else ""
            endpoint = os.environ.get("DOCKER_HOST") or endpoint
            if not isinstance(endpoint, str) or not endpoint.startswith("unix://"):
                raise PhantomError(503, "Phantom requires a local Linux Docker socket")
            attempted = True
            code, _ = await _command(
                "create",
                "--pull=never",
                "--rm",
                "--interactive",
                "--name",
                name,
                "--label",
                "lycosa.phantom=true",
                "--network=none",
                "--read-only",
                "--log-driver=none",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--user=65534:65534",
                "--pids-limit=64",
                "--ulimit",
                "core=0:0",
                "--memory",
                f"{settings.phantom_memory_mb}m",
                "--memory-swap",
                f"{settings.phantom_memory_mb}m",
                "--cpus",
                str(settings.phantom_cpus),
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777",
                "--mount",
                f"type=bind,source={model},target=/model.gguf,readonly",
                "--env",
                f"PHANTOM_TTL={settings.phantom_timeout_seconds}",
                settings.phantom_image,
            )
            if code:
                raise PhantomError(503, "Phantom isolation could not be created")
            code, output = await _command(
                "start",
                "--attach",
                "--interactive",
                name,
                data=body.model_dump_json().encode(),
                deadline=settings.phantom_timeout_seconds + 10,
            )
            if code:
                raise PhantomError(502, "Phantom inference failed")
            result = json.loads(output)
            if not isinstance(result, dict) or not isinstance(result.get("output"), str):
                raise PhantomError(502, "Phantom returned an invalid result")
            if not result["output"].strip():
                raise PhantomError(502, "Phantom returned no text")
            return PhantomResponse(output=result["output"])
        except PhantomError:
            raise
        except TimeoutError:
            raise PhantomError(504, "Phantom deadline exceeded") from None
        except (OSError, ValueError, TypeError):
            raise PhantomError(503, "Phantom runtime unavailable") from None
        finally:
            try:
                # Cancellation must not skip cleanup. Do not return output until
                # the engine confirms that the named container no longer exists.
                cleanup = asyncio.create_task(self._remove(name) if attempted else self._noop())
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    await cleanup
                    raise
            finally:
                self.active -= 1

    async def _noop(self) -> None:
        pass

    async def _remove(self, name: str) -> None:
        try:
            await _command("rm", "--force", name)
            code, output = await _command("ps", "--all", "--quiet", "--filter", f"name=^/{name}$")
            if code or output.strip():
                raise PhantomError(503, "Phantom cleanup unconfirmed; inspect the local runtime")
        except (OSError, TimeoutError):
            raise PhantomError(
                503, "Phantom cleanup unconfirmed; inspect the local runtime"
            ) from None


executor = PhantomExecutor()
