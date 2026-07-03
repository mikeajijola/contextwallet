import { useState } from "react";
import type { ConnectorDTO } from "../types";

export function Connectors({
  connectors,
  onConnect,
}: {
  connectors: ConnectorDTO[];
  onConnect: (source: string) => Promise<void>;
}) {
  const [connecting, setConnecting] = useState<string | null>(null);

  const handleConnect = async (source: string) => {
    setConnecting(source);
    try {
      await onConnect(source);
    } finally {
      setConnecting(null);
    }
  };

  return (
    <div className="panel">
      <div className="panel-title">Connectors</div>
      {connectors.map((c) => (
        <div className="connector" key={c.source}>
          <div className="connector__row">
            <span className="connector__label">{c.label}</span>
            {c.status === "connected" ? (
              <span className="connector__status">connected ✓</span>
            ) : (
              <button
                className="connector__button"
                disabled={connecting === c.source}
                onClick={() => handleConnect(c.source)}
              >
                {connecting === c.source ? <span className="spinner" /> : "Connect"}
              </button>
            )}
          </div>
          {c.report && (
            <div className="connector__report">
              <div>
                auto {c.report.auto} · flagged {c.report.flagged} · deferred {c.report.deferred}
              </div>
              <div>
                {c.report.proposals.map((p) => (
                  <span className="chip" key={p.field}>
                    {p.field} — proposed
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
