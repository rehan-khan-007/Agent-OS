import { describe, it, expect } from "vitest";
import { formatToolArgs } from "./format";

describe("formatToolArgs", () => {
  it("formats a single-argument tool call", () => {
    const result = formatToolArgs("retrieve", '{"query":"GRAPE algorithm"}');
    expect(result).toBe('retrieve(query="GRAPE algorithm")');
  });

  it("formats a multi-argument tool call, preserving argument order", () => {
    const result = formatToolArgs(
      "retrieve",
      '{"query":"Newton-Raphson","top_k":"3"}'
    );
    expect(result).toBe('retrieve(query="Newton-Raphson", top_k="3")');
  });

  it("formats a tool call with no arguments", () => {
    const result = formatToolArgs("web_search", "{}");
    expect(result).toBe("web_search()");
  });

  it("falls back to raw arguments text when JSON is malformed", () => {
    const result = formatToolArgs("retrieve", "{not valid json");
    expect(result).toBe("retrieve({not valid json)");
  });

  it("falls back gracefully on an empty arguments string", () => {
    const result = formatToolArgs("calculator", "");
    expect(result).toBe("calculator()");
  });
});
