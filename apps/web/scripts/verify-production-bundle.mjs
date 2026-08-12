import { execFileSync } from "node:child_process";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const dist = join(root, "dist");
const blockedMarkers = [
  "storecipe-e2e-fixture-only-marker",
  "e2e-intercepted-api-token",
];
const environment = { ...process.env };
delete environment.EXPO_PUBLIC_E2E_MODE;

execFileSync(process.execPath, [join(root, "node_modules", "expo", "bin", "cli"), "export", "--platform", "web"], {
  cwd: root,
  env: environment,
  stdio: "inherit",
});

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
