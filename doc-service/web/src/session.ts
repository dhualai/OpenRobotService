import type { QueryClient } from "@tanstack/react-query";

export function resetSession(qc: QueryClient) {
  qc.clear();
}
