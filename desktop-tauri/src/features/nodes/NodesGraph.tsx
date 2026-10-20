import { useEffect, useRef, useState } from "react";

import { statusColor } from "../../api/format";
import { NODE_ROLES, type NodeInfo } from "../../api/types";
import { IconButton } from "../../components/ui";
import { useNav } from "../../state/nav";
import { useTheme } from "../../state/theme";
import { HUB_ID, HUB_RADIUS, NODE_RADIUS, clampZoom, hitTest, seed, step, type Bodies, type Body } from "./graphSim";

/** Fixed CVD-validated hues per role, in light/dark steps; role identity
 * never rests on color alone (each node also carries a glyph). */
const ROLE_COLORS = {
  light: { ai_compute: "#2A78D6", hybrid: "#1BAF7A", knowledge: "#EDA100", tool: "#008300", vision: "#4A3AA7", storage: "#E34948" },
  dark: { ai_compute: "#3987E5", hybrid: "#199E70", knowledge: "#C98500", tool: "#008300", vision: "#9085E9", storage: "#E66767" },
} as const;
const ROLE_GLYPHS: Record<string, string> = { ai_compute: "AI", hybrid: "HY", knowledge: "KN", tool: "TL", vision: "VI", storage: "ST" };

const nodeRole = (n: NodeInfo) => n.role ?? n.recommended_role ?? null;

function usageLine(node: NodeInfo): string | null {
  const m = node.metrics;
  if (!m || node.status !== "online") return null;
  const cpu = typeof m["cpu_percent"] === "number" ? (m["cpu_percent"] as number).toFixed(0) : null;
  const ram = typeof m["ram_percent"] === "number" ? (m["ram_percent"] as number).toFixed(0) : null;
  if (cpu == null && ram == null) return null;
  return `CPU ${cpu ?? "—"}% · RAM ${ram ?? "—"}%`;
}

/** What one animation frame draws: a copy of the simulation and the view. */
interface Frame {
  bodies: Body[];
  zoom: number;
  panX: number;
  panY: number;
}

/** Star topology: the controller hub with every node sprung around it.
 * Drag a node to move it, drag empty space to pan, scroll to zoom, click a
 * node for its details. The simulation lives in refs; each animation frame
 * renders a snapshot of it. */
export function NodesGraph({ nodes }: { nodes: NodeInfo[] }) {
  const nav = useNav();
  const { mode } = useTheme();
  const colors = ROLE_COLORS[mode];
  const box = useRef<HTMLDivElement>(null);
  const bodies = useRef<Bodies>(new Map());
  const size = useRef({ w: 0, h: 0 });
  const view = useRef({ zoom: 1, panX: 0, panY: 0 });
  const drag = useRef<{ id: string | null; panning: boolean; lastX: number; lastY: number; moved: boolean } | null>(null);
  const [frame, setFrame] = useState<Frame>({ bodies: [], zoom: 1, panX: 0, panY: 0 });
  const [fullscreen, setFullscreen] = useState(false);
  const ids = nodes.map((n) => n.id).join(",");

  useEffect(() => {
    bodies.current = seed(bodies.current, ids ? ids.split(",") : [], size.current.w, size.current.h);
  }, [ids]);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => {
      size.current = { w: el.clientWidth, h: el.clientHeight };
    };
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    measure();
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      step(bodies.current, size.current.w, size.current.h, (now - last) / 1000);
      last = now;
      setFrame({ bodies: [...bodies.current.values()].map((b) => ({ ...b })), ...view.current });
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
    };
  }, [fullscreen]);

  const toWorld = (clientX: number, clientY: number) => {
    const rect = box.current!.getBoundingClientRect();
    const { zoom, panX, panY } = view.current;
    return { x: (clientX - rect.left - panX) / zoom, y: (clientY - rect.top - panY) / zoom };
  };

  const zoomBy = (factor: number, anchor?: { x: number; y: number }) => {
    const v = view.current;
    const next = clampZoom(v.zoom * factor);
    const a = anchor ?? { x: size.current.w / 2, y: size.current.h / 2 };
    v.panX = a.x - (a.x - v.panX) * (next / v.zoom);
    v.panY = a.y - (a.y - v.panY) * (next / v.zoom);
    v.zoom = next;
  };

  const byId = new Map(nodes.map((n) => [n.id, n]));
  const hub = frame.bodies.find((b) => b.id === HUB_ID);
  const devices = frame.bodies.filter((b) => b.id !== HUB_ID && byId.has(b.id));
  const rolesInUse = new Set(nodes.map(nodeRole).filter((r): r is string => !!r));

  return (
    <div
      className="col"
      style={
        fullscreen
          ? { position: "fixed", inset: 0, zIndex: 45, background: "var(--bg)", padding: 16, height: "100%" }
          : { height: "100%", minHeight: 420 }
      }
    >
      <div className="row wrap small" style={{ gap: 16 }}>
        {NODE_ROLES.filter((r) => rolesInUse.has(r)).map((r) => (
          <span key={r} className="row" style={{ gap: 4 }}>
            <span className="dot" style={{ background: colors[r], width: 12, height: 12 }} />
            {r}
          </span>
        ))}
        {(
          [
            ["var(--success)", "online"],
            ["var(--warning)", "registered"],
            ["var(--error)", "offline"],
          ] as const
        ).map(([c, label]) => (
          <span key={label} className="row" style={{ gap: 4 }}>
            <span className="dot" style={{ border: `2px solid ${c}`, background: "transparent" }} />
            {label}
          </span>
        ))}
      </div>
      <div
        ref={box}
        className="graph-canvas"
        data-testid="nodes-graph"
        onWheel={(e) => {
          const rect = box.current!.getBoundingClientRect();
          zoomBy(e.deltaY < 0 ? 1.1 : 1 / 1.1, { x: e.clientX - rect.left, y: e.clientY - rect.top });
        }}
        onPointerDown={(e) => {
          const p = toWorld(e.clientX, e.clientY);
          const hit = hitTest(bodies.current, p.x, p.y);
          const onNode = hit && hit.id !== HUB_ID;
          if (onNode) hit.pinned = true;
          drag.current = { id: onNode ? hit.id : null, panning: !onNode, lastX: e.clientX, lastY: e.clientY, moved: false };
          (e.target as Element).setPointerCapture?.(e.pointerId);
        }}
        onPointerMove={(e) => {
          const d = drag.current;
          if (!d) return;
          if (Math.abs(e.clientX - d.lastX) + Math.abs(e.clientY - d.lastY) > 2) d.moved = true;
          if (d.id) {
            const p = toWorld(e.clientX, e.clientY);
            const b = bodies.current.get(d.id);
            if (b) {
              b.x = p.x;
              b.y = p.y;
            }
          } else if (d.panning) {
            view.current.panX += e.clientX - d.lastX;
            view.current.panY += e.clientY - d.lastY;
          }
          d.lastX = e.clientX;
          d.lastY = e.clientY;
        }}
        onPointerUp={(e) => {
          const d = drag.current;
          drag.current = null;
          if (d?.id) {
            const b = bodies.current.get(d.id);
            if (b) b.pinned = false;
          }
          if (d && !d.moved) {
            const p = toWorld(e.clientX, e.clientY);
            const hit = hitTest(bodies.current, p.x, p.y);
            if (hit && hit.id !== HUB_ID) nav.push({ kind: "node", nodeId: hit.id });
          }
        }}
      >
        <svg role="img" aria-label="Node graph">
          <g transform={`translate(${frame.panX} ${frame.panY}) scale(${frame.zoom})`}>
            {hub && devices.map((b) => <line key={`e-${b.id}`} x1={hub.x} y1={hub.y} x2={b.x} y2={b.y} stroke="var(--border)" strokeWidth={2} />)}
            {hub && (
              <g>
                <polygon
                  points={`${hub.x},${hub.y - HUB_RADIUS} ${hub.x + HUB_RADIUS},${hub.y} ${hub.x},${hub.y + HUB_RADIUS} ${hub.x - HUB_RADIUS},${hub.y}`}
                  fill="var(--primary)"
                />
                <text x={hub.x} y={hub.y + 4} textAnchor="middle" fontSize={11} fontWeight={700} fill="var(--on-primary)">
                  C
                </text>
                <text x={hub.x} y={hub.y + HUB_RADIUS + 18} textAnchor="middle" fontSize={12} fill="var(--text-2)">
                  Controller
                </text>
              </g>
            )}
            {devices.map((b) => {
              const n = byId.get(b.id)!;
              const role = nodeRole(n);
              const usage = usageLine(n);
              const fill = role && role in colors ? colors[role as keyof typeof colors] : "var(--border)";
              return (
                <g key={b.id} style={{ cursor: "pointer" }}>
                  <circle cx={b.x} cy={b.y} r={NODE_RADIUS} fill={fill} stroke={statusColor(n.status)} strokeWidth={3} />
                  <text x={b.x} y={b.y + 4} textAnchor="middle" fontSize={11} fontWeight={700} fill="#fff">
                    {role ? (ROLE_GLYPHS[role] ?? "?") : "?"}
                  </text>
                  <text x={b.x} y={b.y + NODE_RADIUS + 18} textAnchor="middle" fontSize={12} fontWeight={600} fill="var(--text)">
                    {n.name}
                  </text>
                  {usage && (
                    <text x={b.x} y={b.y + NODE_RADIUS + 34} textAnchor="middle" fontSize={10.5} fill="var(--text-2)">
                      {usage}
                    </text>
                  )}
                </g>
              );
            })}
          </g>
        </svg>
        <div className="card" style={{ position: "absolute", right: 8, bottom: 8, padding: 4, display: "flex", flexDirection: "column" }}>
          <IconButton icon="add" label="Zoom in" onClick={() => zoomBy(1.2)} />
          <IconButton icon="minus" label="Zoom out" onClick={() => zoomBy(1 / 1.2)} />
          <IconButton icon="fit" label="Reset view" onClick={() => (view.current = { zoom: 1, panX: 0, panY: 0 })} />
          <IconButton icon={fullscreen ? "collapse" : "expand"} label={fullscreen ? "Exit full screen" : "Expand"} onClick={() => setFullscreen((f) => !f)} />
        </div>
      </div>
    </div>
  );
}
