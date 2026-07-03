import type { AskResponse, ConnectorDTO, ConsumerDTO, FetchResponse, GraphDTO } from "./types";

const BASE = "http://127.0.0.1:8000";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "content-type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    throw new Error(`${init?.method ?? "GET"} ${path} -> ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  connectors: () => req<ConnectorDTO[]>("/connectors"),
  connect: (source: string) => req<ConnectorDTO>(`/connectors/${source}/connect`, { method: "POST" }),
  consumers: () => req<ConsumerDTO[]>("/consumers"),
  setActive: (id: string, active: boolean) =>
    req<ConsumerDTO>(`/consumers/${id}`, { method: "PATCH", body: JSON.stringify({ active }) }),
  setSource: (id: string, source: string, enabled: boolean) =>
    req<ConsumerDTO>(`/consumers/${id}/sources`, {
      method: "PATCH",
      body: JSON.stringify({ source, enabled }),
    }),
  graph: (capId: string) => req<GraphDTO>(`/graph?cap_id=${encodeURIComponent(capId)}`),
  ask: (capId: string, questionId: string) =>
    req<AskResponse>("/ask", {
      method: "POST",
      body: JSON.stringify({ cap_id: capId, question_id: questionId }),
    }),
  fetchCell: (capId: string, cellId: string) =>
    req<FetchResponse>("/fetch", {
      method: "POST",
      body: JSON.stringify({ cap_id: capId, cell_id: cellId }),
    }),
};
