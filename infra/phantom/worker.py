"""CPU-only, one request, no network, no cache, no persistent writes."""

import json
import os
import resource
import signal
import sys


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    signal.alarm(min(600, max(1, int(os.environ.get("PHANTOM_TTL", "120")))))
    # Native model libraries can print to stdout/stderr; only a private duplicate
    # is allowed to carry our small JSON answer back to the parent.
    result_fd = os.dup(1)
    with open(os.devnull, "w") as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
    try:
        raw = sys.stdin.buffer.read(262145)
        if len(raw) > 262144:
            return 2
        body = json.loads(raw)
        from llama_cpp import Llama

        model = Llama(
            model_path="/model.gguf", n_ctx=8192, n_gpu_layers=0, verbose=False
        )
        # No prompt cache/save_state call; no shared model server or history.
        result = model.create_chat_completion(
            messages=[{"role": "user", "content": body["prompt"]}],
            max_tokens=body["max_tokens"],
            temperature=body["temperature"],
            stream=False,
        )
        choice = result["choices"][0]
        if choice.get("finish_reason") != "stop":
            return 3
        response = json.dumps({"output": choice["message"]["content"]}).encode()
        model.close()
        if len(response) > 262144:
            return 4
        with os.fdopen(result_fd, "wb") as out:
            out.write(response)
        return 0
    except Exception:  # noqa: BLE001 - never print model inputs/native diagnostics
        return 1


if __name__ == "__main__":
    sys.exit(main())
