import assert from "node:assert/strict";
import test from "node:test";

import { assertProductionEnvironment, hasInlineExecutableScript } from "./verify-production-bundle.mjs";

const environment = {
  EXPO_PUBLIC_AUTH0_DOMAIN: "tenant.auth0.com",
  EXPO_PUBLIC_AUTH0_CLIENT_ID: "client-id",
  EXPO_PUBLIC_AUTH0_AUDIENCE: "https://storecipe.test/api",
  EXPO_PUBLIC_CATALOG_API_URL: "https://storecipe.test",
  EXPO_PUBLIC_INGESTION_API_URL: "https://storecipe.test",
  EXPO_PUBLIC_LEGAL_OPERATOR_NAME: "Storecipe Ltd.",
  EXPO_PUBLIC_PRIVACY_CONTACT_EMAIL: "privacy@storecipe.test",
  EXPO_PUBLIC_LEGAL_EFFECTIVE_DATE: "2026-08-24",
};

test("rejects missing and placeholder legal production variables", () => {
  assert.throws(() => assertProductionEnvironment({ ...environment, EXPO_PUBLIC_LEGAL_OPERATOR_NAME: "" }), /EXPO_PUBLIC_LEGAL_OPERATOR_NAME/);
  assert.throws(() => assertProductionEnvironment({ ...environment, EXPO_PUBLIC_PRIVACY_CONTACT_EMAIL: "replace-me" }), /EXPO_PUBLIC_PRIVACY_CONTACT_EMAIL/);
});

test("allows inline styles but rejects executable inline scripts", () => {
  assert.equal(hasInlineExecutableScript("<style>body{color:red}</style><script src=\"/app.js\"></script>"), false);
  assert.equal(hasInlineExecutableScript("<script>window.boot()</script>"), true);
  assert.equal(hasInlineExecutableScript("<script type=\"application/json\">{}</script>"), false);
});
