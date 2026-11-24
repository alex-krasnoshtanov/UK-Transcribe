/** Splits text around case-insensitive matches of `query`, for <mark>. */
export function splitMatches(text: string, query: string): Array<{ text: string; hit: boolean }> {
  const q = query.trim();
  if (!q) return [{ text, hit: false }];
  const lower = text.toLocaleLowerCase("uk");
  const needle = q.toLocaleLowerCase("uk");
  const parts: Array<{ text: string; hit: boolean }> = [];
  let from = 0;
  for (;;) {
    const at = lower.indexOf(needle, from);
    if (at < 0) break;
    if (at > from) parts.push({ text: text.slice(from, at), hit: false });
    parts.push({ text: text.slice(at, at + needle.length), hit: true });
    from = at + needle.length;
  }
  if (from < text.length) parts.push({ text: text.slice(from), hit: false });
  return parts;
}

/** Index of the segment playing at `time`, or -1. Segments are sorted by start. */
export function activeSegment(segments: ReadonlyArray<{ start: number; end: number }>, time: number): number {
  let lo = 0;
  let hi = segments.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (segments[mid]!.start <= time) {
      found = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return found >= 0 && time <= segments[found]!.end + 0.5 ? found : -1;
}
