"use client";

import { useEffect, useRef } from "react";

/**
 * Calls `fn` now and then every `ms` while the tab is visible (and `enabled`),
 * never overlapping two calls. Returns nothing: `fn` updates state itself.
 */
export function usePoll(fn: () => Promise<unknown> | unknown, ms: number, enabled = true) {
  const ref = useRef(fn);
  useEffect(() => {
    ref.current = fn;
  });
  useEffect(() => {
    if (!enabled) return;
    let stopped = false;
    let running = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      if (stopped || running) return;
      running = true;
      if (document.visibilityState === "visible") {
        try {
          await ref.current();
        } catch {
          /* the caller shows errors */
        }
      }
      running = false;
      clearTimeout(timer);
      if (!stopped) timer = setTimeout(tick, ms);
    };
    tick();
    const onVisible = () => {
      if (document.visibilityState === "visible") {
        clearTimeout(timer);
        tick();
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopped = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [ms, enabled]);
}
