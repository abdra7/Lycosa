// A small inline icon set (24px grid, stroke icons), so the app ships no
// icon font and needs no network.

const PATHS: Record<string, string> = {
  nodes: "M4 5h16v5H4zM4 14h16v5H4zM7 7.5h.01M7 16.5h.01",
  tasks: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM10 8.5v7l6-3.5z",
  chat: "M4 5h16v11H9l-5 4z",
  workflows: "M6 3v6M6 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM6 15v6M18 6a3 3 0 1 0 0 .01M18 9v3a3 3 0 0 1-3 3H9",
  knowledge: "M4 5a2 2 0 0 1 2-2h12v16H6a2 2 0 0 0-2 2zM4 5v16M8 7h7",
  providers: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2v6M12 16v6M2 12h6M16 12h6",
  admin: "M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6zM9 12l2 2 4-4",
  computer: "M3 4h18v12H3zM8 20h8M12 16v4",
  add: "M12 5v14M5 12h14",
  minus: "M5 12h14",
  fit: "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5",
  expand: "M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7",
  collapse: "M4 14h6v6M20 10h-6V4M10 14l-7 7M14 10l7-7",
  back: "M15 5l-7 7 7 7",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
  delete: "M5 7h14M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3",
  copy: "M9 9h11v11H9zM5 15H4V4h11v1",
  close: "M6 6l12 12M18 6L6 18",
  play: "M8 5v14l11-7z",
  stop: "M7 7h10v10H7z",
  send: "M4 12l16-8-6 16-2-6z",
  logout: "M15 4h4v16h-4M10 8l-4 4 4 4M6 12h11",
  sun: "M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4",
  moon: "M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z",
  person: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0",
  radar: "M12 12l6-6M12 3a9 9 0 1 0 9 9M12 7a5 5 0 1 0 5 5",
  check: "M5 12l5 5L20 7",
  circle: "M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16z",
  warning: "M12 3l10 18H2zM12 10v5M12 18h.01",
  info: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 11v6M12 7.5h.01",
  star: "M12 3l2.9 6 6.6.9-4.8 4.6 1.2 6.5L12 18l-5.9 3 1.2-6.5L2.5 9.9 9.1 9z",
  block: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM5.6 5.6l12.8 12.8",
  key: "M15 7a4 4 0 1 1-3.9 5H3v3h3v3h3v-3h2.1A4 4 0 0 1 15 7z",
  folder: "M3 6h7l2 2h9v11H3z",
  upload: "M12 16V4M7 9l5-5 5 5M4 20h16",
  view: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
  list: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
  hub: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM5 5l4.5 4.5M19 5l-4.5 4.5M5 19l4.5-4.5M19 19l-4.5-4.5",
  link: "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1",
  lock: "M6 11h12v10H6zM8 11V7a4 4 0 0 1 8 0v4",
  pause: "M8 5v14M16 5v14",
  route: "M6 19a2 2 0 1 0 0-.01M18 5a2 2 0 1 0 0-.01M6 17V9a4 4 0 0 1 4-4h6M18 7v8a4 4 0 0 1-4 4H8",
  science: "M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4a2 2 0 0 0 1.8-3l-5-9V3",
  memory: "M5 6h14v12H5zM9 6V3M15 6V3M9 21v-3M15 21v-3M2 10h3M2 14h3M19 10h3M19 14h3",
  pulse: "M3 12h4l3-8 4 16 3-8h4",
  badge: "M4 7h16v13H4zM9 4h6v3H9zM8 12h8M8 16h5",
  sparkle: "M12 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2z",
  shield: "M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6z",
  backup: "M4 7h16v13H4zM8 4h8M10 12h4",
  privacy: "M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6zM12 10v4",
  swap: "M7 7h13l-3-3M17 17H4l3 3",
};

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 18, className }: { name: IconName | string; size?: number; className?: string }) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={PATHS[name] ?? PATHS["circle"]} />
    </svg>
  );
}
