"use client";

import { useState } from "react";

type Message = {
  role: string;
  content: string | null;
};

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function sendMessage() {
    if (!input.trim()) return;

    const userMessage: Message = { role: "user", content: input };
    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setLoading(true);

    try {
      const res = await fetch("http://localhost:8000/agents/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: input,
          session_id: sessionId,
        }),
      });

      const data = await res.json();
      setSessionId(data.session_id);
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: data.response },
      ]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "Error: could not reach the agent." },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex flex-col h-screen max-w-2xl mx-auto p-4">
      <h1 className="text-2xl font-bold mb-4">AgentOS Chat</h1>

      <div className="flex-1 overflow-y-auto space-y-3 mb-4">
        {messages.map((m, i) => (
          <div
            key={i}
            className={`p-3 rounded-lg max-w-[80%] ${
              m.role === "user"
                ? "bg-blue-600 text-white ml-auto"
                : "bg-gray-200 text-black mr-auto"
            }`}
          >
            {m.content}
          </div>
        ))}
        {loading && (
          <div className="p-3 rounded-lg bg-gray-200 text-black mr-auto max-w-[80%]">
            Thinking...
          </div>
        )}
      </div>

      <div className="flex gap-2">
        <input
          className="flex-1 border rounded-lg px-3 py-2 text-black"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && sendMessage()}
          placeholder="Ask something..."
        />
        <button
          onClick={sendMessage}
          className="bg-blue-600 text-white px-4 py-2 rounded-lg"
          disabled={loading}
        >
          Send
        </button>
      </div>
    </main>
  );
}