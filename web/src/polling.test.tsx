// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, renderHook } from "@testing-library/react";
import { usePoller } from "./usePoller";

const mocks = vi.hoisted(() => ({
  runs: vi.fn(), health: vi.fn(), status: vi.fn(), results: vi.fn(), cases: vi.fn(), exportStatus: vi.fn(),
}));
vi.mock("./api", () => ({
  OFFLINE_MESSAGE: "Server not reachable — is the app running?",
  onConnectivity: () => () => undefined,
  api: { ...mocks, submissionUrl: () => "", pageUrl: () => "", pdfUrl: () => "", inputUrl: () => "" },
}));
vi.mock("./components/Landing", () => ({ Landing: () => null }));  // decorative; framer-motion is not under test

const run = (state: string) => ({ run_id: "r1", state, stage: state, origin: "fresh", mode: "real", team_id: "t",
  case_id: "c", created_at: "", finished_at: null });
const status = (state: string) => ({ ...run(state), finished: !["queued", "running"].includes(state), warnings: [],
  errors: [], events: [], progress: {} });

beforeEach(() => {
  vi.useFakeTimers();
  Object.values(mocks).forEach((m) => m.mockReset());
  mocks.health.mockResolvedValue({ app: { ok: true, version: "x", active_run: null, queued_runs: 0 }, modules: {},
    participant_package: { ready: true, cases: [] }, corpus: { ready: true }, llm: { ready: true }, real_pipeline_ready: true,
    pipeline_mode: "real" });
  mocks.cases.mockResolvedValue([]);
  mocks.results.mockResolvedValue(null);
  mocks.exportStatus.mockResolvedValue({ available: false, case_id: "c", code: "running", errors: [] });
  window.history.replaceState(null, "", "/");
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

/** Let the initial requests settle and React commit the resulting state (which is what starts a poller). */
async function settle() {
  for (let i = 0; i < 5; i++) await act(() => vi.advanceTimersByTimeAsync(0));
}

describe("usePoller", () => {
  it("calls once per interval, never overlapping, and stops on unmount", async () => {
    let inFlight = 0, maxInFlight = 0;
    const fn = vi.fn(async () => {
      inFlight++; maxInFlight = Math.max(maxInFlight, inFlight);
      await new Promise((r) => setTimeout(r, 2500));  // slower than the interval
      inFlight--;
    });
    const { unmount } = renderHook(() => usePoller(fn, 1000, true));
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(maxInFlight).toBe(1);  // a slow request delays the next one instead of piling up
    expect(fn.mock.calls.length).toBeLessThanOrEqual(3);
    unmount();
    const calls = fn.mock.calls.length;
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(fn.mock.calls.length).toBe(calls);
  });

  it("does nothing while inactive", async () => {
    const fn = vi.fn();
    renderHook(() => usePoller(fn, 1000, false));
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(fn).not.toHaveBeenCalled();
  });
});

describe("run list", () => {
  it("is fetched once and not polled while no run is active", async () => {
    mocks.runs.mockResolvedValue([run("completed")]);
    const { default: App } = await import("./App");
    render(<App />);
    await act(() => vi.advanceTimersByTimeAsync(15_000));
    expect(mocks.runs).toHaveBeenCalledTimes(1);
  });

  it("polls at most every 3 s while a run is running", async () => {
    mocks.runs.mockResolvedValue([run("running")]);
    const { default: App } = await import("./App");
    render(<App />);
    await settle();
    for (let i = 0; i < 3; i++) await act(() => vi.advanceTimersByTimeAsync(3_000));
    // 1 initial load + one per 3 s interval (3, 6, 9 s)
    expect(mocks.runs.mock.calls.length).toBeLessThanOrEqual(4);
    expect(mocks.runs.mock.calls.length).toBeGreaterThanOrEqual(3);
  });
});

describe("open run", () => {
  it("polls status every 2 s while running and stops at a terminal state", async () => {
    mocks.status
      .mockResolvedValueOnce(status("running"))
      .mockResolvedValueOnce(status("running"))
      .mockResolvedValue(status("completed"));
    const onFinished = vi.fn();
    const { RunDetail } = await import("./components/RunDetail");
    render(<RunDetail runId="r1" health={null} onFinished={onFinished} onOpenRegulation={() => undefined} />);
    await settle();
    for (let i = 0; i < 2; i++) { await act(() => vi.advanceTimersByTimeAsync(2_000)); await settle(); }  // 2 s, 4 s
    expect(mocks.status).toHaveBeenCalledTimes(3);
    expect(onFinished).toHaveBeenCalledTimes(1);  // the transition was seen once
    await act(() => vi.advanceTimersByTimeAsync(20_000));
    expect(mocks.status).toHaveBeenCalledTimes(3);  // no polling after completion
  });

  it("opening an already finished run does not refresh the run list", async () => {
    mocks.status.mockResolvedValue(status("completed"));
    const onFinished = vi.fn();
    const { RunDetail } = await import("./components/RunDetail");
    render(<RunDetail runId="r1" health={null} onFinished={onFinished} onOpenRegulation={() => undefined} />);
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(mocks.status).toHaveBeenCalledTimes(1);
    expect(onFinished).not.toHaveBeenCalled();
  });

  it("clears its poller when switching to another run", async () => {
    mocks.status.mockResolvedValue(status("running"));
    const { RunDetail } = await import("./components/RunDetail");
    const props = { health: null, onFinished: () => undefined, onOpenRegulation: () => undefined };
    const { rerender } = render(<RunDetail key="r1" runId="r1" {...props} />);
    await act(() => vi.advanceTimersByTimeAsync(1_000));
    rerender(<RunDetail key="r2" runId="r2" {...props} />);
    await act(() => vi.advanceTimersByTimeAsync(6_100));
    const ids = mocks.status.mock.calls.map((c) => c[0]);
    expect(ids.filter((id) => id === "r1")).toEqual(["r1"]);  // the old run's poller stopped
    expect(ids.filter((id) => id === "r2").length).toBeLessThanOrEqual(4);  // one poller for the new run
  });
});
