import { render } from "@testing-library/react";
import { QueryClient } from "@tanstack/react-query";

import { Root } from "../App";
import { setTransport } from "../ipc/transport";
import type { FakeTransport } from "./fakeTransport";

/** Render the whole app against a fake Rust side. */
export function renderApp(fake: FakeTransport) {
  setTransport(fake);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return { ...render(<Root client={client} />), client };
}
