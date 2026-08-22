import { execFileSync } from "node:child_process";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const dist = join(root, "dist");
const blockedMarkers = [
  "storecipe-e2e-fixture-only-marker",
  "e2e-intercepted-api-token",
  "fonts.google.com",
  "fonts.gstatic.com",
];
const environment = { ...process.env };
delete environment.EXPO_PUBLIC_E2E_MODE;

const requiredPublicValues = [
  "EXPO_PUBLIC_AUTH0_DOMAIN",
  "EXPO_PUBLIC_AUTH0_CLIENT_ID",
  "EXPO_PUBLIC_AUTH0_AUDIENCE",
  "EXPO_PUBLIC_CATALOG_API_URL",
  "EXPO_PUBLIC_INGESTION_API_URL",
];
for (const name of requiredPublicValues) {
  if (!environment[name]?.trim()) throw new Error(`${name} is required for a production bundle`);
}

const catalogBase = new URL(environment.EXPO_PUBLIC_CATALOG_API_URL);
const ingestionBase = new URL(environment.EXPO_PUBLIC_INGESTION_API_URL);
if (catalogBase.protocol !== "https:" || catalogBase.origin !== ingestionBase.origin) {
  throw new Error("Production API bases must share one HTTPS origin");
}
if (catalogBase.pathname !== "/" || ingestionBase.pathname !== "/") {
  throw new Error("Production API bases must be origins; clients append /v1 paths");
}
const audience = new URL(environment.EXPO_PUBLIC_AUTH0_AUDIENCE);
if (audience.href !== `${catalogBase.origin}/api`) {
  throw new Error("EXPO_PUBLIC_AUTH0_AUDIENCE must equal the public origin plus /api");
}

execFileSync(
  process.execPath,
  [join(root, "node_modules", "expo", "bin", "cli"), "export", "--platform", "web", "--clear"],
  {
    cwd: root,
    env: environment,
    stdio: "inherit",
  },
);

function files(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const file = join(directory, entry.name);
    return entry.isDirectory() ? files(file) : [file];
  });
}

for (const marker of blockedMarkers) {
  const leakedFile = files(dist).find((file) => readFileSync(file, "utf8").includes(marker));
  if (leakedFile) throw new Error(`Production bundle contains E2E fixture marker ${marker}: ${leakedFile}`);
}

console.log("Production bundle excludes E2E fixture markers.");
