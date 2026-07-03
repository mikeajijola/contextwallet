import { useState } from "react";
import { api } from "../api";
import type { AskResponse, CardDTO } from "../types";

export function AskResult({ result, capId }: { result: AskResponse | null; capId: string | null }) {
  if (!result) return null;
  if (result.answer_kind === "absent") {
    return <div className="card card--absent">Nothing here for this viewer.</div>;
  }
  return (
    <div className="cards">
      {result.cards.map((card, i) => (
        <Card key={i} card={card} capId={capId} />
      ))}
    </div>
  );
}

function Card({ card, capId }: { card: CardDTO; capId: string | null }) {
  switch (card.kind) {
    case "agreed":
      return (
        <div className="card">
          <div className="card__value-row">
            <span className="card__value">{card.value}</span>
            <span className="card__meta">
              {card.source}
              {card.date ? ` · ${card.date}` : ""}
            </span>
          </div>
        </div>
      );
    case "conflict_ordered":
      return <ConflictCard card={card} ordered />;
    case "conflict_unordered":
      return <ConflictCard card={card} ordered={false} />;
    case "signal":
      return <SignalCard card={card} capId={capId} />;
    case "refusal":
      return <div className="card card--refusal">not available to you</div>;
    default:
      return null;
  }
}

function ConflictCard({ card, ordered }: { card: CardDTO; ordered: boolean }) {
  const values = card.values ?? [];
  return (
    <div className="card">
      <div className="card__title">{card.ontology_node}</div>
      {!ordered && <span className="badge">no timestamp — choose</span>}
      {values.map((v, i) => {
        const isDefault = ordered && card.default_selection === i;
        const isLoser = ordered && card.default_selection !== null && card.default_selection !== undefined && card.default_selection !== i;
        return (
          <div
            key={i}
            className={`card__conflict-value ${isDefault ? "card__conflict-value--default" : ""} ${
              isLoser ? "card__conflict-value--loser" : ""
            }`}
          >
            <span className="card__value">
              {v.value}
              {isDefault ? " · most recent" : ""}
            </span>
            <span className="card__meta">
              {v.source}
              {v.date ? ` · ${v.date}` : ""}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function SignalCard({ card, capId }: { card: CardDTO; capId: string | null }) {
  const [transcript, setTranscript] = useState<string | null>(null);
  const [refused, setRefused] = useState(false);
  const [loading, setLoading] = useState(false);

  const openTranscript = async () => {
    if (!capId || !card.transcript_cell_id) return;
    setLoading(true);
    setRefused(false);
    try {
      const res = await api.fetchCell(capId, card.transcript_cell_id);
      if (res.refusal) {
        setRefused(true);
      } else {
        setTranscript(res.value ?? "");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="card">
      <div className="card__title">signal</div>
      <div>{card.participants}</div>
      <div className="card__meta">
        {card.channel} · {card.topic}
      </div>
      {card.follow_up && <div className="card__meta">{card.follow_up}</div>}
      {card.transcript_cell_id && (
        <div className="card__transcript-chip">
          🔒 transcript
          <button className="card__open-transcript" onClick={openTranscript} disabled={loading}>
            {loading ? "Opening…" : "Open transcript"}
          </button>
        </div>
      )}
      {refused && (
        <div className="card card--refusal" style={{ marginTop: 8 }}>
          not available to you
        </div>
      )}
      {transcript !== null && <div className="transcript-panel">{transcript}</div>}
    </div>
  );
}
