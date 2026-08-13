import { expect, test as setup } from "@playwright/test";
import { installApiInterceptions } from "./support";

const authFile = ".playwright/auth.json";

setup("authenticate through the fixture landing", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await installApiInterceptions(page);
  await page.goto("/");
  await expect(page).toHaveTitle(/web/i);
  await page.getByRole("button", { name: "Explore demo" }).click();
  await expect(page).toHaveURL(/\/recipes$/);
  await page.context().storageState({ path: authFile });
});
