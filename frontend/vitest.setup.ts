import "@testing-library/jest-dom/vitest";

// jsdom doesn't implement scrollIntoView (it has no real layout
// engine), but the chat page calls it on every message update to
// auto-scroll to the latest message. Stubbed here so component
// tests don't crash on a browser API jsdom simply doesn't provide.
Element.prototype.scrollIntoView = () => {};
