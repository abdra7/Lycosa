# Security Policy

## Reporting a vulnerability

Please report vulnerabilities privately via
[GitHub Security Advisories](https://github.com/abdra7/Lycosa/security/advisories/new)
— do **not** open a public issue for security problems. You should receive a
response within a week. Please include reproduction steps and the affected
component (backend, agent, dashboard, install scripts).

## Threat model (read this before deploying)

Lycosa is **LAN-first**: the controller and agents are designed to run on a
trusted local network. Out of the box it is **not** hardened for exposure to
the public internet.

What the design already provides:

- Passwords hashed with argon2id; API keys stored as prefix + SHA-256 only.
- JWT access tokens backed by revocable server-side sessions (logout kills
  the token immediately).
- Role-based access control (`admin` / `operator` / `node`) on every
  endpoint; nodes can only act as nodes.
- Rate limiting on the API edge; consistent error envelope that never leaks
  internals.
- One API key = one node identity; revoking the key severs the node.
- A node's registered agent URL must be a bare `http(s)://host[:port]` origin,
  so a node key cannot point controller requests at other services.
- Every audited action records its actor (user or API key) and audit rows are
  written before provider credentials change.
- Grafana (LAN-published) runs on an isolated monitoring network and never
  receives the controller's `.env` secrets.

Known LAN-scope trade-offs (see ADR-011 and the backlog):

- Controller ↔ agent traffic is plain HTTP on the LAN; agent execution APIs
  are protected by per-boot random tokens, stored retrievably in the
  controller database. mTLS / enrollment handshakes are future hardening.
- No TLS termination is included; put the API behind a reverse proxy with
  TLS if you need it to leave the trusted network.

## Supported versions

Only the latest minor release receives security fixes.
