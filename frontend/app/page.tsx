"use client";

import { useState, useRef, useEffect } from "react";

type Message = {
  role: string;
  content: string | null;
};

type UploadState = {
  status: "idle" | "uploading" | "processing" | "done" | "failed";
  filename?: string;
  chunksProcessed?: number;
  chunksTotal?: number;
  error?: string;
};

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [upload, setUpload] = useState<UploadState>({ status: "idle" });
  const bottomRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

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
          } else if (evt.type === "chunk") {
            assistantText += evt.text;
            if (!started) {
              started = true;
              setLoading(false);
              setMessages((prev) => [...prev, { role: "assistant", content: assistantText }]);
            } else {
              setMessages((prev) => {
                const updated = [...prev];
                updated[updated.length - 1] = { role: "assistant", content: assistantText };
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
        { role: "assistant", content: "Something went wrong reaching the agent." },
      ]);
    }
  }

  async function pollUploadStatus(uploadId: string, filename: string) {
    const maxAttempts = 60; // ~2 minutes at 2s intervals
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
    e.target.value = ""; // allow re-selecting the same file later

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

  return (
    <div className="flex flex-col h-screen bg-[#F5F4EF] text-[#1F1E1C]">
      {/* Header */}
      <header className="border-b border-[#E5E2D9] py-4 px-6">
        <span className="text-sm font-medium tracking-wide text-[#5B5850]">
          AgentOS
        </span>
      </header>

      {/* Messages */}
      <main className="flex-1 overflow-y-auto">
        <div className="max-w-2xl mx-auto px-6 py-10 space-y-8">
          {messages.length === 0 && (
            <div className="text-center pt-20">
              <h1 className="text-3xl font-[family-name:var(--font-serif)] text-[#3D3B35]">
                What can I help with?
              </h1>
            </div>
          )}

          {messages.map((m, i) =>
            m.role === "user" ? (
              <div key={i} className="flex justify-end">
                <div className="bg-[#EDEAE0] rounded-2xl px-4 py-2.5 max-w-[75%]">
                  <p className="text-[15px] leading-relaxed">{m.content}</p>
                </div>
              </div>
            ) : (
              <div key={i} className="flex justify-start">
                <div className="max-w-[85%]">
                  <p className="text-[15px] leading-relaxed whitespace-pre-wrap">
                    {m.content}
                  </p>
                </div>
              </div>
            )
          )}

          {loading && (
            <div className="flex justify-start">
              <div className="flex gap-1.5 items-center py-2">
                <span className="w-1.5 h-1.5 rounded-full bg-[#9A9688] animate-bounce [animation-delay:-0.3s]" />
                <span className="w-1.5 h-1.5 rounded-full bg-[#9A9688] animate-bounce [animation-delay:-0.15s]" />
                <span className="w-1.5 h-1.5 rounded-full bg-[#9A9688] animate-bounce" />
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
            <div className="text-xs text-[#8A8676] bg-[#EDEAE0] rounded-full px-4 py-1.5 inline-block">
              {upload.status === "uploading" && `Uploading ${upload.filename}...`}
              {upload.status === "processing" &&
                `Processing ${upload.filename}${
                  upload.chunksTotal ? ` (${upload.chunksProcessed}/${upload.chunksTotal} chunks)` : "..."
                }`}
              {upload.status === "done" &&
                `✓ ${upload.filename} added (${upload.chunksProcessed} chunks)`}
              {upload.status === "failed" && `✗ ${upload.filename} failed: ${upload.error}`}
            </div>
          </div>
        </div>
      )}

      {/* Input */}
      <div className="px-6 pb-6 pt-2">
        <div className="max-w-2xl mx-auto">
          <div className="flex items-end gap-2 bg-white border border-[#E5E2D9] rounded-3xl px-4 py-3 shadow-sm focus-within:border-[#C9C5B8] transition-colors">
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
              className="shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-[#8A8676] hover:bg-[#EDEAE0] disabled:opacity-40 transition-colors"
              title="Upload a document (.pdf, .txt, .md)"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
            <textarea
              rows={1}
              className="flex-1 resize-none bg-transparent outline-none text-[15px] placeholder:text-[#A8A498] max-h-40"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  sendMessage();
                }
              }}
              placeholder="Message AgentOS..."
            />
            <button
              onClick={sendMessage}
              disabled={loading || !input.trim()}
              className="shrink-0 w-8 h-8 rounded-full bg-[#3D3B35] disabled:bg-[#D8D5C9] disabled:cursor-not-allowed flex items-center justify-center transition-colors"
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="white"
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
