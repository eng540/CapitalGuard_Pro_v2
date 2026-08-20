export const ENV = {
  // This is an issuer label in Web session JWTs, not a Telegram secret and not
  // a browser-facing OAuth client id. It remains stable across Railway deploys.
  appId: process.env.CAPITALGUARD_WEB_APP_ID ?? "capitalguard-web",
  cookieSecret: process.env.JWT_SECRET ?? "",
  databaseUrl: process.env.DATABASE_URL ?? "",
  oAuthServerUrl: process.env.OAUTH_SERVER_URL ?? "",
  legacyOAuthEnabled: process.env.CAPITALGUARD_ENABLE_LEGACY_OAUTH === "true",
  ownerOpenId: process.env.OWNER_OPEN_ID ?? "",
  isProduction: process.env.NODE_ENV === "production",
  forgeApiUrl: process.env.BUILT_IN_FORGE_API_URL ?? "",
  forgeApiKey: process.env.BUILT_IN_FORGE_API_KEY ?? "",
};
