import { useCallback, useEffect, useRef, useState, type ComponentProps } from "react";
import { Platform, Pressable, StyleSheet, Text, View } from "react-native";

import { ApiNetworkError, ApiUnauthorizedError } from "../api/client";
import type { createCatalogApi, Recipe } from "../api/catalog";
import { Button, ErrorState, InlineNotice, LoadingState, Screen, Section } from "../components";
import { ServingStepper } from "../components/ServingStepper";
import { useTheme } from "../theme/ThemeProvider";
import { scaleFactor, scaleIngredients, type ServingStepperValue } from "../utils/ingredientScale";
import { parseInstructionDuration } from "../cooking/durations";
import { activateCookingKeepAwake, releaseCookingKeepAwake } from "../cooking/keepAwake";
import {
  createDefaultSession,
  loadCookingSession,
  saveCookingSession,
  type CookingSession,
} from "../cooking/session";
import { armTimerAudio, type TimerAudioHandle } from "../cooking/timerAudio";
import { markElapsed, pauseTimer, remainingSeconds, resumeTimer, startTimer } from "../cooking/timer";

type ViewAccessibilityRole = NonNullable<ComponentProps<typeof View>["accessibilityRole"]>;

/** React Native's current role union omits web's valid listitem role. Keep it web-only. */
const webListItemProps: { accessibilityRole?: ViewAccessibilityRole } = Platform.OS === "web"
  ? { accessibilityRole: "listitem" as unknown as ViewAccessibilityRole }
  : {};

export type CookingScreenProps = {
  recipeId: unknown;
  catalog: ReturnType<typeof createCatalogApi>;
  onBack(): void;
  onUnauthorized(): void;
};

function routeRecipeId(value: unknown): string | null {
  return typeof value === "string" && value.trim() && !/\s/.test(value) ? value.trim() : null;
}

function isOfflineError(error: unknown): boolean {
  return error instanceof ApiNetworkError || (typeof error === "object" && error !== null && ((error as { code?: unknown }).code === "ERR_NETWORK" || (error as { code?: unknown }).code === "NETWORK_ERROR"));
}

function ingredientKey(index: number, rawText: string): string {
  return `${index}:${rawText}`;
}

function toStepperValue(session: CookingSession, servings: number | null): ServingStepperValue {
  if (session.scale.kind === "servings") {
    return session.scale;
  }
  if (servings != null && servings > 0) {
    return { kind: "servings", servings };
  }
  return session.scale;
}

function formatClock(totalSeconds: number): string {
  const clamped = Math.max(0, Math.floor(totalSeconds));
  const minutes = Math.floor(clamped / 60);
  const seconds = clamped % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

export function CookingScreen({ recipeId, catalog, onBack, onUnauthorized }: CookingScreenProps) {
  const { theme } = useTheme();
  const id = routeRecipeId(recipeId);
  const [recipe, setRecipe] = useState<Recipe | null>(null);
  const [session, setSession] = useState<CookingSession | null>(null);
  const [loading, setLoading] = useState(Boolean(id));
  const [error, setError] = useState<"none" | "notFound" | "offline" | "generic">(id ? "none" : "notFound");
  const [keepAwakeError, setKeepAwakeError] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const audioRef = useRef<TimerAudioHandle | null>(null);
  const keepAwakeActive = useRef(false);

  const persist = useCallback((next: CookingSession) => {
    setSession(next);
    saveCookingSession(next);
  }, []);

  const load = useCallback(async () => {
    if (!id) {
      setRecipe(null);
      setSession(null);
      setLoading(false);
      setError("notFound");
      return;
    }
    setLoading(true);
    setError("none");
    try {
      const next = await catalog.getRecipe(id);
      const existing = loadCookingSession(id);
      const scale = existing?.scale ?? (next.servings != null && next.servings > 0
        ? { kind: "servings" as const, servings: next.servings }
        : { kind: "multiplier" as const, multiplier: 1 as const });
      let sessionToUse = existing ?? createDefaultSession(id, scale);
      if (sessionToUse.keepAwake) {
        const enabled = await activateCookingKeepAwake();
        if (enabled) {
          keepAwakeActive.current = true;
        } else {
          sessionToUse = { ...sessionToUse, keepAwake: false };
          setKeepAwakeError("Keep the screen awake isn't available on this device.");
        }
      }
      setRecipe(next);
      persist(sessionToUse);
    } catch (caught) {
      if (caught instanceof ApiUnauthorizedError) {
        onUnauthorized();
        return;
      }
      setRecipe(null);
      setSession(null);
      setError(isOfflineError(caught) ? "offline" : (typeof caught === "object" && caught !== null && (caught as { status?: unknown }).status === 404) ? "notFound" : "generic");
    } finally {
      setLoading(false);
    }
  }, [catalog, id, onUnauthorized, persist]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    return () => {
      if (keepAwakeActive.current) {
        void releaseCookingKeepAwake();
        keepAwakeActive.current = false;
      }
    };
  }, []);

  useEffect(() => {
    if (session?.timer?.status !== "running") {
      return undefined;
    }
    const handle = setInterval(() => setNowMs(Date.now()), 250);
    return () => clearInterval(handle);
  }, [session?.timer?.status]);

  useEffect(() => {
    if (!session?.timer || session.timer.status !== "running") {
      return;
    }
    if (remainingSeconds(session.timer, () => nowMs) > 0) {
      return;
    }
    persist({ ...session, timer: markElapsed(session.timer) });
    audioRef.current?.playExpiry();
  }, [nowMs, persist, session]);

  const updateSession = (patch: Partial<CookingSession>) => {
    if (!session) return;
    persist({ ...session, ...patch });
  };

  const toggleIngredient = (key: string) => {
    if (!session) return;
    const checked = session.checkedIngredientKeys.includes(key)
      ? session.checkedIngredientKeys.filter((item) => item !== key)
      : [...session.checkedIngredientKeys, key];
    updateSession({ checkedIngredientKeys: checked });
  };

  const changeScale = (value: ServingStepperValue) => {
    updateSession({ scale: value });
  };

  const startStepTimer = (instructionIndex: number, durationSeconds: number) => {
    if (!session) return;
    audioRef.current = armTimerAudio();
    persist({
      ...session,
      timer: startTimer({ durationSeconds, instructionIndex, now: Date.now }),
    });
  };

  const toggleKeepAwake = async () => {
    if (!session) return;
    setKeepAwakeError(null);
    if (session.keepAwake) {
      await releaseCookingKeepAwake();
      keepAwakeActive.current = false;
      updateSession({ keepAwake: false });
      return;
    }
    const enabled = await activateCookingKeepAwake();
    if (!enabled) {
      setKeepAwakeError("Keep the screen awake isn't available on this device.");
      return;
    }
    keepAwakeActive.current = true;
    updateSession({ keepAwake: true });
  };

  if (!id || error !== "none" && !recipe) {
    const description = error === "offline"
      ? "You’re offline. Check your connection and try again."
      : error === "notFound"
        ? "We couldn't find that recipe."
        : "We couldn't load this cooking session. Please try again.";
    return (
      <Screen>
        <Button label="Back to recipe" variant="quiet" onPress={onBack} />
        <ErrorState title="Cooking unavailable" description={description} action={<Button label="Try again" onPress={() => void load()} />} />
      </Screen>
    );
  }

  if (loading || !recipe || !session) {
    return (
      <Screen>
        <Button label="Back to recipe" variant="quiet" onPress={onBack} />
        <LoadingState label="Loading cooking session" />
      </Screen>
    );
  }

  const stepperValue = toStepperValue(session, recipe.servings);
  const factor = scaleFactor({
    baseServings: recipe.servings,
    selectedServings: stepperValue.kind === "servings" ? stepperValue.servings : null,
    multiplier: stepperValue.kind === "multiplier" ? stepperValue.multiplier : 1,
  });
  const displayedIngredients = scaleIngredients(recipe.ingredients, factor);
  const stepIndex = Math.min(Math.max(session.stepIndex, 0), Math.max(recipe.instructions.length - 1, 0));
  const currentInstruction = recipe.instructions[stepIndex] ?? "";
  const duration = parseInstructionDuration(currentInstruction);
  const timerForStep = session.timer?.instructionIndex === stepIndex ? session.timer : null;
  const remaining = remainingSeconds(timerForStep, () => nowMs);

  return (
    <Screen>
      <Button label="Back to recipe" variant="quiet" onPress={onBack} />
      <Text accessibilityRole="header" style={[styles.title, { color: theme.colors.text, fontFamily: theme.type.fontFamily.heading }]}>
        {`Cooking ${recipe.title}`}
      </Text>
      <ServingStepper servings={recipe.servings} value={stepperValue} onChange={changeScale} />
      <Button
        label={session.keepAwake ? "Release keep awake" : "Keep screen awake"}
        variant="secondary"
        onPress={() => void toggleKeepAwake()}
      />
      {keepAwakeError ? <InlineNotice tone="error" message={keepAwakeError} /> : null}
      <Section title="Ingredients" accessibilityRole="list" accessibilityLabel="Ingredients">
        {recipe.ingredients.length ? displayedIngredients.map((text, index) => {
          const key = ingredientKey(index, recipe.ingredients[index]?.rawText ?? text);
          const checked = session.checkedIngredientKeys.includes(key);
          return (
            <View key={key} {...webListItemProps}>
              <Pressable
                accessibilityRole="checkbox"
                accessibilityState={{ checked }}
                accessibilityLabel={text}
                {...(Platform.OS === "web" ? { "aria-checked": checked } : {})}
                onPress={() => toggleIngredient(key)}
                style={styles.checkRow}
              >
                <Text style={{ color: theme.colors.text }}>{checked ? "☑" : "☐"} {text}</Text>
              </Pressable>
            </View>
          );
        }) : <Text>None listed.</Text>}
      </Section>
      <Section title={`Step ${recipe.instructions.length ? stepIndex + 1 : 0} of ${recipe.instructions.length}`}>
        <Text style={[styles.step, { color: theme.colors.text }]}>
          {currentInstruction || "None listed."}
        </Text>
        <View style={styles.actions}>
          <Button
            label="Previous step"
            variant="secondary"
            disabled={stepIndex <= 0}
            onPress={() => updateSession({ stepIndex: Math.max(0, stepIndex - 1) })}
          />
          <Button
            label="Next step"
            variant="secondary"
            disabled={stepIndex >= recipe.instructions.length - 1}
            onPress={() => updateSession({ stepIndex: Math.min(recipe.instructions.length - 1, stepIndex + 1) })}
          />
        </View>
        {duration ? (
          <View style={styles.timer}>
            <Text style={{ color: theme.colors.text }}>
              {timerForStep ? `Timer ${formatClock(remaining)}` : `Timer ${duration.label}`}
            </Text>
            {timerForStep?.status === "running" ? (
              <Button label="Pause timer" variant="secondary" onPress={() => persist({ ...session, timer: pauseTimer(timerForStep, Date.now) })} />
            ) : timerForStep?.status === "paused" ? (
              <Button label="Resume timer" onPress={() => {
                audioRef.current = armTimerAudio();
                persist({ ...session, timer: resumeTimer(timerForStep, Date.now) });
              }} />
            ) : (
              <Button label="Start timer" onPress={() => startStepTimer(stepIndex, duration.totalSeconds)} />
            )}
          </View>
        ) : null}
      </Section>
    </Screen>
  );
}

const styles = StyleSheet.create({
  title: { fontSize: 28, fontWeight: "700", marginBottom: 8 },
  checkRow: { minHeight: 44, justifyContent: "center", marginBottom: 8 },
  step: { lineHeight: 24, marginBottom: 16 },
  actions: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginBottom: 16 },
  timer: { gap: 8 },
});
