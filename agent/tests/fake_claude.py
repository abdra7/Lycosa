"""Stand-in for the Claude Code CLI in tests: FAKE_CLAUDE_MODE picks the
behavior, FAKE_CLAUDE_RECORD (a path) receives argv, cwd, stdin and env."""

import json
import os
import sys
import time

MODE = os.environ.get("FAKE_CLAUDE_MODE", "ok")
RECORD = os.environ.get("FAKE_CLAUDE_RECORD")


def emit(event: dict) -> None:
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def main() -> None:
    args = sys.argv[1:]
    if args == ["--version"]:
        print("2.1.300 (Claude Code)")
        return
    if args[:2] == ["auth", "status"]:
        signed_in = MODE != "signed_out"
        print(
            json.dumps(
                {
                    "loggedIn": signed_in,
                    "authMethod": "claude.ai" if signed_in else None,
                    "email": "owner@example.com",
                    "orgId": "org-secret",
                    "subscriptionType": "pro" if signed_in else None,
                }
            )
        )
        return
    prompt = sys.stdin.read()
    if RECORD:
        with open(RECORD, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "argv": args,
                    "cwd": os.getcwd(),
                    "cwd_entries": sorted(os.listdir(os.getcwd())),
                    "stdin": prompt,
                    "api_key_in_env": "ANTHROPIC_API_KEY" in os.environ,
                    "system_prompt": open(
                        args[args.index("--system-prompt-file") + 1], encoding="utf-8"
                    ).read(),
                },
                fh,
            )
    emit({"type": "system", "subtype": "init", "model": "claude-test-5", "cwd": os.getcwd()})
    if MODE == "hang":
        time.sleep(30)
        return
    if MODE == "no_result":
        return
    if MODE == "big":
        emit(
            {"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": "x" * 3000}}}
        )
    if MODE == "not_signed_in":
        emit(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "result": "Not logged in · Please run /login",
            }
        )
        return
    if MODE == "usage_limit":
        emit(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "api_error_status": 429,
                "result": "Claude AI usage limit reached",
            }
        )
        return
    for piece in ("Hel", "lo"):
        emit(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": piece},
                },
            }
        )
    emit(
        {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "allowed",
                "unifiedWindows": {"five_hour": {"utilization": 0.07, "resetsAt": 1792068000}},
                "email": "should-not-leak@example.com",
            },
        }
    )
    emit(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "Hello",
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 2,
                "cache_creation_input_tokens": 700,
                "cache_read_input_tokens": 10,
                "output_tokens": 4,
            },
            "modelUsage": {"claude-test-5": {"inputTokens": 2}},
        }
    )


if __name__ == "__main__":
    main()
