// Shared display rules (LycosaColors.status and friends in the Flutter app).

/** Status → color: the same green or red on every screen. */
export function statusColor(status: string): string {
  switch (status) {
    case "online":
    case "succeeded":
    case "embedded":
    case "running":
      return "var(--success)";
    case "offline":
    case "failed":
    case "error":
      return "var(--error)";
    case "skipped":
      return "var(--text-2)";
    default:
      return "var(--warning)"; // registered, pending, paused, pending_approval…
  }
}

export function heartbeatAge(last: string | null | undefined, now = Date.now()): string {
  if (!last) return "—";
  const seconds = Math.max(0, Math.floor((now - new Date(last).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

/** API identifiers (pending_approval) as words (pending approval). */
export const humanize = (value: string) => value.replaceAll("_", " ");

export const chunksLabel = (count: number) => (count === 1 ? "1 chunk" : `${count} chunks`);

/** What a collection's embedder means for search (ADR-013). */
export function searchKind(embeddingBackend: string): string {
  if (embeddingBackend === "hashing") return "keyword search";
  if (embeddingBackend === "fastembed") return "semantic search";
  return `${embeddingBackend} search`;
}

export function localTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function percent(value: unknown, suffix = "%"): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(0)}${suffix}` : "—";
}

export const capitalize = (s: string) => (s ? s[0]!.toUpperCase() + s.slice(1) : s);
