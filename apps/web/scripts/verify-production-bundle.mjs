import { execFileSync } from "node:child_process";
import { readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const dist = join(root, "dist");
const blockedMarkers = [
  "storecipe-e2e-fixture-only-marker",
  "e2e-intercepted-api-token",
  "fonts.google.com",
  "fonts.gstatic.com",
];
export const REQUIRED_LEGAL_ENV = [
  "EXPO_PUBLIC_LEGAL_OPERATOR_NAME",
  "EXPO_PUBLIC_PRIVACY_CONTACT_EMAIL",
  "EXPO_PUBLIC_LEGAL_EFFECTIVE_DATE",
];

export function isMissingOrPlaceholder(value) {
  const normalized = value?.trim() ?? "";
  return !normalized || /(?:placeholder|replace(?:[-_ ]?me)?|change[-_ ]?me|your[-_ ]|example|<[^>]+>)/i.test(normalized);
}

export function assertProductionEnvironment(environment) {
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
  for (const name of REQUIRED_LEGAL_ENV) {
    if (isMissingOrPlaceholder(environment[name])) throw new Error(`${name} must be set to a non-placeholder value for a production bundle`);
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
}

export function files(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const file = join(directory, entry.name);
    return entry.isDirectory() ? files(file) : [file];
  });
}

const NON_EXECUTABLE_SCRIPT_TYPES = new Set(["application/json", "application/ld+json", "text/plain", "text/template"]);

export function hasInlineExecutableScript(content) {
  const scripts = content.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script\s*>/gi);
  for (const script of scripts) {
    const attributes = script[1] ?? "";
    if (/\bsrc\s*=/i.test(attributes)) continue;
    const type = attributes.match(/\btype\s*=\s*["']?([^"'\s>]+)/i)?.[1]?.toLowerCase();
    if (!type || !NON_EXECUTABLE_SCRIPT_TYPES.has(type)) return true;
  }
  return false;
}

export function assertNoInlineExecutableScripts(outputFiles) {
  const inlineScriptFile = outputFiles.find((file) => /\.(?:html|js)$/i.test(file) && hasInlineExecutableScript(readFileSync(file, "utf8")));
  if (inlineScriptFile) throw new Error(`Production bundle contains an inline executable script blocked by script-src 'self': ${inlineScriptFile}`);
}

export function verifyProductionBundle(environment = { ...process.env }) {
  delete environment.EXPO_PUBLIC_E2E_MODE;
  assertProductionEnvironment(environment);
  execFileSync(
    process.execPath,
    [join(root, "node_modules", "expo", "bin", "cli"), "export", "--platform", "web", "--clear"],
    { cwd: root, env: environment, stdio: "inherit" },
  );
  const outputFiles = files(dist);
  for (const marker of blockedMarkers) {
    const leakedFile = outputFiles.find((file) => readFileSync(file, "utf8").includes(marker));
    if (leakedFile) throw new Error(`Production bundle contains E2E fixture marker ${marker}: ${leakedFile}`);
  }
  assertNoInlineExecutableScripts(outputFiles);
  console.log("Production bundle excludes E2E fixture markers and inline executable scripts.");
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  verifyProductionBundle();
}
