import { Pressable, ScrollView, Text } from "react-native";

import { useImportSession } from "../imports/ImportSessionProvider";
import { sharedStyles } from "../theme";
import { getImportPresentation } from "../utils/importPolling";

export function ImportHistoryScreen({ onNewImport }: { onNewImport(): void }) {
  const { activeJob } = useImportSession();
  return <ScrollView style={sharedStyles.screen} contentContainerStyle={{ paddingBottom: 40 }}>
    <Text style={sharedStyles.heading}>Imports</Text>
    {activeJob ? <><Text style={sharedStyles.rowTitle}>Current import</Text><Text style={sharedStyles.note}>{getImportPresentation(activeJob).label}</Text></> : <><Text style={sharedStyles.rowTitle}>No active imports</Text><Text style={sharedStyles.note}>Imports started in this session appear here while they are active.</Text></>}
    <Pressable accessibilityRole="button" onPress={onNewImport} style={sharedStyles.button}><Text style={sharedStyles.buttonText}>Import a recipe</Text></Pressable>
  </ScrollView>;
}
