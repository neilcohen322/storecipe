const KEEP_AWAKE_TAG = "storecipe-cooking";

type KeepAwakeModule = {
  isAvailableAsync(): Promise<boolean>;
  activateKeepAwakeAsync(tag?: string): Promise<void>;
  deactivateKeepAwake(tag?: string): void;
};

async function loadKeepAwake(): Promise<KeepAwakeModule | null> {
  try {
    return (await import("expo-keep-awake")) as KeepAwakeModule;
  } catch {
    return null;
  }
}

export async function activateCookingKeepAwake(): Promise<boolean> {
  const keepAwake = await loadKeepAwake();
  if (!keepAwake) {
    return false;
  }
  try {
    if (!(await keepAwake.isAvailableAsync())) {
      return false;
    }
    await keepAwake.activateKeepAwakeAsync(KEEP_AWAKE_TAG);
    return true;
  } catch {
    return false;
  }
}

export async function releaseCookingKeepAwake(): Promise<void> {
  const keepAwake = await loadKeepAwake();
  if (!keepAwake) {
    return;
  }
  try {
    keepAwake.deactivateKeepAwake(KEEP_AWAKE_TAG);
  } catch {
    // Ignore platforms that cannot change idle-timer state.
  }
}
