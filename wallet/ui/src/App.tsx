import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import type { AskResponse, ConnectorDTO, ConsumerDTO, GraphDTO } from "./types";
import { Header } from "./components/Header";
import { Connectors } from "./components/Connectors";
import { GraphView } from "./components/GraphView";
import { Access } from "./components/Access";
import { Chat } from "./components/Chat";

const EMPTY_GRAPH: GraphDTO = { nodes: [], edges: [] };

export default function App() {
  const [connectors, setConnectors] = useState<ConnectorDTO[]>([]);
  const [consumers, setConsumers] = useState<ConsumerDTO[]>([]);
  const [viewerId, setViewerId] = useState("colin");
  const [graph, setGraph] = useState<GraphDTO>(EMPTY_GRAPH);
  const [askResult, setAskResult] = useState<AskResponse | null>(null);
  const [asking, setAsking] = useState(false);

  const viewer = consumers.find((c) => c.consumer_id === viewerId) ?? null;

  const refreshConnectors = useCallback(async () => {
    setConnectors(await api.connectors());
  }, []);

  const refreshConsumers = useCallback(async () => {
    const rows = await api.consumers();
    setConsumers(rows);
    return rows;
  }, []);

  const refreshGraphFor = useCallback(async (capId: string | null | undefined) => {
    if (!capId) {
      setGraph(EMPTY_GRAPH);
      return;
    }
    try {
      setGraph(await api.graph(capId));
    } catch {
      setGraph(EMPTY_GRAPH);
    }
  }, []);

  // initial load
  useEffect(() => {
    void refreshConnectors();
    void refreshConsumers();
  }, [refreshConnectors, refreshConsumers]);

  // redraw the map whenever the viewer (or its cap_id) changes
  useEffect(() => {
    void refreshGraphFor(viewer?.cap_id);
    setAskResult(null);
  }, [viewer?.cap_id, refreshGraphFor]);

  const handleConnect = async (source: string) => {
    await api.connect(source);
    await refreshConnectors();
    await refreshGraphFor(viewer?.cap_id);
  };

  const handleDisconnect = async (source: string) => {
    await api.disconnect(source);
    await refreshConnectors();
    await refreshGraphFor(viewer?.cap_id);
  };

  const handleToggleActive = async (id: string, active: boolean) => {
    const prev = consumers;
    setConsumers((cs) => cs.map((c) => (c.consumer_id === id ? { ...c, active } : c)));
    try {
      await api.setActive(id, active);
    } catch (e) {
      setConsumers(prev);
      throw e;
    }
    const rows = await refreshConsumers();
    if (id === viewerId) {
      await refreshGraphFor(rows.find((c) => c.consumer_id === id)?.cap_id);
    }
  };

  const handleToggleSource = async (id: string, source: string, enabled: boolean) => {
    const prev = consumers;
    setConsumers((cs) =>
      cs.map((c) =>
        c.consumer_id === id
          ? { ...c, sources: c.sources.map((s) => (s.source === source ? { ...s, enabled } : s)) }
          : c,
      ),
    );
    try {
      await api.setSource(id, source, enabled);
    } catch (e) {
      setConsumers(prev);
      throw e;
    }
    const rows = await refreshConsumers();
    if (id === viewerId) {
      await refreshGraphFor(rows.find((c) => c.consumer_id === id)?.cap_id);
    }
  };

  const handleAsk = async (questionId: string) => {
    if (!viewer?.cap_id) return;
    setAsking(true);
    try {
      setAskResult(await api.ask(viewer.cap_id, questionId));
    } finally {
      setAsking(false);
    }
  };

  return (
    <div className="app">
      <Header consumers={consumers} viewerId={viewerId} onSelect={setViewerId} />
      <div className="app__body">
        <Connectors 
          connectors={connectors} 
          onConnect={handleConnect} 
          onDisconnect={handleDisconnect}
        />

        <div className="map">
          <GraphView graph={graph} />
        </div>

        <div className="panel panel--right">
          <Access
            consumers={consumers}
            onToggleActive={handleToggleActive}
            onToggleSource={handleToggleSource}
          />
          <div style={{ flexGrow: 1, display: "flex", flexDirection: "column" }}>
            <Chat capId={viewer?.cap_id ?? null} />
          </div>
        </div>
      </div>
    </div>
  );
}
