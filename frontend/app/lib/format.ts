/**
 * Formats a tool call's name and raw JSON-string arguments into a
 * readable, terminal-style trace line for display in the chat UI,
 * e.g. formatToolArgs("retrieve", '{"query":"GRAPE"}') ->
 * 'retrieve(query="GRAPE")'.
 *
 * Falls back to showing the raw arguments string unparsed if it
 * isn't valid JSON, rather than throwing and breaking the whole
 * message render over one malformed tool call.
 */
export function formatToolArgs(name: string, rawArgs: string): string {
  try {
    const parsed = JSON.parse(rawArgs);
    const parts = Object.entries(parsed).map(([k, v]) => `${k}="${v}"`);
    return `${name}(${parts.join(", ")})`;
  } catch {
    return `${name}(${rawArgs})`;
  }
}
