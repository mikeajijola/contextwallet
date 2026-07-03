import { useState } from "react";
import type { ConnectorDTO } from "../types";

// Reusing the switch style logic from Access
function Switch({ on, onChange, disabled }: { on: boolean; onChange: (next: boolean) => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      className={`switch ${on ? "switch--on" : ""} ${disabled ? "switch--disabled" : ""}`}
      role="switch"
      aria-checked={on}
      onClick={() => {
        if (!disabled) onChange(!on);
      }}
      disabled={disabled}
      style={{ opacity: disabled ? 0.5 : 1, cursor: disabled ? "not-allowed" : "pointer" }}
    >
      <span className="switch__knob" />
    </button>
  );
}

export function Connectors({
  connectors,
  onConnect,
  onDisconnect,
}: {
  connectors: ConnectorDTO[];
  onConnect: (source: string) => Promise<void>;
  onDisconnect: (source: string) => Promise<void>;
}) {
  const [connecting, setConnecting] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<boolean>(true);
  const [expandedSource, setExpandedSource] = useState<string | null>(null);

  const handleToggleConnect = async (source: string, isConnected: boolean) => {
    setConnecting(source);
    try {
      if (isConnected) {
        await onDisconnect(source);
      } else {
        await onConnect(source);
      }
    } finally {
      setConnecting(null);
    }
  };

  return (
    <div className="panel">
      <div 
        className="panel-title" 
        style={{ display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "pointer", userSelect: "none" }}
        onClick={() => setExpanded(!expanded)}
      >
        <span>Connectors</span>
        <span style={{ fontSize: "0.8em", opacity: 0.6 }}>{expanded ? "▲" : "▼"}</span>
      </div>

      {expanded && (
        <div style={{ marginTop: "12px" }}>
          {connectors.map((c) => {
            const isConnected = c.status === "connected";
            const isExpanded = expandedSource === c.source;
            const isConnecting = connecting === c.source;

            return (
              <div className="connector" key={c.source}>
                <div 
                  className="connector__row" 
                  style={{ cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "space-between" }}
                  onClick={() => setExpandedSource(isExpanded ? null : c.source)}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    <span style={{ fontSize: "0.8em", opacity: 0.5 }}>{isExpanded ? "▼" : "▶"}</span>
                    <span className="connector__label" style={{ fontWeight: 600 }}>{c.label}</span>
                  </div>
                  
                  <div onClick={(e) => e.stopPropagation()} style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    {isConnecting && <span className="spinner" style={{ marginRight: "8px" }} />}
                    <span style={{ fontSize: "0.85em", color: isConnected ? "var(--granted)" : "var(--off)" }}>
                      {isConnected ? "Connected" : "Disconnected"}
                    </span>
                    <Switch 
                      on={isConnected} 
                      onChange={() => handleToggleConnect(c.source, isConnected)} 
                      disabled={isConnecting} 
                    />
                  </div>
                </div>

                {isExpanded && isConnected && c.report && (
                  <div className="connector__report" style={{ marginTop: "16px", paddingTop: "16px", borderTop: "1px dashed #dde2e8" }}>
                    <div style={{ marginBottom: "16px", color: "var(--off)", fontSize: "0.9em" }}>
                      auto {c.report.auto} · flagged {c.report.flagged} · deferred {c.report.deferred}
                    </div>

                    {c.report.schema && c.report.schema.length > 0 && (
                      <div>
                        <div style={{ fontSize: "0.85em", fontWeight: 600, marginBottom: "12px", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                          Full Schema
                        </div>
                        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "8px 16px", fontSize: "0.9em" }}>
                          <div style={{ fontWeight: 600, color: "var(--off)", borderBottom: "1px solid #dde2e8", paddingBottom: "4px" }}>Field</div>
                          <div style={{ fontWeight: 600, color: "var(--off)", borderBottom: "1px solid #dde2e8", paddingBottom: "4px" }}>Mapped Node</div>
                          <div style={{ fontWeight: 600, color: "var(--off)", borderBottom: "1px solid #dde2e8", paddingBottom: "4px" }}>Status</div>
                          
                          {c.report.schema.map((f, i) => (
                            <div style={{ display: "contents" }} key={i}>
                              <div style={{ fontFamily: "var(--font-mono)", paddingTop: "4px" }}>{f.name}</div>
                              <div style={{ paddingTop: "4px" }}>
                                {f.node ? <span className="chip">{f.node}</span> : <span style={{ color: "var(--off)" }}>—</span>}
                              </div>
                              <div style={{ paddingTop: "4px", color: f.status === "deferred" ? "var(--off)" : "inherit" }}>
                                {f.status}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
                {isExpanded && !isConnected && (
                  <div style={{ marginTop: "12px", paddingTop: "12px", borderTop: "1px dashed #dde2e8", fontSize: "0.9em", color: "var(--off)" }}>
                    Connect this source to view its schema.
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
