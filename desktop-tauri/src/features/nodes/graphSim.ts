// The star-topology force simulation of the node graph view: every pair
// repels, each device is sprung to a rest distance from the controller hub,
// and a gentle pull keeps the whole graph centered.

export const HUB_ID = "__hub__";
export const NODE_RADIUS = 26;
export const HUB_RADIUS = 20;
export const MIN_ZOOM = 0.4;
export const MAX_ZOOM = 3;

export interface Body {
  id: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  pinned: boolean;
}

export type Bodies = Map<string, Body>;

/** Bodies for `ids`, keeping the positions of ones that already exist and
 * placing new ones on a circle around the center. */
export function seed(existing: Bodies, ids: string[], width: number, height: number): Bodies {
  const cx = width > 0 ? width / 2 : 300;
  const cy = height > 0 ? height / 2 : 220;
  const out: Bodies = new Map();
  out.set(HUB_ID, existing.get(HUB_ID) ?? { id: HUB_ID, x: cx, y: cy, vx: 0, vy: 0, pinned: false });
  ids.forEach((id, i) => {
    const angle = (2 * Math.PI * i) / Math.max(ids.length, 1);
    const prev = existing.get(id);
    out.set(id, prev ?? { id, x: cx + Math.cos(angle) * 140, y: cy + Math.sin(angle) * 140, vx: 0, vy: 0, pinned: false });
  });
  return out;
}

/** Advance the simulation by `dt` seconds (clamped to 50 ms per step). */
export function step(bodies: Bodies, width: number, height: number, dtRaw: number): void {
  const dt = Math.min(Math.max(dtRaw, 0), 0.05);
  if (width <= 0 || height <= 0 || bodies.size < 2) return;
  const list = [...bodies.values()];
  const fx = new Map<string, number>(list.map((b) => [b.id, 0]));
  const fy = new Map<string, number>(list.map((b) => [b.id, 0]));
  const add = (id: string, x: number, y: number) => {
    fx.set(id, fx.get(id)! + x);
    fy.set(id, fy.get(id)! + y);
  };

  for (let i = 0; i < list.length; i++) {
    for (let j = i + 1; j < list.length; j++) {
      const a = list[i]!;
      const b = list[j]!;
      let dx = a.x - b.x;
      let dy = a.y - b.y;
      let dist = Math.hypot(dx, dy);
      if (dist < 1) {
        // deterministic nudge apart for exact overlaps
        dx = ((i * 31 + j) % 7) / 7 - 0.5;
        dy = ((j * 17 + i) % 5) / 5 - 0.5;
        dist = Math.max(Math.hypot(dx, dy), 0.01);
      }
      const push = 2600 / (dist * dist);
      add(a.id, (dx / dist) * push, (dy / dist) * push);
      add(b.id, (-dx / dist) * push, (-dy / dist) * push);
    }
  }

  const hub = bodies.get(HUB_ID);
  if (hub) {
    for (const b of list) {
      if (b.id === HUB_ID) continue;
      const dx = hub.x - b.x;
      const dy = hub.y - b.y;
      const dist = Math.max(Math.hypot(dx, dy), 0.01);
      const pull = (dist - 150) * 0.9;
      add(b.id, (dx / dist) * pull, (dy / dist) * pull);
      add(HUB_ID, (-dx / dist) * pull * 0.15, (-dy / dist) * pull * 0.15);
    }
  }

  const cx = width / 2;
  const cy = height / 2;
  for (const b of list) add(b.id, (cx - b.x) * 0.02, (cy - b.y) * 0.02);

  const margin = 40;
  for (const b of list) {
    if (b.pinned) continue;
    b.vx = (b.vx + fx.get(b.id)! * dt) * 0.82;
    b.vy = (b.vy + fy.get(b.id)! * dt) * 0.82;
    b.x = Math.min(Math.max(b.x + b.vx * dt, margin), Math.max(margin, width - margin));
    b.y = Math.min(Math.max(b.y + b.vy * dt, margin), Math.max(margin, height - margin));
  }
}

export function hitTest(bodies: Bodies, x: number, y: number): Body | null {
  for (const b of bodies.values()) {
    const r = b.id === HUB_ID ? HUB_RADIUS : NODE_RADIUS;
    if (Math.hypot(b.x - x, b.y - y) <= r + 6) return b;
  }
  return null;
}

export const clampZoom = (z: number) => Math.min(Math.max(z, MIN_ZOOM), MAX_ZOOM);
