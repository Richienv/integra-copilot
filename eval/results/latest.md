# Evaluation · codex/gpt-6-sol · 2026-09-24

| Metric | Result |
|---|---|
| Strict execution accuracy (%) | 63.4 |
| Relaxed execution accuracy (%) | 95.1 |
| Refusal accuracy (%) | 100.0 |
| False refusals | 0 |
| Model errors (counted as misses) | 0 |
| Schema recall (%) | 100.0 |
| Correct after a repair | 0 |
| Summaries replaced by the number check | 0 |
| Latency p50 (ms) | 56920 |
| Latency p95 (ms) | 119534 |
| Tokens per question | 1422 |
| Total cost | 0.0 |

Model: gpt-6-sol through the Codex CLI signed in with ChatGPT (reasoning effort low). Token counts are estimates of the Copilot's own prompts; Codex adds about 21,000 tokens of its own instructions to every call. Cost is zero because calls are billed to the ChatGPT plan.

Relaxed accuracy by language: en 92.9%, id 100.0%, zh 90.0%

| Id | Lang | Expected | Got | Strict | Relaxed | Repairs | ms |
|---|---|---|---|---|---|---|---|
| ar-01 | id | sql | data | yes | yes | 0 | 47664 |
| ar-02 | id | sql | data | yes | yes | 0 | 66862 |
| ar-03 | id | sql | data | yes | yes | 0 | 48027 |
| ap-01 | en | sql | data | yes | yes | 0 | 65547 |
| ap-02 | en | sql | data | no | yes | 0 | 53652 |
| ar-04 | zh | sql | data | yes | yes | 0 | 58910 |
| ar-05 | zh | sql | data | yes | yes | 0 | 55798 |
| ar-06 | id | sql | data | yes | yes | 0 | 53581 |
| ar-07 | en | sql | data | yes | yes | 0 | 53042 |
| pay-01 | id | sql | data | yes | yes | 0 | 54502 |
| pay-02 | en | sql | data | no | yes | 0 | 62004 |
| pay-03 | zh | sql | data | yes | yes | 0 | 66544 |
| so-01 | id | sql | data | yes | yes | 0 | 59557 |
| so-02 | en | sql | data | no | no | 0 | 53433 |
| so-03 | zh | sql | data | yes | yes | 0 | 54122 |
| so-04 | id | sql | data | yes | yes | 0 | 55920 |
| so-05 | en | sql | data | yes | yes | 0 | 47866 |
| so-06 | id | sql | data | yes | yes | 0 | 63208 |
| so-07 | zh | sql | data | yes | yes | 0 | 58030 |
| so-08 | en | sql | data | no | yes | 0 | 57455 |
| cu-01 | id | sql | data | no | yes | 0 | 63067 |
| cu-02 | en | sql | data | no | yes | 0 | 56385 |
| cu-03 | zh | sql | data | yes | yes | 0 | 48320 |
| po-01 | id | sql | data | no | yes | 0 | 49523 |
| po-02 | en | sql | data | yes | yes | 0 | 59730 |
| po-03 | zh | sql | data | yes | yes | 0 | 65832 |
| po-04 | id | sql | data | yes | yes | 0 | 61567 |
| po-05 | en | sql | data | no | yes | 0 | 80797 |
| st-01 | id | sql | data | no | yes | 0 | 104122 |
| st-02 | zh | sql | data | no | yes | 0 | 130855 |
| st-03 | en | sql | data | yes | yes | 0 | 114422 |
| st-04 | id | sql | data | no | yes | 0 | 88746 |
| st-05 | en | sql | data | no | yes | 0 | 111578 |
| gl-01 | id | sql | data | no | yes | 0 | 119534 |
| gl-02 | en | sql | data | yes | yes | 0 | 119913 |
| gl-03 | zh | sql | data | no | no | 0 | 119718 |
| gl-04 | id | sql | data | yes | yes | 0 | 85019 |
| hr-01 | id | sql | data | yes | yes | 0 | 113111 |
| hr-02 | zh | sql | data | yes | yes | 0 | 70581 |
| hr-03 | en | sql | data | yes | yes | 0 | 50794 |
| hr-04 | id | sql | data | no | yes | 0 | 42137 |
| no-01 | id | refuse | refused | — | yes | 0 | 18397 |
| no-02 | en | refuse | refused | — | yes | 0 | 17212 |
| no-03 | zh | refuse | refused | — | yes | 0 | 15738 |
| no-04 | id | refuse | refused | — | yes | 0 | 14682 |
| no-05 | en | refuse | refused | — | yes | 0 | 23499 |
| no-06 | en | refuse | refused | — | yes | 0 | 18866 |
| no-07 | id | refuse | refused | — | yes | 0 | 21132 |
| no-08 | en | refuse | refused | — | yes | 0 | 16860 |
| no-09 | zh | refuse | refused | — | yes | 0 | 17632 |
