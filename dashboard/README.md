# Lycosa Dashboard

Native Flutter Desktop operator dashboard (macOS/Windows/Linux) for the
Lycosa controller: node inventory, tasks, workflow runs, knowledge
collections, and admin — distributed as per-OS installers, not a Docker
service (ADR-006).

## Current state (sub-phase 8a)

Connection setup (controller URL + credentials, multiple profiles stored in
the OS keychain), login/logout with server-side revocation, and the
authenticated shell. Feature screens land in 8b (nodes), 8c
(tasks/workflows), 8d (knowledge/admin).

## Development

Requires the [Flutter SDK](https://docs.flutter.dev/get-started/install)
(stable). On Windows, desktop builds additionally need Visual Studio with
C++ workload and **Developer Mode enabled** (for plugin symlinks).

```bash
cd dashboard
flutter pub get
flutter analyze
flutter test
flutter run -d windows     # or: -d macos / -d linux
```

Point the connection screen at a running controller
(`docker compose -f infra/docker-compose.yml up -d` from the repo root,
default admin from your `.env`).

## Architecture notes (ADR-015)

- **State:** Riverpod; session/auth state drives which screen renders
  (no router package).
- **API:** hand-written typed client over `package:http`, parsing the
  ADR-007 error envelope into `ApiException.friendly` messages.
- **Secrets:** controller profiles (URL + bearer token) live in the OS
  keychain via `flutter_secure_storage`.
- **Live data:** REST polling until the Sprint 9 WebSocket event stream.
