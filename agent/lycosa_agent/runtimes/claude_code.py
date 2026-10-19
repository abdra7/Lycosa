"""Claude subscription runtime: runs the node owner's own, unmodified Claude
Code CLI in headless mode (ADR-032).

Anthropic lets an end user sign in to the unmodified Claude Code binary with
their own Claude plan and have a platform run it. It does not let a
third-party app offer Claude.ai login or collect, store or pass on Claude
credentials. So this runtime:

- never signs in and never reads a token: sign-in is `claude auth login`,
  Anthropic's own flow, run by the user on this machine; only
  `claude auth status` is read, and only its non-identifying fields
- serves a single Lycosa user, the one the machine's owner configured
  (the executor enforces it), so one person's plan is never shared
- runs every call as a plain model call: safe mode (no CLAUDE.md, hooks,
  plugins, skills or MCP servers), no tools, no saved session, an empty
  temporary working directory, the prompt on stdin
"""

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from lycosa_agent.runtimes.chat import ChatMessage

logger = logging.getLogger("lycosa.agent.claude")

MODEL_ALIASES = ("default", "sonnet", "opus", "haiku")
DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."
# Claude Code still tells the model about its (empty, temporary) working
# directory; without this the model talks about it (Sprint 14 live QA)
SCOPE_NOTE = (
    "You are answering through Lycosa as a plain chat model: you have no tools "
    "and no files. Ignore the working directory and environment details and do "
    "not mention them."
)
# an API key in the environment would make Claude Code bill the API instead
# of the user's plan; the user's own sign-in is what this runtime runs on
STRIPPED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
MAX_OUTPUT_CHARS = 2_000_000
STDOUT_LINE_LIMIT = 16 * 1024 * 1024
STDERR_KEEP_BYTES = 4096
STATUS_TIMEOUT_SECONDS = 20.0

ERROR_MESSAGES = {
    "not_installed": "Claude Code is not installed on this node",
    "not_signed_in": "Claude Code on this node is not signed in; run `claude auth login` there",
    "usage_limit": "The Claude plan's usage limit is reached; try again after it resets",
    "model_unavailable": "This model is not available on the node's Claude plan",
    "timeout": "Claude Code did not finish in time",
    "output_too_large": "Claude Code's answer exceeded the size limit",
    "failed": "Claude Code failed on this node; check its installation and sign-in",
}

_PLAN_PATTERN = re.compile(r"^[a-z0-9_]{1,20}$")
_VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+")
_STOP_REASONS = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length"}


class ClaudeRuntimeError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(ERROR_MESSAGES[code])


def classify_failure(text: str, status: Any = None) -> str:
    """Map a failed run (result text and API status) to a public error code.
    The raw text is only inspected, never returned."""
    lowered = text.lower()
    if status in (401, 403) or any(
        s in lowered for s in ("login", "logged in", "authenticat", "oauth", "credential")
    ):
        return "not_signed_in"
    if status == 429 or any(s in lowered for s in ("usage limit", "rate limit", "limit reached")):
        return "usage_limit"
    if status == 404 or (
        "model" in lowered
        and any(s in lowered for s in ("not found", "not available", "invalid", "not supported"))
    ):
        return "model_unavailable"
    return "failed"


def render_prompt(messages: list[ChatMessage]) -> tuple[str, str]:
    """(system prompt, stdin prompt). Claude Code takes one prompt per run, so
    a multi-turn conversation is passed as a transcript."""
    given = "\n\n".join(m.content for m in messages if m.role == "system").strip()
    system = f"{given or DEFAULT_SYSTEM_PROMPT}\n\n{SCOPE_NOTE}"
    turns = [m for m in messages if m.role != "system"]
    if len(turns) == 1 and turns[0].role == "user":
        return system, turns[0].content
    parts = [
        "Below is the conversation so far. Reply to the last user message as the assistant.",
        "",
    ]
    for message in turns:
        parts.append(f"<{message.role}>\n{message.content}\n</{message.role}>")
    return system, "\n".join(parts)


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _plan_usage(info: Any) -> dict[str, Any] | None:
    """The non-identifying part of a rate_limit_event: per-window utilization."""
    if not isinstance(info, dict):
        return None
    windows: dict[str, dict[str, float | int | None]] = {}
    for name, window in (info.get("unifiedWindows") or {}).items():
        if not (isinstance(name, str) and _PLAN_PATTERN.match(name) and isinstance(window, dict)):
            continue
        utilization = window.get("utilization")
        resets_at = window.get("resetsAt")
        windows[name] = {
            "utilization": float(utilization)
            if isinstance(utilization, (int, float)) and not isinstance(utilization, bool)
            else None,
            "resets_at": resets_at if isinstance(resets_at, int) else None,
        }
    status = info.get("status")
    return {
        "status": status if isinstance(status, str) and _PLAN_PATTERN.match(status) else None,
        "windows": windows,
    }


class ClaudeCodeRuntime:
    name = "claude_subscription"

    def __init__(
        self,
        binary: str = "claude",
        *,
        timeout_seconds: float = 300.0,
        max_concurrency: int = 1,
        command: list[str] | None = None,
    ) -> None:
        self._binary = binary
        self._command_override = command  # tests run a fake CLI through the interpreter
        self._timeout = timeout_seconds
        self._slots = asyncio.Semaphore(max(1, max_concurrency))
        self.last_plan_usage: dict[str, Any] | None = None

    def _command(self) -> list[str]:
        if self._command_override is not None:
            return list(self._command_override)
        path = shutil.which(self._binary)
        if path is None:
            raise ClaudeRuntimeError("not_installed")
        return [path]

    @staticmethod
    def _env() -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in STRIPPED_ENV}
        env["NO_COLOR"] = "1"
        return env

    async def _run_quick(self, args: list[str]) -> str:
        process = await asyncio.create_subprocess_exec(
            *self._command(),
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=self._env(),
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), STATUS_TIMEOUT_SECONDS)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise ClaudeRuntimeError("timeout") from None
        return stdout.decode("utf-8", errors="replace")

    async def status(self) -> dict[str, Any]:
        """Installed / version / signed in / plan type. Never the account's
        email, organization or any token."""
        result: dict[str, Any] = {
            "enabled": True,
            "installed": False,
            "version": None,
            "signed_in": False,
            "auth_method": "none",
            "plan": None,
            "plan_usage": self.last_plan_usage,
        }
        try:
            version = _VERSION_PATTERN.search(await self._run_quick(["--version"]))
            result["installed"] = True
            result["version"] = version.group(0) if version else None
            raw = await self._run_quick(["auth", "status", "--json"])
        except ClaudeRuntimeError:
            return result
        except OSError:
            return result
        try:
            auth = json.loads(raw)
        except ValueError:
            return result
        if not isinstance(auth, dict):
            return result
        result["signed_in"] = auth.get("loggedIn") is True
        method = str(auth.get("authMethod") or "").lower()
        if not result["signed_in"]:
            result["auth_method"] = "none"
        elif method == "claude.ai":
            result["auth_method"] = "subscription"
        elif "key" in method or "console" in method:
            result["auth_method"] = "api_key"
        else:
            result["auth_method"] = "other"
        plan = auth.get("subscriptionType")
        if isinstance(plan, str) and _PLAN_PATTERN.match(plan.lower()):
            result["plan"] = plan.lower()
        return result

    async def stream(
        self, messages: list[ChatMessage], model: str
    ) -> AsyncIterator[dict[str, Any]]:
        """Run one call and yield normalized events: start, text, reasoning,
        usage, finish, or a single error event."""
        try:
            command = self._command()
        except ClaudeRuntimeError as exc:
            yield _error(exc.code)
            return
        async with self._slots:
            with tempfile.TemporaryDirectory(prefix="chat-", ignore_cleanup_errors=True) as workdir:
                async for event in self._run(command, workdir, messages, model):
                    yield event

    async def _run(
        self, command: list[str], workdir: str, messages: list[ChatMessage], model: str
    ) -> AsyncIterator[dict[str, Any]]:
        system, prompt = render_prompt(messages)
        system_file = Path(workdir) / "system-prompt.txt"
        system_file.write_text(system, encoding="utf-8")
        argv = [
            *command,
            "-p",
            "--safe-mode",
            "--tools",
            "",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--system-prompt-file",
            str(system_file),
        ]
        if model != "default":
            argv += ["--model", model]
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workdir,
                env=self._env(),
                limit=STDOUT_LINE_LIMIT,
            )
        except OSError:
            yield _error("not_installed")
            return
        stderr_task = asyncio.create_task(self._drain_stderr(process))
        try:
            async for event in self._events(process, prompt):
                yield event
        finally:
            if process.returncode is None:
                process.kill()
            await process.wait()
            stderr_tail = await stderr_task
            if process.returncode not in (0, None) and stderr_tail:
                # stays on this machine's log; never forwarded to the controller
                logger.warning("claude exited %s: %s", process.returncode, stderr_tail)

    @staticmethod
    async def _drain_stderr(process: asyncio.subprocess.Process) -> str:
        assert process.stderr is not None
        kept = bytearray()
        while chunk := await process.stderr.read(4096):
            if len(kept) < STDERR_KEEP_BYTES:
                kept.extend(chunk[: STDERR_KEEP_BYTES - len(kept)])
        return kept.decode("utf-8", errors="replace").strip()

    async def _events(
        self, process: asyncio.subprocess.Process, prompt: str
    ) -> AsyncIterator[dict[str, Any]]:
        assert process.stdin is not None and process.stdout is not None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout
        try:
            process.stdin.write(prompt.encode("utf-8"))
            await process.stdin.drain()
            process.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the CLI exited early; its exit is reported below
        emitted = 0
        seen_text = False
        model: str | None = None
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                yield _error("timeout")
                return
            try:
                line = await asyncio.wait_for(process.stdout.readline(), remaining)
            except TimeoutError:
                yield _error("timeout")
                return
            except ValueError:  # a single line beyond STDOUT_LINE_LIMIT
                yield _error("output_too_large")
                return
            if not line:
                break
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            if kind == "system" and event.get("subtype") == "init":
                if isinstance(event.get("model"), str):
                    model = event["model"][:100]
                    yield {"type": "start", "model": model}
            elif kind == "stream_event":
                delta = (event.get("event") or {}).get("delta") or {}
                text = None
                if delta.get("type") == "text_delta":
                    text, out_type = delta.get("text"), "text"
                elif delta.get("type") == "thinking_delta":
                    text, out_type = delta.get("thinking"), "reasoning"
                if isinstance(text, str) and text:
                    emitted += len(text)
                    if emitted > MAX_OUTPUT_CHARS:
                        yield _error("output_too_large")
                        return
                    seen_text = seen_text or out_type == "text"
                    yield {"type": out_type, "text": text}
            elif kind == "rate_limit_event":
                usage = _plan_usage(event.get("rate_limit_info"))
                if usage is not None:
                    self.last_plan_usage = usage
            elif kind == "result":
                for out in self._result_events(event, seen_text, model):
                    yield out
                return
        yield _error("failed")  # the CLI ended without a result

    @staticmethod
    def _result_events(
        event: dict[str, Any], seen_text: bool, model: str | None
    ) -> list[dict[str, Any]]:
        result_text = event.get("result") if isinstance(event.get("result"), str) else ""
        if event.get("is_error") or event.get("subtype") != "success":
            return [_error(classify_failure(result_text, event.get("api_error_status")))]
        out: list[dict[str, Any]] = []
        if not seen_text and result_text:
            out.append({"type": "text", "text": result_text[:MAX_OUTPUT_CHARS]})
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
        out.append(
            {
                "type": "usage",
                "input_tokens": _count(usage.get("input_tokens"))
                + _count(usage.get("cache_creation_input_tokens"))
                + _count(usage.get("cache_read_input_tokens")),
                "output_tokens": _count(usage.get("output_tokens")),
            }
        )
        model_usage = event.get("modelUsage")
        if isinstance(model_usage, dict) and model_usage:
            name = next(iter(model_usage))
            if isinstance(name, str):
                model = name[:100]
        out.append(
            {
                "type": "finish",
                "reason": _STOP_REASONS.get(str(event.get("stop_reason")), "other"),
                "model": model,
            }
        )
        return out


def _error(code: str) -> dict[str, Any]:
    return {"type": "error", "code": code, "message": ERROR_MESSAGES[code]}
