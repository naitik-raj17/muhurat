"""Markdown report for the evaluation results."""

from __future__ import annotations

from typing import Any


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _ok(flag: bool) -> str:
    return "✅" if flag else "❌"


def _table(header: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def render(r: dict[str, Any]) -> str:
    meta = r["meta"]
    vm, ct, rs, lat, cost = (
        r["vendor_matching"],
        r["contracts"],
        r["rsvp"],
        r["latency"],
        r["cost"],
    )
    imp, rep, rem = rs["import"], rs["replies"], rs["reminders"]
    out: list[str] = []
    out.append("# Evaluation report\n")
    out.append(
        f"Generated {meta['generated_at']} at commit `{meta['commit']}` on a fresh, seeded "
        f"`{meta['database']}` database. Offline: LLM = {meta['llm']}; embeddings = "
        f"`{meta['embedding_provider']}`. All data is synthetic. Reproduce with `make eval`.\n"
    )

    out.append("## Summary\n")
    out.append(
        _table(
            ["Area", "Metric", "Result", "Target"],
            [
                [
                    "Vendor matching",
                    f"precision@5 ({vm['n_queries']} queries)",
                    f"{vm['overall']['p5']:.3f} (max possible {vm['overall']['p5_max']:.3f})",
                    "–",
                ],
                [
                    "Vendor matching",
                    "recall@10",
                    f"{vm['overall']['r10']:.3f} (max possible {vm['overall']['r10_max']:.3f})",
                    "–",
                ],
                ["Vendor matching", "MRR", f"{vm['overall']['mrr']:.3f}", "–"],
                [
                    "Contracts",
                    f"field accuracy, seed set ({ct['seed']['contracts']})",
                    _pct(ct["seed"]["field_accuracy"]),
                    "–",
                ],
                [
                    "Contracts",
                    f"field accuracy, held-out set ({ct['held_out']['contracts']})",
                    _pct(ct["held_out"]["field_accuracy"]),
                    "–",
                ],
                ["Contracts", "risk-flag F1 (all)", f"{ct['overall']['risk_f1']:.3f}", "–"],
                [
                    "Contracts",
                    "quotes verified in source",
                    _pct(ct["overall"]["quote_verification"]),
                    "100%",
                ],
                [
                    "RSVP import",
                    f"report counts correct ({imp['rows']} rows)",
                    _pct(imp["report_count_accuracy"]),
                    "–",
                ],
                [
                    "RSVP import",
                    "stored guest fields correct",
                    _pct(imp["guest_field_accuracy"]),
                    "–",
                ],
                [
                    "RSVP replies",
                    f"intent accuracy ({rep['replies']} replies)",
                    _pct(rep["accuracy"]),
                    "–",
                ],
                [
                    "RSVP replies",
                    "precision of automatic updates",
                    _pct(rep["auto_precision"]),
                    "–",
                ],
                [
                    "RSVP reminders",
                    f"rule decisions correct ({rem['decisions']})",
                    _pct(rem["decision_accuracy"]),
                    "100%",
                ],
                [
                    "Quiet hours",
                    "messages sent in quiet hours",
                    rs["quiet_hours_dispatch"]["sent_during_quiet_hours"],
                    "0",
                ],
                [
                    "API latency",
                    "worst p95 across endpoints",
                    f"{lat['worst_p95_ms']:.0f} ms",
                    f"< {lat['target_p95_ms']:.0f} ms {_ok(lat['all_meet_target'])}",
                ],
                [
                    "Cost",
                    "LLM cost per wedding",
                    f"₹{cost['cost_inr']:.2f}",
                    f"< ₹{cost['target_inr']} {_ok(cost['meets_target'])}",
                ],
                [
                    "Cost",
                    "worst case per wedding",
                    f"₹{cost['worst_case_inr']:.2f}",
                    f"< ₹{cost['target_inr']} {_ok(cost['worst_case_meets_target'])}",
                ],
            ],
        )
    )

    # ---- vendor matching
    out.append("\n## 1. Vendor matching\n")
    out.append(
        f"{vm['n_queries']} queries over {vm['catalogue_size']} catalogue vendors. A vendor is "
        "relevant when it is in the requested category, lists the queried style, and (if the "
        "query sets them) is in the city and starts within the budget. The style appears only "
        "in the free-text query, never as a filter. Small relevant sets cap precision@5 and "
        "large ones cap recall@10, so each is shown with the best a perfect ranking could "
        f"reach. Mean relevant vendors per query: {vm['mean_relevant']:.1f}.\n"
    )
    out.append(
        _table(
            ["Query type", "n", "P@5", "P@5 max", "R@10", "R@10 max", "MRR"],
            [
                [
                    k,
                    v["n"],
                    f"{v['p5']:.3f}",
                    f"{v['p5_max']:.3f}",
                    f"{v['r10']:.3f}",
                    f"{v['r10_max']:.3f}",
                    f"{v['mrr']:.3f}",
                ]
                for k, v in vm["by_kind"].items()
            ]
            + [
                [
                    "**all**",
                    vm["n_queries"],
                    f"{vm['overall']['p5']:.3f}",
                    f"{vm['overall']['p5_max']:.3f}",
                    f"{vm['overall']['r10']:.3f}",
                    f"{vm['overall']['r10_max']:.3f}",
                    f"{vm['overall']['mrr']:.3f}",
                ]
            ],
        )
    )
    misses = [q for q in vm["queries"] if q["p5"] < q["p5_max"] or q["r10"] < q["r10_max"]]
    if misses:
        out.append("\nQueries below their ceiling:\n")
        out.append(
            _table(
                ["Query", "Category", "City", "Budget", "Relevant", "P@5", "R@10"],
                [
                    [
                        q["q"],
                        q["category"],
                        q["city"] or "–",
                        q["budget_max"] or "–",
                        q["n_relevant"],
                        f"{q['p5']:.2f}",
                        f"{q['r10']:.2f}",
                    ]
                    for q in misses
                ],
            )
        )

    # ---- contracts
    out.append("\n## 2. Contract extraction\n")
    out.append(
        "8 fields per contract: advance %, advance refundable, and presence of each of the "
        "6 clauses. The **seed** contracts were in view while the rule-based extractor was "
        "written; the **held-out** contracts use other headings and wording. Offline, the "
        "mock LLM returns the rule-based extraction, so these numbers measure the fallback "
        "path, not a real model.\n"
    )
    out.append(
        _table(
            [
                "Set",
                "Contracts",
                "Field accuracy",
                "Risk precision",
                "Risk recall",
                "Exact risk set",
                "Quotes verified",
            ],
            [
                [
                    name,
                    s["contracts"],
                    _pct(s["field_accuracy"]),
                    f"{s['risk_precision']:.3f}",
                    f"{s['risk_recall']:.3f}",
                    _pct(s["risk_exact_match"]),
                    _pct(s["quote_verification"]),
                ]
                for name, s in (
                    ("seed", ct["seed"]),
                    ("held-out", ct["held_out"]),
                    ("all", ct["overall"]),
                )
            ],
        )
    )
    out.append("\nPer field (all contracts):\n")
    out.append(
        _table(["Field", "Accuracy"], [[k, _pct(v)] for k, v in ct["overall"]["per_field"].items()])
    )
    out.append("\nPer contract:\n")
    out.append(
        _table(
            ["Contract", "Set", "Fields", "Wrong fields", "Missed flags", "Extra flags"],
            [
                [
                    c["key"],
                    c["split"],
                    _pct(c["field_accuracy"]),
                    ", ".join(c["wrong_fields"]) or "–",
                    ", ".join(c["risk_fn"]) or "–",
                    ", ".join(c["risk_fp"]) or "–",
                ]
                for c in ct["contracts"]
            ],
        )
    )

    # ---- rsvp
    out.append("\n## 3. RSVP pipeline\n")
    out.append("### Import\n")
    out.append(
        f"{imp['lists']} generated lists, {imp['rows']} rows (mixed phone formats, invalid "
        "phones, missing sides, duplicate rows, households sharing a phone or a family "
        "label).\n"
    )
    out.append(
        _table(
            ["Check", "Result"],
            [
                ["Import report counts match truth", _pct(imp["report_count_accuracy"])],
                [
                    "Stored guest fields (E.164 phone, language, side)",
                    _pct(imp["guest_field_accuracy"]),
                ],
                ["Family grouping precision (pairs)", f"{imp['grouping_precision']:.3f}"],
                ["Family grouping recall (pairs)", f"{imp['grouping_recall']:.3f}"],
            ],
        )
    )
    bad_lists = [x for x in imp["per_list"] if x["mismatches"]]
    if bad_lists:
        out.append(
            "\nMismatched counts: "
            + "; ".join(
                ", ".join(
                    f"{k} got {v['got']} want {v['want']}" for k, v in x["mismatches"].items()
                )
                for x in bad_lists
            )
            + "\n"
        )

    out.append("\n### Replies\n")
    out.append(
        f"{rep['replies']} hand-labelled replies (English, Hinglish, Hindi), each sent through "
        "the real path: invite → outbox → simulated WhatsApp reply → intent → ActionPolicy → "
        "RSVP. A reply that doesn't answer the invitation should go to a person.\n"
    )
    out.append(
        _table(
            ["Metric", "Result"],
            [
                ["Accuracy (status, or correctly sent to a person)", _pct(rep["accuracy"])],
                ["Precision of automatic updates", _pct(rep["auto_precision"])],
                ["Wrong automatic updates", rep["wrong_auto_updates"]],
                ["Answers handled automatically (coverage)", _pct(rep["auto_coverage"])],
                ["Non-answers sent to a person", _pct(rep["review_recall"])],
                ["Plus-ones correct (accepted replies)", _pct(rep["plus_ones_accuracy"])],
            ]
            + [
                [f"Accuracy, {k} ({v['n']})", _pct(v["accuracy"])]
                for k, v in rep["by_lang"].items()
            ],
        )
    )
    if rep["errors"]:
        out.append("\nMisread replies (expected → got; “review” = sent to a person):\n")
        out.append(
            _table(
                ["Reply", "Expected", "Got"],
                [[e["text"], e["expected"], e["got"]] for e in rep["errors"]],
            )
        )

    out.append("\n### Reminders and quiet hours\n")
    q, qd = rs["quiet_hours_rule"], rs["quiet_hours_dispatch"]
    out.append(
        _table(
            ["Check", "Result"],
            [
                [
                    f"Reminder decisions vs. oracle ({rem['rule']})",
                    f"{_pct(rem['decision_accuracy'])} of {rem['decisions']}",
                ],
                ["Reminders wrongly sent / missed", f"{rem['wrongly_sent']} / {rem['missed']}"],
                ["Duplicate reminder sends", rem["duplicate_sends"]],
                ["Max reminders per RSVP respected", _ok(rem["max_per_rsvp_respected"])],
                [
                    f"Quiet-hours rule, every minute of a week ({q['config']})",
                    f"{_pct(q['accuracy'])} of {q['timestamps']}",
                ],
                ["Config matches spec (21:00-09:00 IST)", _ok(q["matches_spec"])],
                [
                    f"Dispatcher inside a quiet window ({qd['queued']} queued)",
                    f"{qd['sent_during_quiet_hours']} sent",
                ],
            ],
        )
    )

    # ---- latency
    out.append("\n## 4. API latency\n")
    out.append(
        "In-process through the full app (middleware, auth, RBAC, tenant scoping, Postgres + "
        "pgvector, embedding model for search) on the seeded data; no network hop. "
        f"{lat['endpoints'][0]['n']} timed requests per endpoint after warm-up.\n"
    )
    out.append(
        _table(
            ["Endpoint", "p50 ms", "p95 ms", "p99 ms", "max ms", "p95 < 2 s"],
            [
                [
                    e["endpoint"],
                    f"{e['p50_ms']:.0f}",
                    f"{e['p95_ms']:.0f}",
                    f"{e['p99_ms']:.0f}",
                    f"{e['max_ms']:.0f}",
                    _ok(e["meets_target"]),
                ]
                for e in lat["endpoints"]
            ],
        )
    )

    # ---- cost
    wl = cost["workload"]
    out.append("\n## 5. LLM cost per wedding\n")
    out.append(
        f"One wedding's workload through the real pipeline: {wl['guests']} guests sending "
        f"{wl['replies']} replies, and {wl['contracts']} contracts padded to 5-6 pages "
        f"({wl['contract_analyses']} analyses including re-analysis). Token counts come from "
        "the mock (~4 characters per token) and are priced at the real model rates in "
        "`config/models.yaml`. Rule-readable replies never reach the LLM.\n"
    )
    out.append(
        _table(
            ["Task", "Model", "Calls", "Tokens in", "Tokens out", "Cost"],
            [
                [
                    k,
                    v["model"],
                    v["calls"],
                    v["tokens_in"],
                    v["tokens_out"],
                    f"₹{v['cost_inr']:.2f}",
                ]
                for k, v in cost["by_task"].items()
            ]
            + [["**total**", "", cost["llm_calls"], "", "", f"**₹{cost['cost_inr']:.2f}**"]],
        )
    )
    out.append(
        f"\nWorst case (every reply goes to the LLM, every call is retried once, every output "
        f"hits max_tokens): **₹{cost['worst_case_inr']:.2f}** per wedding "
        f"({', '.join(f'{k} ₹{v:.2f}' for k, v in cost['worst_case_by_task'].items())}).\n"
    )

    out.append("\n## Limits of this evaluation\n")
    out.append(
        "- Offline means the mock LLM. Contract extraction and reply reading measure the "
        "rule-based paths; with `OPENROUTER_API_KEY` set, the real models take over and would "
        "need their own run.\n"
        "- Labels come from the synthetic seed's generation rules and from hand-labelled "
        "examples, not from real planners or guests.\n"
        "- Latency is server time in-process on one machine, without network or concurrent "
        "load.\n"
        "- Cost uses ~4 characters per token and list prices; real tokenisers and price "
        "changes will move it.\n"
    )
    t = meta["timings_s"]
    out.append("\n_Run time: " + ", ".join(f"{k} {v:.0f}s" for k, v in t.items()) + "._\n")
    return "\n".join(out)
