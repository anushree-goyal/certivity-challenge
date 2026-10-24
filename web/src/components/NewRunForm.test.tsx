// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

vi.mock("../api", () => ({
  api: {
    cases: vi.fn(async () => [
      { case_id: "heldout-front-bumper", type: "main", held_out: true, spec_file: "spec.md", spec_files: ["spec.md"], drawings: [], has_spec: true },
      { case_id: "sub-dev", type: "subgoal", held_out: false, spec_file: null, spec_files: [], drawings: [], has_spec: false },
    ]),
    createRun: vi.fn(),
  },
}));

// DEV_MODE is read once when the module loads, so each scenario sets the URL and imports a fresh copy.
async function renderForm(search: string) {
  window.history.replaceState(null, "", `/${search}`);
  vi.resetModules();
  const { NewRunForm } = await import("./NewRunForm");
  render(<NewRunForm health={null} onCreated={() => undefined} />);
  await waitFor(() => expect(screen.getByRole("option", { name: /front bumper/i })).toBeTruthy());
}

afterEach(() => cleanup());

describe("new analysis input screen", () => {
  it("shows only the product inputs and Start analysis", async () => {
    await renderForm("");
    expect(screen.queryByText(/team id/i)).toBeNull();
    expect(screen.queryByText(/case id/i)).toBeNull();
    expect(screen.queryByText("Run settings")).toBeNull();
    expect(screen.queryByText(/processing/i)).toBeNull();
    expect(screen.queryByText(/reuse a cached result/i)).toBeNull();
    expect(screen.getByText("Case from the regulation package")).toBeTruthy();
    expect(screen.getByText("Technical specification (.md, .txt)")).toBeTruthy();
    expect(screen.getByText("Exploded-view drawings (PNG, JPEG, WebP)")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Start analysis" })).toBeTruthy();
  });

  it("offers main-goal cases only, held-out first", async () => {
    await renderForm("");
    const values = screen.getAllByRole("option").map((o) => (o as HTMLOptionElement).value);
    expect(values).toEqual(["", "heldout-front-bumper"]);
  });

  it("keeps fixture processing and cache reuse behind ?dev=1", async () => {
    await renderForm("?dev=1");
    expect(screen.getByText("Developer options")).toBeTruthy();
    expect(screen.getByRole("option", { name: /fixture/i })).toBeTruthy();
    expect(screen.getByText(/reuse a cached result/i)).toBeTruthy();
    expect(screen.queryByText(/team id/i)).toBeNull();
  });
});
