import {
  resolveIdempotencySession,
  resolveImportIdempotencyAttempt,
} from "../idempotencySession";
import {
  fingerprintIngredientNormalization,
  fingerprintRecipeCreate,
  parseRecipeLines,
} from "../recipeFingerprint";

test("reuses key for the same payload fingerprint", () => {
  let created = 0;
  const createKey = () => {
    created += 1;
    return `key-${created}`;
  };

  const first = resolveIdempotencySession(null, "payload-a", createKey);
  const second = resolveIdempotencySession(first, "payload-a", createKey);

  expect(second.key).toBe("key-1");
  expect(created).toBe(1);
});

test("rotates key when the payload fingerprint changes", () => {
  let created = 0;
  const createKey = () => {
    created += 1;
    return `key-${created}`;
  };

  const first = resolveIdempotencySession(null, "payload-a", createKey);
  const second = resolveIdempotencySession(first, "payload-b", createKey);

  expect(first.key).toBe("key-1");
  expect(second.key).toBe("key-2");
  expect(created).toBe(2);
});

test("raw ingredient fingerprint ignores whitespace-only line normalization", () => {
  const compact = fingerprintIngredientNormalization(parseRecipeLines("flour\n\n  salt  \n"));
  const padded = fingerprintIngredientNormalization(parseRecipeLines("flour\n\n\n  salt  \n\n"));
  expect(padded).toBe(compact);
  expect(compact).toBe(JSON.stringify({ ingredients: ["flour", "salt"] }));
});

test("recipe fingerprint hashes reviewed structured ingredients and metadata", () => {
  const payload = {
    title: "Soup",
    ingredients: [
      {
        rawText: "2 cups flour",
        name: "flour",
        canonicalName: "flour",
        quantity: 2,
        unit: "cups",
      },
      {
        rawText: "salt",
        name: "salt",
        canonicalName: "salt",
        quantity: null,
        unit: null,
      },
    ],
    instructions: ["mix", "bake"],
    tags: [],
  };
  expect(fingerprintRecipeCreate(payload)).toBe(
    JSON.stringify({
      title: "Soup",
      sourceUrl: null,
      servings: null,
      prepMinutes: null,
      cookMinutes: null,
      totalMinutes: null,
      ingredients: [
        {
          rawText: "2 cups flour",
          name: "flour",
          canonicalName: "flour",
          quantity: 2,
          unit: "cups",
        },
        {
          rawText: "salt",
          name: "salt",
          canonicalName: "salt",
          quantity: null,
          unit: null,
        },
      ],
      instructions: ["mix", "bake"],
      tags: [],
    }),
  );
});

test("catalog fingerprint changes when reviewed ingredient fields change", () => {
  const base = {
    title: "Soup",
    ingredients: [
      {
        rawText: "salt",
        name: "salt",
        canonicalName: "salt",
        quantity: null,
        unit: null,
      },
    ],
    instructions: ["mix"],
    tags: [] as string[],
  };
  const baseFingerprint = fingerprintRecipeCreate(base);
  expect(fingerprintRecipeCreate({ ...base, ingredients: [{ ...base.ingredients[0], canonicalName: "table salt" }] })).not.toBe(baseFingerprint);
  expect(fingerprintRecipeCreate({ ...base, ingredients: [{ ...base.ingredients[0], name: "sea salt" }] })).not.toBe(baseFingerprint);
  expect(fingerprintRecipeCreate({ ...base, ingredients: [{ ...base.ingredients[0], quantity: 1, unit: "tsp" }] })).not.toBe(baseFingerprint);
  expect(fingerprintRecipeCreate({ ...base, title: "Stew" })).not.toBe(baseFingerprint);
  expect(fingerprintRecipeCreate({ ...base, instructions: ["simmer"] })).not.toBe(baseFingerprint);
});

test("catalog fingerprint is independent of raw ingredient-line strings", () => {
  const reviewed = {
    title: "Soup",
    ingredients: [
      {
        rawText: "2 cups flour",
        name: "flour",
        canonicalName: "flour",
        quantity: 2,
        unit: "cups",
      },
    ],
    instructions: ["mix"],
    tags: [] as string[],
  };
  const compactRaw = fingerprintIngredientNormalization(parseRecipeLines("2 cups flour"));
  const paddedRaw = fingerprintIngredientNormalization(parseRecipeLines("  2 cups flour  \n"));
  expect(compactRaw).toBe(paddedRaw);
  expect(fingerprintRecipeCreate(reviewed)).toBe(fingerprintRecipeCreate({ ...reviewed }));
});

test("import attempt keeps jobId across same-payload retries", () => {
  let created = 0;
  const createKey = () => {
    created += 1;
    return `key-${created}`;
  };

  const first = resolveImportIdempotencyAttempt(null, "text:soup", createKey);
  const accepted = { session: first.session, jobId: "job-1" };
  const retry = resolveImportIdempotencyAttempt(accepted, "text:soup", createKey);

  expect(retry.session.key).toBe("key-1");
  expect(retry.jobId).toBe("job-1");
  expect(created).toBe(1);
});

test("import attempt clears jobId when payload changes", () => {
  let created = 0;
  const createKey = () => {
    created += 1;
    return `key-${created}`;
  };

  const accepted = {
    session: { key: "key-1", fingerprint: "text:soup" },
    jobId: "job-1",
  };
  const next = resolveImportIdempotencyAttempt(accepted, "text:stew", createKey);

  expect(next.session.key).toBe("key-1");
  expect(next.jobId).toBeNull();
  expect(created).toBe(1);
});

test("reviewed create attempt reuses normalization session for same raw lines", () => {
  let created = 0;
  const createKey = () => {
    created += 1;
    return `key-${created}`;
  };
  const rawFingerprint = fingerprintIngredientNormalization(["water", "salt"]);
  const normalizationSession = resolveIdempotencySession(null, rawFingerprint, createKey);
  const reviewedPayload = {
    title: "Soup",
    ingredients: [
      {
        rawText: "water",
        name: "water",
        canonicalName: "water",
        quantity: null,
        unit: null,
      },
      {
        rawText: "salt",
        name: "salt",
        canonicalName: "salt",
        quantity: null,
        unit: null,
      },
    ],
    instructions: ["boil"],
    tags: [] as string[],
  };
  const catalogSession = resolveIdempotencySession(null, fingerprintRecipeCreate(reviewedPayload), createKey);
  const attempt = {
    rawFingerprint,
    normalizationSession,
    reviewedPayload,
    catalogSession,
  };
  const retryNormalization = resolveIdempotencySession(attempt.normalizationSession, rawFingerprint, createKey);
  expect(retryNormalization.key).toBe("key-1");
  expect(created).toBe(2);
});

test("title edit keeps normalization session and rotates catalog session", () => {
  let created = 0;
  const createKey = () => {
    created += 1;
    return `key-${created}`;
  };
  const rawFingerprint = fingerprintIngredientNormalization(["water"]);
  const normalizationSession = resolveIdempotencySession(null, rawFingerprint, createKey);
  const reviewedPayload = {
    title: "Soup",
    ingredients: [
      {
        rawText: "water",
        name: "water",
        canonicalName: "water",
        quantity: null,
        unit: null,
      },
    ],
    instructions: ["boil"],
    tags: [] as string[],
  };
  const catalogSession = resolveIdempotencySession(null, fingerprintRecipeCreate(reviewedPayload), createKey);
  const nextPayload = { ...reviewedPayload, title: "Stew" };
  const nextCatalogSession = resolveIdempotencySession(catalogSession, fingerprintRecipeCreate(nextPayload), createKey);
  const retryNormalization = resolveIdempotencySession(normalizationSession, rawFingerprint, createKey);
  expect(retryNormalization.key).toBe("key-1");
  expect(nextCatalogSession.key).toBe("key-3");
  expect(nextCatalogSession.key).not.toBe(catalogSession.key);
});
