# Recipe extraction evaluation

`cases.json` is a versioned, held-out gold-label set for deterministic and
model-assisted recipe extraction. It includes clean text, noisy text, and visible HTML
without usable JSON-LD. The ten-case seed contains five complete Hebrew recipes and
five complete English recipes. The final thirty-case target contains fifteen of each.
Deliberately mixed-language recipes are not an evaluation category.

Only licensed, self-authored, synthetic, or narrowly reduced sources may be committed.
Every case records its provenance, and full scraped pages are prohibited. This
evaluation set must never be included in fine-tuning data.
