import { describe, expect, it } from "vitest";

import { agentCommands, controllerUrlForOtherDevices, isLoopbackHost } from "./AddNodeDialog";
import { HUB_ID, NODE_RADIUS, clampZoom, hitTest, seed, step } from "./graphSim";

describe("add-node helpers", () => {
  it("recognizes loopback controller URLs", () => {
    expect(isLoopbackHost("http://localhost:8000")).toBe(true);
    expect(isLoopbackHost("http://127.0.0.1:18000")).toBe(true);
    expect(isLoopbackHost("http://192.168.1.10:8000")).toBe(false);
    expect(isLoopbackHost("not a url")).toBe(false);
  });

  it("swaps a loopback host for the LAN IP and leaves others alone", () => {
    expect(controllerUrlForOtherDevices("http://localhost:8000", "192.168.1.20")).toBe("http://192.168.1.20:8000");
    expect(controllerUrlForOtherDevices("http://localhost:8000", null)).toBe("http://localhost:8000");
    expect(controllerUrlForOtherDevices("http://10.0.0.5:8000", "192.168.1.20")).toBe("http://10.0.0.5:8000");
  });

  it("builds per-OS agent commands", () => {
    const win = agentCommands("http://10.0.0.5:8000", "lyc_x_y", true);
    expect(win.install).toContain("install-agent.ps1");
    expect(win.run).toBe('$env:LYCOSA_CONTROLLER_URL = "http://10.0.0.5:8000"\n$env:LYCOSA_API_KEY = "lyc_x_y"\nlycosa-agent run');
    const nix = agentCommands("http://10.0.0.5:8000", "lyc_x_y", false);
    expect(nix.install).toContain("install-agent.sh");
    expect(nix.run).toBe("LYCOSA_CONTROLLER_URL=http://10.0.0.5:8000 \\\nLYCOSA_API_KEY=lyc_x_y \\\nlycosa-agent run");
  });
});

describe("graph simulation", () => {
  it("seeds a hub and keeps existing positions", () => {
    const first = seed(new Map(), ["a", "b"], 600, 400);
    expect(first.has(HUB_ID)).toBe(true);
    first.get("a")!.x = 123;
    const second = seed(first, ["a", "b", "c"], 600, 400);
    expect(second.get("a")!.x).toBe(123);
    expect(second.size).toBe(4);
  });

  it("settles devices near the rest distance from the hub, inside the canvas", () => {
    const bodies = seed(new Map(), ["a", "b", "c"], 800, 600);
    for (let i = 0; i < 2000; i++) step(bodies, 800, 600, 0.016);
    const hub = bodies.get(HUB_ID)!;
    for (const id of ["a", "b", "c"]) {
      const b = bodies.get(id)!;
      const d = Math.hypot(b.x - hub.x, b.y - hub.y);
      expect(d).toBeGreaterThan(100);
      expect(d).toBeLessThan(220);
      expect(b.x).toBeGreaterThanOrEqual(40);
      expect(b.x).toBeLessThanOrEqual(760);
    }
  });

  it("leaves pinned bodies where they are", () => {
    const bodies = seed(new Map(), ["a"], 800, 600);
    const a = bodies.get("a")!;
    a.pinned = true;
    a.x = 50;
    a.y = 50;
    step(bodies, 800, 600, 0.05);
    expect([a.x, a.y]).toEqual([50, 50]);
  });

  it("hit-tests and clamps zoom", () => {
    const bodies = seed(new Map(), ["a"], 800, 600);
    const a = bodies.get("a")!;
    expect(hitTest(bodies, a.x + NODE_RADIUS, a.y)?.id).toBe("a");
    expect(hitTest(bodies, -500, -500)).toBeNull();
    expect(clampZoom(10)).toBe(3);
    expect(clampZoom(0.01)).toBe(0.4);
  });
});
