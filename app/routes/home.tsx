import type { Route } from "./+types/home";

import { LiveViewer } from "~/features/live-view/live-viewer";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "CCTV Viewer" },
    { name: "description", content: "Always-on low-latency CCTV viewer." },
  ];
}

export default function Home() {
  return <LiveViewer />;
}
