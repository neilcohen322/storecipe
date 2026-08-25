import { LegalScreen } from "../src/screens/LegalScreen";
import { getLegalConfig } from "../src/screens/legalConfig";

export default function PrivacyRoute() {
  return <LegalScreen document="privacy" config={getLegalConfig()} />;
}
