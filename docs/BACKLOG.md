# Backlog

- As an operator, I want to remove a decommissioned node (DELETE
  /api/v1/nodes/{id} + dashboard action) so that stale nodes don't clutter
  the inventory. (Found during 8b live verification: a Phase-2 test node
  lingered as "registered" forever; no removal path exists.)
- Re-embed job: switching a knowledge collection's embedding backend
  requires re-ingestion (ADR-013).
- Redis-backed rate limiting when the API scales horizontally (ADR-008).
- Hot-reload of recommendation rules without api restart (ADR-010).
- Async task queue behind POST /tasks returning 202 + polling (ADR-012).
- Shield synchronous workflow runs from client disconnects the same way
  POST /tasks is (see Ticket #102 fix); long runs cancelled by a dashboard
  timeout currently stay "running" forever.
- mTLS / enrollment handshake for agent exec API hardening (ADR-011).
- [Bug/Feature] Network Device Discovery: Devices/agents on the local network are not automatically discovered by the controller/dashboard. (Investigate missing mDNS/SSDP network scan capabilities, and local firewall/port binding issues for port 8000/8010).
