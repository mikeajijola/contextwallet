import { useState } from "react";
import type { ConsumerDTO } from "../types";

function Switch({ on, onChange }: { on: boolean; onChange: (next: boolean) => void }) {
  return (
    <button
      type="button"
      className={`switch ${on ? "switch--on" : ""}`}
      role="switch"
      aria-checked={on}
      onClick={() => onChange(!on)}
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

  return (
    <div>
      <div className="panel-title">Access</div>
      {consumers.map((c) => (
        <div className="consumer" key={c.consumer_id}>
          <div className="consumer__row">
            <span className="consumer__label">
              {c.label}
              {c.owner && <span className="chip chip--owner">owner</span>}
            </span>
            {!c.owner && (
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
            )}
          </div>
          {c.consumer_id === "partner" && <div className="consumer__sublabel">Stripe deal only</div>}
          {!c.owner && (
            <div className="consumer__sources">
              {c.sources.map((s) => (
                <div className="source-row" key={s.source}>
                  <span>{s.label}</span>
                  {s.note ? (
                    <span className="source-row__note">🔒 {s.note}</span>
                  ) : (
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
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
      {error && <div className="inline-error">{error}</div>}
    </div>
  );
}
