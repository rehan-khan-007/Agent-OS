"use client";

import { useState, useRef, useEffect } from "react";

type ToolCall = {
  name: string;
  arguments: string;
};

type Message = {
  role: string;
  content: string | null;
  toolCalls?: ToolCall[];
};

type UploadState = {
  status: "idle" | "uploading" | "processing" | "done" | "failed";
  filename?: string;
  chunksProcessed?: number;
  chunksTotal?: number;
  error?: string;
};

type ConnectionState = "checking" | "connected" | "unreachable";
type Theme = "dark" | "light";

function formatToolArgs(name: string, rawArgs: string): string {
  try {
    const parsed = JSON.parse(rawArgs);
    const parts = Object.entries(parsed).map(([k, v]) => `${k}="${v}"`);
    return `${name}(${parts.join(", ")})`;
  } catch {
    return `${name}(${rawArgs})`;
  }
}

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [upload, setUpload] = useState<UploadState>({ status: "idle" });
  const [connection, setConnection] = useState<ConnectionState>("checking");
  const [theme, setTheme] = useState<Theme>("dark");
  const bottomRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const saved = localStorage.getItem("agentos-theme") as Theme | null;
    const initial = saved ?? "dark";
    setTheme(initial);
    document.documentElement.classList.toggle("dark", initial === "dark");
  }, []);

  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.classList.toggle("dark", next === "dark");
    localStorage.setItem("agentos-theme", next);
  }

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  useEffect(() => {
    let cancelled = false;
    fetch(`${process.env.NEXT_PUBLIC_API_URL}/health`)
      .then((res) => {
        if (!cancelled) setConnection(res.ok ? "connected" : "unreachable");
      })
      .catch(() => {
        if (!cancelled) setConnection("unreachable");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function sendMessage() {
    if (!input.trim()) return;

    const userMessage: Message = { role: "user", content: input };
    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setLoading(true);

    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/agents/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: input, session_id: sessionId }),
      });

      if (!res.body) throw new Error("No response body");

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let assistantText = "";
      let toolCalls: ToolCall[] = [];
      let started = false;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() || "";

        for (const part of parts) {
          if (!part.startsWith("data: ")) continue;
          const evt = JSON.parse(part.slice(6));

          if (evt.type === "session") {
            setSessionId(evt.session_id);
          } else if (evt.type === "tool_call") {
            toolCalls = [...toolCalls, { name: evt.name, arguments: evt.arguments }];
            if (!started) {
              started = true;
              setLoading(false);
              setMessages((prev) => [...prev, { role: "assistant", content: "", toolCalls }]);
            } else {
              setMessages((prev) => {
                const updated = [...prev];
                updated[updated.length - 1] = { role: "assistant", content: assistantText, toolCalls };
                return updated;
              });
            }
          } else if (evt.type === "chunk") {
            assistantText += evt.text;
            if (!started) {
              started = true;
              setLoading(false);
              setMessages((prev) => [...prev, { role: "assistant", content: assistantText, toolCalls }]);
            } else {
              setMessages((prev) => {
                const updated = [...prev];
                updated[updated.length - 1] = { role: "assistant", content: assistantText, toolCalls };
                return updated;
              });
            }
          }
        }
      }
    } catch {
      setLoading(false);
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "Connection to the agent failed. Try again." },
      ]);
    }
  }

  async function pollUploadStatus(uploadId: string, filename: string) {
    const maxAttempts = 60;
    for (let i = 0; i < maxAttempts; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      try {
        const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/documents/upload/${uploadId}/status`);
        const data = await res.json();

        if (data.status === "done") {
          setUpload({
            status: "done",
            filename,
            chunksProcessed: data.chunks_processed,
            chunksTotal: data.chunks_total,
          });
          setTimeout(() => setUpload({ status: "idle" }), 4000);
          return;
        }
        if (data.status === "failed") {
          setUpload({ status: "failed", filename, error: data.error });
          setTimeout(() => setUpload({ status: "idle" }), 6000);
          return;
        }
        setUpload({
          status: "processing",
          filename,
          chunksProcessed: data.chunks_processed,
          chunksTotal: data.chunks_total,
        });
      } catch {
        // transient poll failure — keep trying until maxAttempts
      }
    }
    setUpload({ status: "failed", filename, error: "Timed out waiting for processing." });
    setTimeout(() => setUpload({ status: "idle" }), 6000);
  }

  async function handleFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = "";

    setUpload({ status: "uploading", filename: file.name });

    try {
      const formData = new FormData();
      formData.append("file", file);

      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/documents/upload`, {
        method: "POST",
        body: formData,
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Upload failed." }));
        setUpload({ status: "failed", filename: file.name, error: err.detail });
        setTimeout(() => setUpload({ status: "idle" }), 6000);
        return;
      }

      const data = await res.json();
      pollUploadStatus(data.upload_id, file.name);
    } catch {
      setUpload({ status: "failed", filename: file.name, error: "Network error during upload." });
      setTimeout(() => setUpload({ status: "idle" }), 6000);
    }
  }

  const connectionColor =
    connection === "connected" ? "bg-[var(--accent)]" : connection === "checking" ? "bg-[var(--info)]" : "bg-[var(--danger)]";
  const connectionLabel =
    connection === "connected" ? "online" : connection === "checking" ? "connecting" : "unreachable";

  return (
    <div className="flex flex-col h-screen bg-[var(--background)] text-[var(--foreground)]">
      {/* Header — instrument panel strip */}
      <header className="border-b border-[var(--hairline)] py-3 px-6 flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <span className={`w-1.5 h-1.5 rounded-full ${connectionColor} ${connection === "checking" ? "animate-pulse" : ""}`} />
          <span className="font-[family-name:var(--font-geist-mono)] text-[11px] tracking-[0.15em] uppercase text-[var(--muted)]">
            agent-os
          </span>
        </div>
        <div className="flex items-center gap-4">
          <span className="font-[family-name:var(--font-geist-mono)] text-[10px] tracking-[0.1em] uppercase text-[var(--faint)]">
            {connectionLabel}
          </span>
          <button
            onClick={toggleTheme}
            className="w-7 h-7 rounded flex items-center justify-center text-[var(--faint)] hover:text-[var(--muted)] hover:bg-[var(--panel)] transition-colors"
            title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          >
            {theme === "dark" ? (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="4" />
                <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" strokeLinecap="round" />
              </svg>
            ) : (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            )}
          </button>
        </div>
      </header>

      {/* Messages */}
      <main className="flex-1 overflow-y-auto">
        <div className="max-w-2xl mx-auto px-6 py-10 space-y-7">
          {messages.length === 0 && (
            <div className="pt-24">
              <p className="font-[family-name:var(--font-geist-mono)] text-[11px] tracking-[0.15em] uppercase text-[var(--faint)] mb-3">
                session idle
              </p>
              <h1 className="text-2xl text-[var(--foreground)] font-normal">
                What do you need looked up?
              </h1>
            </div>
          )}

          {messages.map((m, i) =>
            m.role === "user" ? (
              <div key={i} className="flex justify-end">
                <div className="bg-[var(--panel)] border border-[var(--hairline)] rounded px-4 py-2.5 max-w-[75%]">
                  <p className="text-[15px] leading-relaxed">{m.content}</p>
                </div>
              </div>
            ) : (
              <div key={i} className="flex justify-start">
                <div className="max-w-[85%] w-full">
                  {m.toolCalls && m.toolCalls.length > 0 && (
                    <div className="mb-2.5 border-l-2 border-[var(--accent)]/40 pl-3 space-y-1">
                      {m.toolCalls.map((tc, j) => (
                        <p
                          key={j}
                          className="font-[family-name:var(--font-geist-mono)] text-[12px] text-[var(--accent)]/80"
                        >
                          <span className="text-[var(--faint)]">{">"}</span> {formatToolArgs(tc.name, tc.arguments)}
                        </p>
                      ))}
                    </div>
                  )}
                  {m.content && (
                    <p className="text-[15px] leading-relaxed whitespace-pre-wrap text-[var(--foreground)]">
                      {m.content}
                    </p>
                  )}
                </div>
              </div>
            )
          )}

          {loading && (
            <div className="flex justify-start">
              <div className="flex gap-1.5 items-center py-2">
                <span className="w-1.5 h-1.5 rounded-full bg-[var(--faint)] animate-bounce [animation-delay:-0.3s]" />
                <span className="w-1.5 h-1.5 rounded-full bg-[var(--faint)] animate-bounce [animation-delay:-0.15s]" />
                <span className="w-1.5 h-1.5 rounded-full bg-[var(--faint)] animate-bounce" />
              </div>
            </div>
          )}

          <div ref={bottomRef} />
        </div>
      </main>

      {/* Upload status pill */}
      {upload.status !== "idle" && (
        <div className="px-6 pb-2">
          <div className="max-w-2xl mx-auto">
            <div className="font-[family-name:var(--font-geist-mono)] text-[11px] text-[var(--muted)] bg-[var(--panel)] border border-[var(--hairline)] rounded px-3 py-1.5 inline-block">
              {upload.status === "uploading" && `uploading ${upload.filename}...`}
              {upload.status === "processing" &&
                `processing ${upload.filename}${
                  upload.chunksTotal ? ` (${upload.chunksProcessed}/${upload.chunksTotal})` : "..."
                }`}
              {upload.status === "done" && (
                <span className="text-[var(--accent)]">
                  ✓ {upload.filename} indexed ({upload.chunksProcessed} chunks)
                </span>
              )}
              {upload.status === "failed" && (
                <span className="text-[var(--danger)]">
                  ✗ {upload.filename} failed: {upload.error}
                </span>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Input */}
      <div className="px-6 pb-6 pt-2">
        <div className="max-w-2xl mx-auto">
          <div className="flex items-end gap-2 bg-[var(--panel)] border border-[var(--hairline)] rounded px-4 py-3 focus-within:border-[var(--accent)]/50 transition-colors">
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,.txt,.md"
              className="hidden"
              onChange={handleFileSelected}
            />
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={upload.status === "uploading" || upload.status === "processing"}
              className="shrink-0 w-8 h-8 rounded flex items-center justify-center text-[var(--faint)] hover:text-[var(--muted)] hover:bg-[var(--background)] disabled:opacity-40 transition-colors"
              title="Upload a document (.pdf, .txt, .md)"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
            <textarea
              rows={1}
              className="flex-1 resize-none bg-transparent outline-none text-[15px] placeholder:text-[var(--faint)] placeholder:font-[family-name:var(--font-geist-mono)] placeholder:text-[13px] max-h-40"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  sendMessage();
                }
              }}
              placeholder="> message agent-os"
            />
            <button
              onClick={sendMessage}
              disabled={loading || !input.trim()}
              className="shrink-0 w-8 h-8 rounded bg-[var(--accent)] disabled:bg-[var(--hairline)] disabled:cursor-not-allowed flex items-center justify-center transition-colors"
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="var(--accent-foreground)"
                strokeWidth="2.5"
              >
                <path d="M12 19V5M5 12l7-7 7 7" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
