import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

import { chatStore } from "../features/chat/chatStore";

// jsdom has no ResizeObserver; the graph view only needs it to exist
class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver ??= NoopResizeObserver;

afterEach(() => {
  cleanup();
  chatStore.bindProfile("__reset__");
  localStorage.clear();
});
