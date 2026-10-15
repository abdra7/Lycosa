# Live desktop acceptance

`desktop_acceptance_test.dart` drives the real desktop app against a running
controller. It walks the desktop acceptance cases (connection, nodes, Claude
subscription, Chat, tasks, workflows, knowledge, admin, themes, reconnect)
and saves a screenshot of each screen.

**Use a throwaway controller only.** The run creates and deletes keys,
collections, workflows and accounts, restarts the API container and deletes
the test node at the end. Never point it at a controller with real data.

It is skipped unless `LYCOSA_QA_URL` and `LYCOSA_QA_PASSWORD` are defined, so
`flutter test` and CI never run it.

## Prerequisites

- A throwaway controller (its own compose project, volumes and ports), an
  admin and an operator user.
- One registered node named `qa-laptop` whose agent runs with
  `LYCOSA_CLAUDE_SUBSCRIPTION=true` and its owner set to the admin email,
  with Claude Code signed in on that machine. The run makes a few small
  calls on that plan.
- Fixture files in `LYCOSA_QA_FIXTURES`: `qa-policy.txt` (containing
  `QA-LYCOSA-731`) and `scan.pdf` (a PDF without a text layer).

## Run (Windows)

```sh
flutter test integration_test/desktop_acceptance_test.dart -d windows \
  --dart-define=LYCOSA_QA_URL=http://127.0.0.1:18000 \
  --dart-define=LYCOSA_QA_EMAIL=<admin email> \
  --dart-define=LYCOSA_QA_PASSWORD=<admin password> \
  --dart-define=LYCOSA_QA_OPERATOR_EMAIL=<operator email> \
  --dart-define=LYCOSA_QA_OPERATOR_PASSWORD=<operator password> \
  --dart-define=LYCOSA_QA_OUT=<output dir> \
  --dart-define=LYCOSA_QA_FIXTURES=<fixtures dir> \
  --dart-define=LYCOSA_QA_API_CONTAINER=<api container name>
```

Results go to `<output dir>/results.jsonl`, one line per case, as Passed,
Failed, Blocked or Not Run. Screenshots go to `<output dir>/shots/`, and a
failed case also saves `FAIL-<id>.png`.

Saved sessions and the theme use in-memory stores during the run, so your
real desktop profiles in the OS credential store are never read or changed.
The native file dialog is replaced by a stub that returns a fixture file;
everything after the dialog is the real app.
