import { actionItems, linkItems, mobilePrimaryItems, moreItems } from "../registry";

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
    expect(moreItems().filter((item) => item.kind === "link").map((item) => item.id))
      .toEqual(linkItems.filter((item) => !primaryIds.has(item.id)).map((item) => item.id));
  });
});
