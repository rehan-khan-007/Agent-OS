import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ChatPage from "./page";

// Mocks localStorage in jsdom (jsdom provides a real implementation,
// but we want a clean, isolated one per test rather than sharing
// state across tests via the same jsdom window).
function freshLocalStorage() {
  const store = new Map<string, string>();
  return {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => store.set(key, value),
    removeItem: (key: string) => store.delete(key),
    clear: () => store.clear(),
  };
}

beforeEach(() => {
  Object.defineProperty(window, "localStorage", {
    value: freshLocalStorage(),
    writable: true,
  });
  // Health check fetch fires on mount — default to a resolved,
  // failed response so it doesn't hang/reject unexpectedly in tests
  // that don't care about connection status.
  global.fetch = vi.fn().mockResolvedValue({ ok: false });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ChatPage", () => {
  it("renders the idle-state hero text when there are no messages yet", async () => {
    render(<ChatPage />);
    expect(screen.getByText("session idle")).toBeInTheDocument();
    expect(screen.getByText("What do you need looked up?")).toBeInTheDocument();
    await waitFor(() => screen.getByText("unreachable")); // let the background health check settle
  });

  it("shows 'connecting' status while the health check is in flight", async () => {
    render(<ChatPage />);
    expect(screen.getByText("connecting")).toBeInTheDocument();
    await waitFor(() => screen.getByText("unreachable")); // let it resolve before the test ends
  });

  it("shows 'online' once the health check succeeds", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: true });
    render(<ChatPage />);
    await waitFor(() => {
      expect(screen.getByText("online")).toBeInTheDocument();
    });
  });

  it("shows 'unreachable' if the health check fails", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("network error"));
    render(<ChatPage />);
    await waitFor(() => {
      expect(screen.getByText("unreachable")).toBeInTheDocument();
    });
  });

  it("defaults to dark theme and toggles to light on click, persisting the choice", async () => {
    const user = userEvent.setup();
    render(<ChatPage />);

    expect(document.documentElement.classList.contains("dark")).toBe(true);

    const toggleButton = screen.getByTitle("Switch to light theme");
    await user.click(toggleButton);

    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(window.localStorage.getItem("agentos-theme")).toBe("light");
  });

  it("restores a previously saved light theme on load", async () => {
    window.localStorage.setItem("agentos-theme", "light");
    render(<ChatPage />);
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    await waitFor(() => screen.getByText("unreachable"));
  });

  it("the send button is disabled when the input is empty", async () => {
    render(<ChatPage />);
    const textarea = screen.getByPlaceholderText("> message agent-os");
    // The send button has no accessible text (icon-only) — find it by
    // being the only other button besides the theme toggle and
    // upload-file button, via its disabled state directly.
    const sendButton = textarea.parentElement?.querySelector("button:last-child");
    expect(sendButton).toBeDisabled();
    await waitFor(() => screen.getByText("unreachable"));
  });

  it("enables the send button once text is typed", async () => {
    const user = userEvent.setup();
    render(<ChatPage />);
    const textarea = screen.getByPlaceholderText("> message agent-os");
    await user.type(textarea, "hello");

    const sendButton = textarea.parentElement?.querySelector("button:last-child");
    expect(sendButton).not.toBeDisabled();
  });
});
