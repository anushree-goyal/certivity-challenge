import { useEffect, useRef } from "react";

/**
 * One poller per resource: calls `fn` every `intervalMs` while `active`, never overlapping (the next call is
 * scheduled only after the previous one settles) and always cleared on unmount or when `active`/interval change.
 * The latest `fn` is used without restarting the timer, so callers need not memoise it.
 */
export function usePoller(fn: () => Promise<unknown> | unknown, intervalMs: number, active: boolean) {
  const fnRef = useRef(fn);
  fnRef.current = fn;
  useEffect(() => {
    if (!active) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try { await fnRef.current(); } catch { /* errors are surfaced by the caller's own state */ }
      if (!stopped) timer = setTimeout(tick, intervalMs);
    };
    timer = setTimeout(tick, intervalMs);
    return () => { stopped = true; clearTimeout(timer); };
  }, [intervalMs, active]);
}
