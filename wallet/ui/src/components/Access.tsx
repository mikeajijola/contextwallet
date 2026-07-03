import { useState } from "react";
import type { ConsumerDTO } from "../types";

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

export function Access({
  consumers,
  onToggleActive,
  onToggleSource,
}: {
  consumers: ConsumerDTO[];
  onToggleActive: (id: string, active: boolean) => Promise<void>;
  onToggleSource: (id: string, source: string, enabled: boolean) => Promise<void>;
}) {
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<boolean>(true);
  const [expandedConsumer, setExpandedConsumer] = useState<string | null>(null);

  return (
    <div style={{ marginBottom: "16px" }}>
      <div 
        className="panel-title"
        style={{ display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "pointer", userSelect: "none" }}
        onClick={() => setExpanded(!expanded)}
      >
        <span>Access</span>
        <span style={{ fontSize: "0.8em", opacity: 0.6 }}>{expanded ? "▲" : "▼"}</span>
      </div>

      {expanded && (
        <div style={{ marginTop: "12px" }}>
          {consumers.map((c) => {
            const isExpanded = expandedConsumer === c.consumer_id;

            return (
              <div className="consumer" key={c.consumer_id}>
                <div 
                  className="consumer__row"
                  style={{ cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "space-between" }}
                  onClick={() => setExpandedConsumer(isExpanded ? null : c.consumer_id)}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    <span style={{ fontSize: "0.8em", opacity: 0.5 }}>{isExpanded ? "▼" : "▶"}</span>
                    <span className="consumer__label" style={{ fontWeight: 600 }}>
                      {c.label}
                      {c.owner && <span className="chip chip--owner" style={{ marginLeft: "8px" }}>owner</span>}
                    </span>
                  </div>
                  
                  {!c.owner && (
                    <div onClick={(e) => e.stopPropagation()} style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                      <span style={{ fontSize: "0.85em", color: c.active ? "var(--granted)" : "var(--off)" }}>
                        {c.active ? "Active" : "Revoked"}
                      </span>
                      <Switch
                        on={c.active}
                        onChange={async (next) => {
                          try {
                            await onToggleActive(c.consumer_id, next);
                            setError(null);
                          } catch {
                            setError(`Could not update ${c.label} — try again`);
                          }
                        }}
                      />
                    </div>
                  )}
                </div>

                {isExpanded && (
                  <div style={{ marginTop: "12px", paddingTop: "12px", borderTop: "1px dashed #dde2e8" }}>
                    {c.consumer_id === "partner" && <div className="consumer__sublabel" style={{ marginBottom: "12px" }}>Stripe deal only</div>}
                    
                    {c.owner ? (
                      <div style={{ fontSize: "0.9em", color: "var(--off)" }}>The owner has access to all active connectors.</div>
                    ) : (
                      <div className="consumer__sources">
                        {c.sources.map((s) => (
                          <div className="source-row" key={s.source} style={{ padding: "4px 0" }}>
                            <span>{s.label}</span>
                            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                              {s.note && <span className="source-row__note">🔒 {s.note}</span>}
                              <Switch
                                on={s.enabled}
                                onChange={async (next) => {
                                  try {
                                    await onToggleSource(c.consumer_id, s.source, next);
                                    setError(null);
                                  } catch {
                                    setError(`Could not update ${s.label} — try again`);
                                  }
                                }}
                              />
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
          {error && <div className="inline-error" style={{ marginTop: "8px" }}>{error}</div>}
        </div>
      )}
    </div>
  );
}
