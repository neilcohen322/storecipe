export type ServingStepperValue =
  | { kind: "servings"; servings: number }
  | { kind: "multiplier"; multiplier: 0.5 | 1 | 2 };

export type ServingStepperMode = ServingStepperValue["kind"];

const QUANTITY_TOLERANCE = 1e-6;

const UNICODE_FRACTIONS: Record<string, number> = {
  "½": 0.5,
  "¼": 0.25,
  "¾": 0.75,
  "⅓": 1 / 3,
  "⅔": 2 / 3,
};

const COMMON_FRACTIONS = [
  { num: 1, den: 8 },
  { num: 1, den: 4 },
  { num: 1, den: 3 },
  { num: 3, den: 8 },
  { num: 1, den: 2 },
  { num: 5, den: 8 },
  { num: 2, den: 3 },
  { num: 3, den: 4 },
  { num: 7, den: 8 },
] as const;

const VAGUE_PREFIX = /^(a|an|about|approx(?:imately)?|roughly|some|to taste|pinch of)\b/i;

function parseFraction(numerator: string, denominator: string): number | null {
  const num = Number(numerator);
  const den = Number(denominator);
  if (!Number.isFinite(num) || !Number.isFinite(den) || den === 0) return null;
  return num / den;
}

function isRangeRest(rest: string): boolean {
  return /^\s*-\s*\d/.test(rest) || /^\s+to\s+\d/i.test(rest);
}

function parseLeadingQuantityCore(text: string): { value: number; raw: string; rest: string } | null {
  if (!text || VAGUE_PREFIX.test(text)) return null;

  const mixedSlash = text.match(/^(\d+)\s+(\d+)\/(\d+)/);
  if (mixedSlash) {
    const fraction = parseFraction(mixedSlash[2]!, mixedSlash[3]!);
    if (fraction == null) return null;
    const raw = mixedSlash[0]!;
    const rest = text.slice(raw.length);
    if (isRangeRest(rest)) return null;
    return { value: Number(mixedSlash[1]!) + fraction, raw, rest };
  }

  const mixedUnicode = text.match(/^(\d+)\s+([½¼¾⅓⅔])/);
  if (mixedUnicode) {
    const fraction = UNICODE_FRACTIONS[mixedUnicode[2]!];
    if (fraction == null) return null;
    const raw = mixedUnicode[0]!;
    const rest = text.slice(raw.length);
    if (isRangeRest(rest)) return null;
    return { value: Number(mixedUnicode[1]!) + fraction, raw, rest };
  }

  const slashFraction = text.match(/^(\d+\/\d+)/);
  if (slashFraction) {
    const [numerator, denominator] = slashFraction[1]!.split("/");
    const value = parseFraction(numerator!, denominator!);
    if (value == null) return null;
    const raw = slashFraction[0]!;
    const rest = text.slice(raw.length);
    if (isRangeRest(rest)) return null;
    return { value, raw, rest };
  }

  const unicodeFraction = text.match(/^([½¼¾⅓⅔])/);
  if (unicodeFraction) {
    const value = UNICODE_FRACTIONS[unicodeFraction[1]!];
    if (value == null) return null;
    const raw = unicodeFraction[0]!;
    const rest = text.slice(raw.length);
    if (isRangeRest(rest)) return null;
    return { value, raw, rest };
  }

  const decimal = text.match(/^(\d+\.\d+)/);
  if (decimal) {
    const value = Number(decimal[1]);
    if (!Number.isFinite(value)) return null;
    const raw = decimal[0]!;
    const rest = text.slice(raw.length);
    if (isRangeRest(rest)) return null;
    return { value, raw, rest };
  }

  const integer = text.match(/^(\d+)/);
  if (integer) {
    const value = Number(integer[1]);
    if (!Number.isFinite(value)) return null;
    const raw = integer[0]!;
    const rest = text.slice(raw.length);
    if (isRangeRest(rest)) return null;
    return { value, raw, rest };
  }

  return null;
}

export function parseLeadingQuantity(rawText: string): { value: number; raw: string; rest: string } | null {
  const leading = rawText.match(/^\s*/)?.[0] ?? "";
  return parseLeadingQuantityCore(rawText.slice(leading.length));
}

export function formatScaledQuantity(value: number): string {
  if (!Number.isFinite(value)) return String(value);

  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  const whole = Math.floor(abs + QUANTITY_TOLERANCE);
  const fractional = abs - whole;

  if (fractional < 0.005) {
    return `${sign}${whole}`;
  }

  for (const { num, den } of COMMON_FRACTIONS) {
    const candidate = num / den;
    if (Math.abs(fractional - candidate) < 0.01) {
      if (whole > 0) return `${sign}${whole} ${num}/${den}`;
      return `${sign}${num}/${den}`;
    }
  }

  const rounded = Math.round(abs * 100) / 100;
  const fixed = rounded.toFixed(2).replace(/\.?0+$/, "");
  return `${sign}${fixed}`;
}

export function scaleFactor(args: {
  baseServings: number | null;
  selectedServings: number | null;
  multiplier: number;
}): number {
  const { baseServings, selectedServings, multiplier } = args;
  if (baseServings != null && baseServings > 0 && selectedServings != null) {
    return selectedServings / baseServings;
  }
  return multiplier;
}

function quantitiesAgree(structured: number, parsed: number): boolean {
  return Math.abs(structured - parsed) < QUANTITY_TOLERANCE;
}

export function scaledIngredientText(
  ingredient: { rawText: string; quantity?: number | null },
  factor: number,
): string {
  if (factor === 1) return ingredient.rawText;

  const structured = ingredient.quantity;
  if (structured == null || !Number.isFinite(structured)) return ingredient.rawText;

  const leading = ingredient.rawText.match(/^\s*/)?.[0] ?? "";
  const parsed = parseLeadingQuantityCore(ingredient.rawText.slice(leading.length));
  if (!parsed || !quantitiesAgree(structured, parsed.value)) return ingredient.rawText;

  return `${leading}${formatScaledQuantity(parsed.value * factor)}${parsed.rest}`;
}

export function scaleIngredients(
  ingredients: readonly { rawText: string; quantity?: number | null }[],
  factor: number,
): string[] {
  return ingredients.map((ingredient) => scaledIngredientText(ingredient, factor));
}
