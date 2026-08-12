const path = require("path");
const { getDefaultConfig } = require("expo/metro-config");

const config = getDefaultConfig(__dirname);
const fixtureBuild = process.env.EXPO_PUBLIC_E2E_MODE === "true";
const authProviderModule = path.join(
  __dirname,
  fixtureBuild ? "src/testing/E2EAuthProvider.tsx" : "src/app/ProductionAuthProvider.tsx",
);

config.resolver.resolveRequest = (context, moduleName, platform) => {
  if (moduleName === "@storecipe/auth-provider") {
    return { type: "sourceFile", filePath: authProviderModule };
  }

  return context.resolveRequest(context, moduleName, platform);
};

module.exports = config;
