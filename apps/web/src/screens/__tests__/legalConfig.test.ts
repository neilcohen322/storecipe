import { getLegalConfig } from "../legalConfig";

const env = {
  EXPO_PUBLIC_LEGAL_OPERATOR_NAME: "Storecipe Ltd.",
  EXPO_PUBLIC_PRIVACY_CONTACT_EMAIL: "privacy@storecipe.test",
  EXPO_PUBLIC_LEGAL_EFFECTIVE_DATE: "2026-08-24",
};

test("reads the required public legal configuration", () => {
  expect(getLegalConfig(env)).toEqual({ operatorName: "Storecipe Ltd.", privacyContactEmail: "privacy@storecipe.test", effectiveDate: "2026-08-24" });
});

test.each([
  { ...env, EXPO_PUBLIC_LEGAL_OPERATOR_NAME: "" },
  { ...env, EXPO_PUBLIC_PRIVACY_CONTACT_EMAIL: "replace-me" },
  { ...env, EXPO_PUBLIC_LEGAL_EFFECTIVE_DATE: "" },
])("rejects missing or placeholder legal configuration", (invalidEnv) => {
  expect(() => getLegalConfig(invalidEnv)).toThrow("EXPO_PUBLIC_LEGAL_OPERATOR_NAME, EXPO_PUBLIC_PRIVACY_CONTACT_EMAIL, and EXPO_PUBLIC_LEGAL_EFFECTIVE_DATE are required");
});
