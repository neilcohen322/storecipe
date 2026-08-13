import { expect, test } from "@playwright/test";

import { assertNoHorizontalOverflow, assertStablePageQuality, captureConsoleErrors, installApiInterceptions } from "./support";

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await installApiInterceptions(page);
});

for (const scheme of ["light", "dark"] as const) {
  test(`${scheme} theme persists without horizontal overflow`, async ({ page }, testInfo) => {
    const errors = captureConsoleErrors(page);
    await page.goto("/account");
    const controls = testInfo.project.name.startsWith("compact") ? page : page.getByTestId("desktop-sidebar");
    await controls.getByRole("button", { name: `Use ${scheme} theme` }).click();
    await expect.poll(() => page.evaluate(() => localStorage.getItem("storecipe.theme"))).toBe(scheme);
    await assertNoHorizontalOverflow(page);
    await page.reload();
    await expect.poll(() => page.evaluate(() => localStorage.getItem("storecipe.theme"))).toBe(scheme);
    await assertNoHorizontalOverflow(page);
    await assertStablePageQuality(page, errors);
  });
}
