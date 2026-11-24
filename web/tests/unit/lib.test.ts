import { describe, expect, it } from "vitest";

import { allowed } from "@/lib/backend";
import { bytes, clock, eta, percent } from "@/lib/format";
import { activeSegment, splitMatches } from "@/lib/highlight";

const ID = "0123456789abcdef0123456789abcdef";

describe("backend allow-list", () => {
  it("lets the dashboard's calls through", () => {
    expect(allowed("jobs", "POST")).toBe(true);
    expect(allowed(`jobs/${ID}`, "DELETE")).toBe(true);
    expect(allowed(`jobs/${ID}/transcript.srt`, "GET")).toBe(true);
    expect(allowed(`jobs/${ID}/retry`, "POST")).toBe(true);
    expect(allowed("settings", "PUT")).toBe(true);
  });
  it("refuses anything else", () => {
    expect(allowed("docs", "GET")).toBe(false);
    expect(allowed("jobs/../../admin", "GET")).toBe(false);
    expect(allowed(`jobs/${ID}/transcript.pdf`, "GET")).toBe(false);
    expect(allowed("settings", "DELETE")).toBe(false);
    expect(allowed(`jobs/${ID.toUpperCase()}`, "GET")).toBe(false);
  });
});

describe("format", () => {
  it("clock", () => {
    expect(clock(0)).toBe("0:00");
    expect(clock(65.9)).toBe("1:05");
    expect(clock(3725)).toBe("1:02:05");
    expect(clock(null)).toBe("–");
  });
  it("eta, bytes, percent", () => {
    expect(eta(30)).toBe("under a minute");
    expect(eta(3900)).toBe("about 1 h 5 min");
    expect(bytes(1536)).toBe("1.5 KB");
    expect(bytes(200 * 1024 * 1024)).toBe("200 MB");
    expect(percent(1.2)).toBe("100%");
  });
});

describe("highlight", () => {
  it("marks case-insensitive Cyrillic matches", () => {
    expect(splitMatches("Привіт, привіт!", "ПРИВІТ")).toEqual([
      { text: "Привіт", hit: true },
      { text: ", ", hit: false },
      { text: "привіт", hit: true },
      { text: "!", hit: false },
    ]);
    expect(splitMatches("abc", " ")).toEqual([{ text: "abc", hit: false }]);
  });
  it("finds the playing segment", () => {
    const segs = [
      { start: 0, end: 2 },
      { start: 2, end: 5 },
      { start: 10, end: 12 },
    ];
    expect(activeSegment(segs, 3)).toBe(1);
    expect(activeSegment(segs, 8)).toBe(-1);
    expect(activeSegment(segs, 11)).toBe(2);
  });
});
