import type { ConsumerDTO } from "../types";

export function Header({
  consumers,
  viewerId,
  onSelect,
}: {
  consumers: ConsumerDTO[];
  viewerId: string;
  onSelect: (id: string) => void;
}) {
  return (
    <header className="app__header">
      <div className="app__identity">
        COLIN'S WALLET <small>wallet:colin</small>
      </div>
      <div className="viewas">
        <div className="viewas__caption">Viewing as — what this capability can see</div>
        <div className="viewas__pills">
          {consumers.map((c) => {
            const revoked = !c.owner && !c.active;
            const active = viewerId === c.consumer_id;
            return (
              <button
                key={c.consumer_id}
                className={`pill ${active ? "pill--active" : ""} ${revoked ? "pill--revoked" : ""}`}
                onClick={() => onSelect(c.consumer_id)}
              >
                {c.label}
                {revoked && <span className="pill__tag">revoked</span>}
              </button>
            );
          })}
        </div>
      </div>
    </header>
  );
}
