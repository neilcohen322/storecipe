import { LegalScreen } from "../src/screens/LegalScreen";
import { getLegalConfig } from "../src/screens/legalConfig";

export default function TermsRoute() {
  return <LegalScreen document="terms" config={getLegalConfig()} />;
}
