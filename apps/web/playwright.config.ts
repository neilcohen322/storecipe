import { defineConfig } from "@playwright/test";

process.env.EXPO_PUBLIC_E2E_MODE = "true";

const baseURL = "http://localhost:4173";

export default defineConfig({
  testDir: "./e2e",
  outputDir: "test-results",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: process.env.CI ? [["github"], ["line"]] : "line",
  use: {
    baseURL,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "off",
  },
  webServer: {
    command: "pnpm build:web && pnpm exec serve -s dist -l 4173",
    url: baseURL,
    reuseExistingServer: false,
    timeout: 180_000,
  },
  projects: [
    { name: "auth-setup", testMatch: /auth\.setup\.ts/ },
    {
      name: "compact-chromium",
      dependencies: ["auth-setup"],
      testIgnore: /auth\.setup\.ts/,
      use: { browserName: "chromium", viewport: { width: 390, height: 844 }, storageState: ".playwright/auth.json" },
    },
    {
      name: "medium-chromium",
      dependencies: ["auth-setup"],
      testIgnore: /auth\.setup\.ts/,
      use: { browserName: "chromium", viewport: { width: 768, height: 1024 }, storageState: ".playwright/auth.json" },
    },
    {
      name: "expanded-chromium",
      dependencies: ["auth-setup"],
      testIgnore: /auth\.setup\.ts/,
      use: { browserName: "chromium", viewport: { width: 1440, height: 900 }, storageState: ".playwright/auth.json" },
    },
  ],
});
