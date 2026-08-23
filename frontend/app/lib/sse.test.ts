import { describe, it, expect } from "vitest";
import { parseSSEChunk } from "./sse";

describe("parseSSEChunk", () => {
  it("parses a single complete frame", () => {
    const { events, remainingBuffer } = parseSSEChunk(
      'data: {"type":"session","session_id":"abc"}\n\n',
      ""
    );
    expect(events).toEqual([{ type: "session", session_id: "abc" }]);
    expect(remainingBuffer).toBe("");
  });

  it("parses multiple complete frames arriving in one chunk", () => {
    const raw =
      'data: {"type":"chunk","text":"hello "}\n\n' +
      'data: {"type":"chunk","text":"world"}\n\n';
    const { events, remainingBuffer } = parseSSEChunk(raw, "");
    expect(events).toEqual([
      { type: "chunk", text: "hello " },
      { type: "chunk", text: "world" },
    ]);
    expect(remainingBuffer).toBe("");
  });

  it("carries over an incomplete trailing frame to the buffer", () => {
    // A network chunk can split a frame in half — the incomplete
    // tail must not be parsed yet, just carried forward.
    const raw = 'data: {"type":"chunk","te';
    const { events, remainingBuffer } = parseSSEChunk(raw, "");
    expect(events).toEqual([]);
    expect(remainingBuffer).toBe('data: {"type":"chunk","te');
  });

  it("completes a frame that was split across two chunks", () => {
    const first = parseSSEChunk('data: {"type":"chunk","te', "");
    expect(first.events).toEqual([]);

    const second = parseSSEChunk('xt":"hi"}\n\n', first.remainingBuffer);
    expect(second.events).toEqual([{ type: "chunk", text: "hi" }]);
    expect(second.remainingBuffer).toBe("");
  });

  it("ignores lines that don't start with 'data: '", () => {
    const raw = ": this is a comment line, not a data frame\n\n" +
      'data: {"type":"session","session_id":"x"}\n\n';
    const { events } = parseSSEChunk(raw, "");
    expect(events).toEqual([{ type: "session", session_id: "x" }]);
  });

  it("skips a malformed JSON frame instead of throwing", () => {
    const raw =
      "data: {this is not valid json\n\n" +
      'data: {"type":"chunk","text":"still works"}\n\n';
    const { events } = parseSSEChunk(raw, "");
    // The malformed frame is silently dropped; the valid one after
    // it still comes through — one bad frame shouldn't break the
    // whole stream.
    expect(events).toEqual([{ type: "chunk", text: "still works" }]);
  });

  it("handles an empty chunk with no buffered content", () => {
    const { events, remainingBuffer } = parseSSEChunk("", "");
    expect(events).toEqual([]);
    expect(remainingBuffer).toBe("");
  });
});
