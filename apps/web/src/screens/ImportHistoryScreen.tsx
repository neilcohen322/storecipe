import { Text } from "react-native";

import { Button, EmptyState, PageHeader, Screen, Section } from "../components";
import { useImportSession } from "../imports/ImportSessionProvider";
import { getImportPresentation } from "../utils/importPolling";

export function ImportHistoryScreen({ onNewImport }: { onNewImport(): void }) {
  const { activeJob } = useImportSession();
  return <Screen>
    <PageHeader title="Imports" />
    {activeJob ? <Section title="Current import"><Text>{getImportPresentation(activeJob).label}</Text></Section> : <EmptyState title="No active imports" description="Imports started in this session appear here while they are active." />}
    <Button label="Import a recipe" onPress={onNewImport} />
  </Screen>;
}
