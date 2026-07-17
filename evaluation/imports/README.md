# Recipe extraction evaluation

`cases.json` is a versioned, held-out gold-label set for deterministic and
model-assisted recipe extraction. It includes clean text, noisy text, and visible HTML
without usable JSON-LD, with four Hebrew, four English, and two genuinely mixed-language
cases.

Only licensed, self-authored, synthetic, or narrowly reduced sources may be committed.
Every case records its provenance, and full scraped pages are prohibited. This
evaluation set must never be included in fine-tuning data.
