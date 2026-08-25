export type TimerAudioHandle = {
  playExpiry(): void;
};

type AudioContextConstructor = typeof AudioContext;

function getAudioContextConstructor(): AudioContextConstructor | null {
  if (typeof window === "undefined") {
    return null;
  }
  const maybeWindow = window as Window & {
    webkitAudioContext?: AudioContextConstructor;
  };
  return window.AudioContext ?? maybeWindow.webkitAudioContext ?? null;
}

function playBeep(context: AudioContext): void {
  const oscillator = context.createOscillator();
  const gain = context.createGain();
  oscillator.type = "sine";
  oscillator.frequency.value = 880;
  gain.gain.value = 0.2;
  oscillator.connect(gain);
  gain.connect(context.destination);
  const startAt = context.currentTime;
  oscillator.start(startAt);
  oscillator.stop(startAt + 0.25);
}

export function armTimerAudio(): TimerAudioHandle | null {
  const AudioContextClass = getAudioContextConstructor();
  if (!AudioContextClass) {
    return null;
  }

  let context: AudioContext;
  try {
    context = new AudioContextClass();
  } catch {
    return null;
  }

  if (context.state === "suspended") {
    void context.resume();
  }

  return {
    playExpiry() {
      if (context.state === "suspended") {
        void context.resume().then(() => {
          playBeep(context);
        });
        return;
      }
      playBeep(context);
    },
  };
}
