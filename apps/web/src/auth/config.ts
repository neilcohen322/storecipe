export type Auth0PublicConfig = {
  domain: string;
  clientId: string;
  audience: string;
};

export function getAuth0Config(
  env: Record<string, string | undefined> = process.env as Record<
    string,
    string | undefined
  >,
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
