import { useState, useEffect, useRef } from "react";
import { api } from "../api";

export function Chat({ capId }: { capId: string | null }) {
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const [messages, setMessages] = useState<{role: string, content: string}[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [initializing, setInitializing] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    initSession();
  }, []);

  useEffect(() => {
    if (activeSession) {
      loadHistory(activeSession);
    } else {
      setMessages([]);
    }
  }, [activeSession]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const initSession = async () => {
    try {
      const sess = await api.getSessions();
      if (sess.length > 0) {
        setActiveSession(sess[0].session_id);
      } else {
        await handleNewSession();
      }
    } catch (e) {
      console.error(e);
    } finally {
      setInitializing(false);
    }
  };

  const loadHistory = async (sessionId: string) => {
    try {
      const msgs = await api.getSessionHistory(sessionId);
      setMessages(msgs);
    } catch (e) {
      console.error(e);
    }
  };

  const handleNewSession = async () => {
    try {
      const res = await api.createSession();
      setActiveSession(res.session_id);
    } catch (e) {
      console.error(e);
    }
  };

  const handleSend = async () => {
    if (!input.trim() || !activeSession || !capId) return;
    const msg = input.trim();
    setInput("");
    setMessages(prev => [...prev, { role: "user", content: msg }]);
    setLoading(true);
    try {
      const res = await api.sendChatMessage(activeSession, capId, msg);
      setMessages(prev => [...prev, { role: "model", content: res.reply }]);
    } catch (e) {
      console.error(e);
      setMessages(prev => [...prev, { role: "model", content: "Error connecting to AI." }]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: "400px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
        <div className="panel-title" style={{ margin: 0 }}>Wallet Chat</div>
        <button className="connector__button" style={{ padding: "4px 8px", fontSize: "0.8em" }} onClick={handleNewSession}>New Chat</button>
      </div>
      
      <div ref={scrollRef} style={{ flexGrow: 1, overflowY: "auto", border: "1px solid var(--ground)", borderRadius: "6px", padding: "12px", display: "flex", flexDirection: "column", gap: "12px", marginBottom: "12px", background: "var(--ground)" }}>
        {messages.length === 0 && !initializing && <div style={{ color: "var(--off)", textAlign: "center", marginTop: "auto", marginBottom: "auto" }}>No messages yet. Ask something!</div>}
        {messages.map((m, i) => (
          <div key={i} style={{ alignSelf: m.role === "user" ? "flex-end" : "flex-start", background: m.role === "user" ? "var(--map-accent)" : "white", color: m.role === "user" ? "white" : "var(--ink)", padding: "8px 12px", borderRadius: "8px", maxWidth: "85%", fontSize: "0.9em", border: m.role === "user" ? "none" : "1px solid #dde2e8", whiteSpace: "pre-wrap" }}>
            {m.content}
          </div>
        ))}
        {loading && <div style={{ alignSelf: "flex-start", background: "white", padding: "8px 12px", borderRadius: "8px", border: "1px solid #dde2e8", fontSize: "0.9em" }}><span className="spinner" /></div>}
      </div>
      
      <div style={{ display: "flex", gap: "8px" }}>
        <input 
          type="text" 
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSend()}
          disabled={loading || initializing || !capId || !activeSession}
          placeholder="Ask the wallet..."
          style={{ flexGrow: 1, padding: "8px", borderRadius: "6px", border: "1px solid #dde2e8", fontFamily: "inherit" }}
        />
        <button 
          className="connector__button" 
          onClick={handleSend}
          disabled={loading || initializing || !capId || !activeSession || !input.trim()}
        >
          Send
        </button>
      </div>
    </div>
  );
}
