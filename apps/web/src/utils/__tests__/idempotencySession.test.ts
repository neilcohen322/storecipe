import {
  resolveIdempotencySession,
  resolveImportIdempotencyAttempt,
} from "../idempotencySession";
import { fingerprintRecipeCreate, parseRecipeLines } from "../recipeFingerprint";

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

test("recipe fingerprint is exactly the normalized title, ingredients, and instructions", () => {
  const ingredients = parseRecipeLines("flour\n\n  salt  \n");
  const instructions = parseRecipeLines("mix\n  \nbake\n");
  const compact = fingerprintRecipeCreate({
    title: "Soup",
    ingredients,
    instructions,
  });
  const paddedIngredients = parseRecipeLines("flour\n\n\n  salt  \n\n");
  const paddedInstructions = parseRecipeLines("mix\n\n\nbake\n\n");
  const padded = fingerprintRecipeCreate({
    title: "Soup",
    ingredients: paddedIngredients,
    instructions: paddedInstructions,
  });

  expect(padded).toBe(compact);
  expect(compact).toBe(JSON.stringify({ title: "Soup", ingredients: ["flour", "salt"], instructions: ["mix", "bake"] }));
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
