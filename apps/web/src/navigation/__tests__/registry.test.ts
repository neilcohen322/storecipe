import { actionItems, linkItems, mobilePrimaryItems, moreItems, navigationRegistry } from "../registry";
import type { Availability } from "../types";

describe("navigation registry", () => {
  it("uses unique link paths and keeps actions free of hrefs", () => {
    expect(new Set(linkItems.map((item) => item.href)).size).toBe(linkItems.length);
    expect(actionItems.every((item) => !("href" in item))).toBe(true);
  });

  it("provides exactly four compact primary destinations", () => {
    expect(mobilePrimaryItems().map((item) => item.label)).toEqual([
      "Recipes",
      "Create",
      "Imports",
      "More",
    ]);
  });

  it("keeps every non-primary link reachable through More", () => {
    const primaryIds = new Set(mobilePrimaryItems().map((item) => item.id));
    expect(moreItems("compact").filter((item) => item.kind === "link").map((item) => item.id))
      .toEqual(linkItems.filter((item) => !primaryIds.has(item.id)).map((item) => item.id));
  });

  it("excludes overflow entries that are unavailable in the requested layout", () => {
    const account = navigationRegistry.find((item) => item.id === "account");
    expect(account).toBeDefined();
    const mutableAccount = account as { availability: Availability };
    const originalAvailability = mutableAccount.availability;

    try {
      mutableAccount.availability = "desktop";
      expect(moreItems("compact").map((item) => item.id)).not.toContain("account");
      expect(moreItems("desktop").map((item) => item.id)).toContain("account");
    } finally {
      mutableAccount.availability = originalAvailability;
    }
  });
});
