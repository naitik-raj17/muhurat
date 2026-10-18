# Evaluation report

Generated 2026-10-18T23:04:33+00:00 at commit `2cd11ad` on a fresh, seeded `wedding_eval` database. Offline: LLM = mock (offline; rule-based extractors, token counts priced at real model rates); embeddings = `sentence_transformers`. All data is synthetic. Reproduce with `make eval`.

## Summary

| Area | Metric | Result | Target |
|---|---|---|---|
| Vendor matching | precision@5 (50 queries) | 0.784 (max possible 0.792) | – |
| Vendor matching | recall@10 | 0.868 (max possible 0.888) | – |
| Vendor matching | MRR | 0.987 | – |
| Contracts | field accuracy, seed set (5) | 100.0% | – |
| Contracts | field accuracy, held-out set (7) | 58.9% | – |
| Contracts | risk-flag F1 (all) | 0.792 | – |
| Contracts | quotes verified in source | 100.0% | 100% |
| RSVP import | report counts correct (563 rows) | 100.0% | – |
| RSVP import | stored guest fields correct | 100.0% | – |
| RSVP replies | intent accuracy (74 replies) | 81.1% | – |
| RSVP replies | precision of automatic updates | 92.2% | – |
| RSVP reminders | rule decisions correct (1332) | 100.0% | 100% |
| Quiet hours | messages sent in quiet hours | 0 | 0 |
| API latency | worst p95 across endpoints | 380 ms | < 2000 ms ✅ |
| Cost | LLM cost per wedding | ₹15.43 | < ₹300 ✅ |
| Cost | worst case per wedding | ₹94.51 | < ₹300 ✅ |

## 1. Vendor matching

50 queries over 316 catalogue vendors. A vendor is relevant when it is in the requested category, lists the queried style, and (if the query sets them) is in the city and starts within the budget. The style appears only in the free-text query, never as a filter. Small relevant sets cap precision@5 and large ones cap recall@10, so each is shown with the best a perfect ranking could reach. Mean relevant vendors per query: 8.3.

| Query type | n | P@5 | P@5 max | R@10 | R@10 max | MRR |
|---|---|---|---|---|---|---|
| city | 16 | 0.362 | 0.362 | 1.000 | 1.000 | 0.958 |
| national | 26 | 0.992 | 1.000 | 0.760 | 0.788 | 1.000 |
| national+budget | 8 | 0.950 | 0.975 | 0.952 | 0.989 | 1.000 |
| **all** | 50 | 0.784 | 0.792 | 0.868 | 0.888 | 0.987 |

Queries below their ceiling:

| Query | Category | City | Budget | Relevant | P@5 | R@10 |
|---|---|---|---|---|---|---|
| pre wedding photography | photographer | – | – | 15 | 1.00 | 0.53 |
| south indian makeup artist | makeup | – | – | 11 | 1.00 | 0.82 |
| bridal mehendi artist | mehendi_artist | – | 33000 | 7 | 0.80 | 1.00 |
| live band DJ | dj | – | – | 15 | 0.80 | 0.47 |
| bollywood DJ | dj | – | 116000 | 9 | 1.00 | 0.89 |
| north indian pandit | pandit | – | – | 13 | 1.00 | 0.46 |
| south indian pandit | pandit | – | 45000 | 11 | 1.00 | 0.73 |

## 2. Contract extraction

8 fields per contract: advance %, advance refundable, and presence of each of the 6 clauses. The **seed** contracts were in view while the rule-based extractor was written; the **held-out** contracts use other headings and wording. Offline, the mock LLM returns the rule-based extraction, so these numbers measure the fallback path, not a real model.

| Set | Contracts | Field accuracy | Risk precision | Risk recall | Exact risk set | Quotes verified |
|---|---|---|---|---|---|---|
| seed | 5 | 100.0% | 1.000 | 1.000 | 100.0% | 100.0% |
| held-out | 7 | 58.9% | 0.579 | 0.786 | 28.6% | 100.0% |
| all | 12 | 76.0% | 0.724 | 0.875 | 58.3% | 100.0% |

Per field (all contracts):

| Field | Accuracy |
|---|---|
| advance_percent | 58.3% |
| advance_refundable | 66.7% |
| has_payment_schedule | 58.3% |
| has_cancellation | 83.3% |
| has_refund | 91.7% |
| has_liability | 91.7% |
| has_force_majeure | 91.7% |
| has_deliverables | 66.7% |

Per contract:

| Contract | Set | Fields | Wrong fields | Missed flags | Extra flags |
|---|---|---|---|---|---|
| venue_fair | seed | 100.0% | – | – | – |
| decor_risky_advance | seed | 100.0% | – | – | – |
| photo_no_cancellation | seed | 100.0% | – | – | – |
| catering_fair | seed | 100.0% | – | – | – |
| dj_injection | seed | 100.0% | – | – | – |
| heritage_venue_fair | held_out | 12.5% | advance_percent, advance_refundable, has_payment_schedule, has_cancellation, has_refund, has_force_majeure, has_deliverables | – | no_cancellation_clause, no_deliverables, no_force_majeure, no_refund_clause |
| decor_forfeit_words | held_out | 25.0% | advance_percent, advance_refundable, has_payment_schedule, has_cancellation, has_liability, has_deliverables | high_nonrefundable_advance, one_sided_liability | no_cancellation_clause, no_deliverables |
| photo_retainer_minimal | held_out | 62.5% | advance_percent, has_payment_schedule, has_deliverables | – | no_deliverables |
| dj_fifty_refundable | held_out | 87.5% | advance_refundable | – | – |
| makeup_sixty_nonrefundable | held_out | 75.0% | advance_percent, has_payment_schedule | high_nonrefundable_advance | – |
| pandit_no_deliverables | held_out | 62.5% | advance_percent, advance_refundable, has_payment_schedule | – | – |
| caterer_injection | held_out | 87.5% | has_deliverables | – | no_deliverables |

## 3. RSVP pipeline

### Import

5 generated lists, 563 rows (mixed phone formats, invalid phones, missing sides, duplicate rows, households sharing a phone or a family label).

| Check | Result |
|---|---|
| Import report counts match truth | 100.0% |
| Stored guest fields (E.164 phone, language, side) | 100.0% |
| Family grouping precision (pairs) | 1.000 |
| Family grouping recall (pairs) | 1.000 |

### Replies

74 hand-labelled replies (English, Hinglish, Hindi), each sent through the real path: invite → outbox → simulated WhatsApp reply → intent → ActionPolicy → RSVP. A reply that doesn't answer the invitation should go to a person.

| Metric | Result |
|---|---|
| Accuracy (status, or correctly sent to a person) | 81.1% |
| Precision of automatic updates | 92.2% |
| Wrong automatic updates | 4 |
| Answers handled automatically (coverage) | 83.3% |
| Non-answers sent to a person | 92.9% |
| Plus-ones correct (accepted replies) | 91.7% |
| Accuracy, en (47) | 76.6% |
| Accuracy, hi (10) | 90.0% |
| Accuracy, hinglish (17) | 88.2% |

Misread replies (expected → got; “review” = sent to a person):

| Reply | Expected | Got |
|---|---|---|
| Count me in | accepted | review |
| Happy to attend! | accepted | review |
| Not coming | declined | review |
| Can't attend, but sending blessings | declined | review |
| Depends on my leave, will let you know | maybe | review |
| Koshish karenge aane ki | maybe | review |
| abhi pakka nahi hai | maybe | declined |
| शायद आएंगे | maybe | accepted |
| My husband is coming but I can't | review | accepted |
| Can't wait to come! | accepted | review |
| No doubt, we'll be there | accepted | declined |
| We are not coming, sorry | declined | review |
| Wouldn't miss it | accepted | review |
| No way we'd skip this, coming! | accepted | review |

### Reminders and quiet hours

| Check | Result |
|---|---|
| Reminder decisions vs. oracle (after 3 days without reply, max 3 reminders) | 100.0% of 1332 |
| Reminders wrongly sent / missed | 0 / 0 |
| Duplicate reminder sends | 0 |
| Max reminders per RSVP respected | ✅ |
| Quiet-hours rule, every minute of a week (21:00-09:00 Asia/Kolkata) | 100.0% of 10080 |
| Config matches spec (21:00-09:00 IST) | ✅ |
| Dispatcher inside a quiet window (40 queued) | 0 sent |

## 4. API latency

In-process through the full app (middleware, auth, RBAC, tenant scoping, Postgres + pgvector, embedding model for search) on the seeded data; no network hop. 60 timed requests per endpoint after warm-up.

| Endpoint | p50 ms | p95 ms | p99 ms | max ms | p95 < 2 s |
|---|---|---|---|---|---|
| POST /auth/login | 117 | 168 | 185 | 185 | ✅ |
| GET /weddings | 9 | 12 | 13 | 13 | ✅ |
| GET /weddings/{id}/overview | 23 | 29 | 35 | 35 | ✅ |
| GET /vendors/search (hybrid) | 102 | 234 | 550 | 550 | ✅ |
| GET /vendors/benchmark | 13 | 21 | 21 | 21 | ✅ |
| GET /weddings/{id}/guests | 29 | 42 | 293 | 293 | ✅ |
| GET /weddings/{id}/attendance | 43 | 304 | 315 | 315 | ✅ |
| GET /weddings/{id}/timeline | 14 | 19 | 20 | 20 | ✅ |
| GET /weddings/{id}/budget | 13 | 16 | 17 | 17 | ✅ |
| GET /weddings/{id}/budget/forecast | 12 | 16 | 18 | 18 | ✅ |
| GET /approvals | 9 | 11 | 12 | 12 | ✅ |
| GET /agent-actions | 9 | 13 | 16 | 16 | ✅ |
| POST /weddings/{id}/agents/run | 89 | 380 | 381 | 381 | ✅ |

## 5. LLM cost per wedding

One wedding's workload through the real pipeline: 200 guests sending 267 replies, and 8 contracts padded to 5-6 pages (10 analyses including re-analysis). Token counts come from the mock (~4 characters per token) and are priced at the real model rates in `config/models.yaml`. Rule-readable replies never reach the LLM.

| Task | Model | Calls | Tokens in | Tokens out | Cost |
|---|---|---|---|---|---|
| contract_analysis | google/gemini-3.1-pro-preview | 10 | 59626 | 4182 | ₹14.91 |
| guest_comm | google/gemini-3.5-flash-lite | 74 | 10997 | 1036 | ₹0.52 |
| **total** |  | 84 |  |  | **₹15.43** |

Worst case (every reply goes to the LLM, every call is retried once, every output hits max_tokens): **₹94.51** per wedding (guest_comm ₹15.97, contract_analysis ₹78.54).


## Limits of this evaluation

- Offline means the mock LLM. Contract extraction and reply reading measure the rule-based paths; with `OPENROUTER_API_KEY` set, the real models take over and would need their own run.
- Labels come from the synthetic seed's generation rules and from hand-labelled examples, not from real planners or guests.
- Latency is server time in-process on one machine, without network or concurrent load.
- Cost uses ~4 characters per token and list prices; real tokenisers and price changes will move it.


_Run time: seed 28s, vendor_matching 6s, contracts 1s, rsvp 17s, latency 39s, cost 27s._
