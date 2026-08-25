export type LegalConfig = {
  operatorName: string;
  privacyContactEmail: string;
  effectiveDate: string;
};

function expoPublicLegalEnv(): Record<string, string | undefined> {
  // Metro only inlines static process.env.EXPO_PUBLIC_* property access.
  return {
    EXPO_PUBLIC_LEGAL_OPERATOR_NAME: process.env.EXPO_PUBLIC_LEGAL_OPERATOR_NAME,
    EXPO_PUBLIC_PRIVACY_CONTACT_EMAIL: process.env.EXPO_PUBLIC_PRIVACY_CONTACT_EMAIL,
    EXPO_PUBLIC_LEGAL_EFFECTIVE_DATE: process.env.EXPO_PUBLIC_LEGAL_EFFECTIVE_DATE,
  };
}

export function isMissingOrPlaceholderLegalValue(value: string | undefined): boolean {
  const normalized = value?.trim() ?? "";
  return !normalized || /(?:placeholder|replace(?:[-_ ]?me)?|change[-_ ]?me|your[-_ ]|example|<[^>]+>)/i.test(normalized);
}

export function getLegalConfig(env: Record<string, string | undefined> = expoPublicLegalEnv()): LegalConfig {
  const operatorName = env.EXPO_PUBLIC_LEGAL_OPERATOR_NAME?.trim() ?? "";
  const privacyContactEmail = env.EXPO_PUBLIC_PRIVACY_CONTACT_EMAIL?.trim() ?? "";
  const effectiveDate = env.EXPO_PUBLIC_LEGAL_EFFECTIVE_DATE?.trim() ?? "";
  if ([operatorName, privacyContactEmail, effectiveDate].some((value) => isMissingOrPlaceholderLegalValue(value))) {
    throw new Error("EXPO_PUBLIC_LEGAL_OPERATOR_NAME, EXPO_PUBLIC_PRIVACY_CONTACT_EMAIL, and EXPO_PUBLIC_LEGAL_EFFECTIVE_DATE are required");
  }
  return { operatorName, privacyContactEmail, effectiveDate };
}
