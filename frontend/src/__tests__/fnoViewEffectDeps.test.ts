import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const SRC = resolve(
  process.cwd(),
  "src/features/fno/FnoWorkspaceView.tsx",
);

/**
 * The active-view effect must depend on the STABLE `mutate` function.
 *
 * `useMutation` returns a NEW result object on every render (its identity
 * changes with the mutation state), so an effect that depends on the whole
 * mutation result re-runs on every render and turns the active-view POST
 * into a request storm (observed live as a burst of
 * `POST /api/market/fno/view`).
 *
 * This is a source-level contract check: the dependency array is the thing
 * that must not regress.
 */
describe("FnoWorkspaceView active-view effect dependencies", () => {
  const src = readFileSync(SRC, "utf-8");

  it("does not depend on the whole mutation result object", () => {
    const effect = src.match(
      /useEffect\(\(\) => \{[\s\S]*?postActiveView\(\{ symbol: sym[\s\S]*?\}, \[([^\]]*)\]\);/,
    );
    expect(effect, "active-view effect not found").not.toBeNull();
    const deps = effect![1];
    expect(deps).toContain("postActiveView");
    expect(deps).not.toMatch(/\bactiveView\b/);
  });

  it("extracts the stable mutate function from the mutation result", () => {
    expect(src).toMatch(
      /const \{ mutate: postActiveView \} = activeView;/,
    );
  });
});
