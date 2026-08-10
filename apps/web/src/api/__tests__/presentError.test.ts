import { presentError } from "../presentError";

describe("presentError", () => {
  it("returns fixed copy for typed categories without leaking details", () => {
    expect(presentError({ category: "network" })).toBe("Check your connection and try again.");
    expect(presentError({ category: "auth" })).toBe("Your session needs attention. Please sign in again.");
    expect(presentError({ category: "api" })).toBe("We couldn't complete that request. Please try again.");
    expect(presentError(new Error("Bearer secret https://provider.test/raw imported recipe"))).not.toContain("secret");
  });
});
