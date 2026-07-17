import { StatusBar } from 'expo-status-bar';
import { Pressable, StyleSheet, Text, View } from 'react-native';

export default function App() {
  return (
    <View style={styles.container}>
      <View style={styles.badge}>
        <Text style={styles.badgeText}>PRIVATE RECIPE LIBRARY</Text>
      </View>
      <Text style={styles.title}>Storecipe</Text>
      <Text style={styles.subtitle}>
        Your private recipe library, ready for thoughtful recommendations.
      </Text>
      <Pressable accessibilityRole="button" disabled style={styles.button}>
        <Text style={styles.buttonText}>Recipe workspace</Text>
      </Pressable>
      <Text style={styles.note}>The Expo universal client is running.</Text>
      <StatusBar style="light" />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#10231c',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 32,
  },
  badge: {
    backgroundColor: '#d8f3dc',
    borderRadius: 999,
    paddingHorizontal: 14,
    paddingVertical: 7,
    marginBottom: 24,
  },
  badgeText: {
    color: '#1b4332',
    fontSize: 12,
    fontWeight: '700',
    letterSpacing: 1.2,
  },
  title: {
    color: '#f7fff9',
    fontSize: 54,
    fontWeight: '800',
    letterSpacing: -1.5,
  },
  subtitle: {
    color: '#b7d8c7',
    fontSize: 18,
    lineHeight: 27,
    maxWidth: 520,
    textAlign: 'center',
    marginTop: 12,
  },
  button: {
    backgroundColor: '#2d6a4f',
    borderRadius: 12,
    marginTop: 32,
    paddingHorizontal: 22,
    paddingVertical: 14,
  },
  buttonText: {
    color: '#d8f3dc',
    fontSize: 15,
    fontWeight: '600',
  },
  note: {
    color: '#78a890',
    fontSize: 13,
    marginTop: 18,
  },
});
