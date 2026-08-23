"""
Real concurrent-request load test against the LIVE deployed
AgentOS backend — genuinely different from load_test_queue.py, which
tested the queue/worker layer with a zero-cost stub handler. This
hits the actual /agents/chat endpoint with real, simultaneous HTTP
requests, exercising real LLM calls under real concurrency.

What this specifically checks, beyond "did it survive N requests":
CROSS-REQUEST CONTAMINATION. Render's free tier runs a single
worker process (WEB_CONCURRENCY=1) — this test verifies that async
code sharing one process under concurrent load doesn't leak state
between simultaneous requests. Each request asks a UNIQUE, numbered
question and expects a session_id that's never seen in any other
response — if the server ever returned the same session_id twice, or
an answer clearly meant for a different question, that would be a
real, serious concurrency bug, not just a performance number.

COST: real LLM calls (via the deployed OpenRouter key), same routing
as any other chat message. Kept intentionally short and simple to
minimize tokens. Stays under the deployed rate limit (20 requests /
5 min per IP) — default 15 concurrent requests.

Usage:
    python3 scripts/concurrent_load_test.py [num_requests]
"""

import asyncio
import sys
import time

import httpx

BASE_URL = "https://agent-os-backend-v2.onrender.com"


async def _send_one(client: httpx.AsyncClient, index: int) -> dict:
    start = time.time()
    try:
        response = await client.post(
            f"{BASE_URL}/agents/chat",
            json={"message": f"Reply with only the number {index} and nothing else."},
            timeout=60.0,
        )
        duration = time.time() - start
        if response.status_code != 200:
            return {"index": index, "success": False, "duration": duration,
                     "error": f"HTTP {response.status_code}: {response.text[:150]}"}

        data = response.json()
        return {
            "index": index,
            "success": True,
            "duration": duration,
            "session_id": data.get("session_id"),
            "response_text": data.get("response", "")[:80],
        }
    except Exception as e:
        return {"index": index, "success": False, "duration": time.time() - start, "error": str(e)}


async def main():
    num_requests = int(sys.argv[1]) if len(sys.argv) > 1 else 15

    print(f"Firing {num_requests} REAL, SIMULTANEOUS requests at {BASE_URL}/agents/chat")
    print("This makes real, billed LLM calls. Ctrl+C to stop.\n")

    async with httpx.AsyncClient() as client:
        start = time.time()
        results = await asyncio.gather(*[_send_one(client, i) for i in range(num_requests)])
        total_duration = time.time() - start

    successes = [r for r in results if r["success"]]
    failures = [r for r in results if not r["success"]]

    print(f"Completed {num_requests} concurrent requests in {total_duration:.2f}s "
          f"(effective throughput: {num_requests / total_duration:.2f} req/sec)\n")

    for r in results:
        if r["success"]:
            print(f"  [{r['index']}] OK {r['duration']:.2f}s — session={r['session_id'][:8]}... "
                  f"response=\"{r['response_text']}\"")
        else:
            print(f"  [{r['index']}] FAILED {r['duration']:.2f}s — {r['error']}")

    print(f"\n{'=' * 60}")
    print(f"Success: {len(successes)}/{num_requests}")
    print(f"Failed:  {len(failures)}/{num_requests}")

    if successes:
        durations = [r["duration"] for r in successes]
        print(f"Latency under concurrency — min: {min(durations):.2f}s, "
              f"max: {max(durations):.2f}s, avg: {sum(durations)/len(durations):.2f}s")

        # The real correctness check: every concurrent request must get
        # a genuinely distinct session_id. Any duplicate here would mean
        # the server leaked state between simultaneous requests.
        session_ids = [r["session_id"] for r in successes]
        unique_session_ids = set(session_ids)
        if len(unique_session_ids) == len(session_ids):
            print(f"\nCross-contamination check: PASSED — all {len(session_ids)} "
                  f"concurrent requests received unique session IDs.")
        else:
            duplicates = len(session_ids) - len(unique_session_ids)
            print(f"\nCross-contamination check: FAILED — {duplicates} duplicate "
                  f"session ID(s) found across concurrent requests. This indicates "
                  f"a real state-leak bug under concurrency.")

    print(f"{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(main())
