"""Opt-in live check of the universal LLM layer against a running controller.

Lists the registered providers, tests every account you can use with a real
(token-free) model listing, and optionally sends one prompt. Prompts may be
billed by the provider; nothing runs without --execute. No key is ever
printed: the API never returns one.

    python scripts/smoke_llm.py --execute --url http://127.0.0.1:8000 \
        --email admin@lycosa.local [--purpose default | --account ID --model M]

The password is read from LYCOSA_PASSWORD or prompted for.
"""

import argparse
import getpass
import os
import sys

import httpx

REQUIRED = {
    "openai",
    "anthropic",
    "gemini",
    "deepseek",
    "qwen",
    "xai",
    "mistral",
    "openrouter",
    "ollama",
    "lmstudio",
    "vllm",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--execute", action="store_true", required=True)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--email", required=True)
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--purpose", help="send one prompt through this route")
    target.add_argument("--account", help="send one prompt to this account id")
    parser.add_argument("--model", help="model id, with --account")
    parser.add_argument("--prompt", default="Reply with the single word OK.")
    args = parser.parse_args()
    if args.account and not args.model:
        parser.error("--account needs --model")

    password = os.environ.get("LYCOSA_PASSWORD") or getpass.getpass("Password: ")
    with httpx.Client(base_url=args.url, timeout=360, follow_redirects=False) as client:
        login = client.post("/api/v1/auth/login", json={"email": args.email, "password": password})
        login.raise_for_status()
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        try:
            providers = {p["id"] for p in client.get("/api/v1/llm/providers").json()}
            missing = REQUIRED - providers
            print(f"providers registered: {len(providers)}; missing: {sorted(missing) or 'none'}")

            accounts = client.get("/api/v1/llm/accounts").json()
            for account in accounts:
                if not account["usable"]:
                    continue
                result = client.post(f"/api/v1/llm/accounts/{account['id']}/test").json()
                print(
                    f"{account['provider']:<18} {account['label']:<20} {result['status']:<13}"
                    f" models={result.get('models_available')} api_access={result['api_access']}"
                    + (f"  ({result['detail']})" if result.get("detail") else "")
                )
            if not accounts:
                print("no accounts connected yet")

            if args.purpose or args.account:
                body = {"prompt": args.prompt, "max_tokens": 64}
                body |= (
                    {"purpose": args.purpose}
                    if args.purpose
                    else {"account_id": args.account, "model": args.model}
                )
                answer = client.post("/api/v1/llm/test", json=body)
                if answer.is_success:
                    a = answer.json()
                    print(
                        f"answer from {a['provider']}:{a['model']}"
                        f" (fallback #{a['fallback_index']}, {a['latency_ms']} ms,"
                        f" cost={a['estimated_cost']}): {a['content']!r}"
                    )
                else:
                    print("prompt failed:", answer.json().get("error", {}).get("message"))
                    return 1
            return 1 if missing else 0
        finally:
            client.post("/api/v1/auth/logout")


if __name__ == "__main__":
    sys.exit(main())
