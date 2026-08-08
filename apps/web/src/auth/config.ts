export type Auth0PublicConfig = {
  domain: string;
  clientId: string;
  audience: string;
};

function expoPublicAuthEnv(): Record<string, string | undefined> {
  // Metro only inlines static process.env.EXPO_PUBLIC_* property access.
  return {
    EXPO_PUBLIC_AUTH0_DOMAIN: process.env.EXPO_PUBLIC_AUTH0_DOMAIN,
    EXPO_PUBLIC_AUTH0_CLIENT_ID: process.env.EXPO_PUBLIC_AUTH0_CLIENT_ID,
    EXPO_PUBLIC_AUTH0_AUDIENCE: process.env.EXPO_PUBLIC_AUTH0_AUDIENCE,
  };
}

export function getAuth0Config(
  env: Record<string, string | undefined> = expoPublicAuthEnv(),
): Auth0PublicConfig {
  const domain = env.EXPO_PUBLIC_AUTH0_DOMAIN?.trim() ?? "";
  const clientId = env.EXPO_PUBLIC_AUTH0_CLIENT_ID?.trim() ?? "";
  const audience = env.EXPO_PUBLIC_AUTH0_AUDIENCE?.trim() ?? "";
  if (!domain || !clientId || !audience) {
    throw new Error(
      "EXPO_PUBLIC_AUTH0_DOMAIN, EXPO_PUBLIC_AUTH0_CLIENT_ID, and EXPO_PUBLIC_AUTH0_AUDIENCE are required",
    );
  }
  return { domain, clientId, audience };
}
