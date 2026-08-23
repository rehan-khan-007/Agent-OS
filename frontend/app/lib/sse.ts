/**
 * Pure SSE (Server-Sent Events) parsing logic, extracted from the
 * chat page's streaming handler so it can be unit tested without a
 * real fetch/ReadableStream — mirrors the backend's approach of
 * pulling parsing/decision logic out into standalone functions
 * (see reciprocal_rank_fusion, _is_retryable) rather than leaving it
 * tangled inside I/O-bound code.
 *
 * SSE frames are separated by a blank line ("\n\n"). A raw decoded
 * text chunk from the stream may contain zero, one, or several
 * complete frames, and may also end mid-frame — the incomplete tail
 * must be carried over and prepended to the next chunk. Each
 * complete frame is expected in the form "data: <json>\n\n"; any
 * line not starting with "data: " is ignored (this project's
 * backend only ever sends "data: " lines, but the frontend shouldn't
 * assume that other output can never appear on this stream — for
 * example if a load balancer ever injects a keep-alive comment line).
 */

export type SSEEvent = Record<string, unknown>;

export function parseSSEChunk(
  incomingText: string,
  bufferedText: string
): { events: SSEEvent[]; remainingBuffer: string } {
  const combined = bufferedText + incomingText;
  const frames = combined.split("\n\n");
  // The last element is either an empty string (if combined ended
  // exactly on a frame boundary) or an incomplete trailing frame —
  // either way, it's not a complete frame yet, so it's carried over.
  const remainingBuffer = frames.pop() ?? "";

  const events: SSEEvent[] = [];
  for (const frame of frames) {
    if (!frame.startsWith("data: ")) continue;
    const jsonText = frame.slice("data: ".length);
    try {
      events.push(JSON.parse(jsonText));
    } catch {
      // Malformed frame — skip it rather than crash the whole stream
      // over one bad event.
      continue;
    }
  }

  return { events, remainingBuffer };
}
