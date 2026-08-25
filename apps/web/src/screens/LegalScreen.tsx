import { Text } from "react-native";

import { PageHeader, Screen, Section } from "../components";
import { useTheme } from "../theme/ThemeProvider";
import type { LegalConfig } from "./legalConfig";

export type LegalDocument = "privacy" | "terms";

export function LegalScreen({ document, config }: { document: LegalDocument; config: LegalConfig }) {
  const { theme } = useTheme();
  const paragraph = (copy: string) => <Text style={[styles.paragraph, { color: theme.colors.text }]}>{copy}</Text>;
  const contact = `Questions? Contact ${config.operatorName} at ${config.privacyContactEmail}.`;
  if (document === "terms") {
    return <Screen><PageHeader title="Terms of service" subtitle={`Effective ${config.effectiveDate}`} />
      <Section title="Using Storecipe">{paragraph("Storecipe helps you save, organize, and manage recipes. Use the service lawfully and keep your account credentials secure.")}</Section>
      <Section title="Your content">{paragraph("You remain responsible for the recipe content and media you add to Storecipe.")}</Section>
      <Section title="Contact">{paragraph(contact)}</Section>
    </Screen>;
  }
  return <Screen><PageHeader title="Privacy policy" subtitle={`Effective ${config.effectiveDate}`} />
    <Section title="Information we process">{paragraph(`${config.operatorName} processes account information and the recipes, imports, ratings, and cover media you provide in order to operate Storecipe.`)}</Section>
    <Section title="Account deletion">{paragraph("When you request account deletion, we delete your Catalog and Ingestion data first, then delete your Auth0 identity last. A JWT-protected deletion tombstone blocks account recreation for 90 days. If you later sign in again after deletion, an empty cookbook is expected.")}</Section>
    <Section title="Cover media retention">{paragraph("Recoverable recipe cover media may remain in Google Cloud Storage (GCS) soft-delete storage for up to 7 days before permanent removal.")}</Section>
    <Section title="Contact">{paragraph(contact)}</Section>
  </Screen>;
}

const styles = { paragraph: { lineHeight: 22 } };
