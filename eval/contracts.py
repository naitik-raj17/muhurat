"""Contract extraction: field-level accuracy against known answers.

Every contract goes through the real pipeline: upload (PDF) -> outbox -> text extraction ->
LLM extraction (offline: the mock, which uses the rule-based extractor) -> quote verification
-> risk rules. Scored per contract:

* fields (8): advance %, whether the advance is refundable, and presence of each of the six
  clauses (payment schedule, cancellation, refund, liability, force majeure, deliverables);
* risk flags: precision / recall over (contract, risk code) pairs, and exact-set matches;
* quotes: share of extracted clauses whose quote was found verbatim in the document.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from eval.harness import dispatch, login, new_wedding
from eval.heldout_contracts import HELD_OUT, expected_present
from eval.heldout_contracts import render_pdf as render_heldout

CLAUSES = (
    "payment_schedule",
    "cancellation",
    "refund",
    "liability",
    "force_majeure",
    "deliverables",
)


@dataclass
class Case:
    key: str
    split: str  # "seed" or "held_out"
    pdf: bytes
    advance_percent: float | None
    advance_refundable: bool | None
    present: set[str]
    risk_codes: set[str]


def _cases() -> list[Case]:
    from seed.contracts import CONTRACT_SPECS
    from seed.contracts import render_pdf as render_seed

    cases = [
        Case(
            s.key,
            "seed",
            render_seed(s),
            s.expected["advance_percent"],
            s.expected["advance_refundable"],
            set(s.expected["has"]),
            set(s.expected["risk_codes"]),
        )
        for s in CONTRACT_SPECS
    ]
    cases += [
        Case(
            c.key,
            "held_out",
            render_heldout(c),
            c.expected["advance_percent"],
            c.expected["advance_refundable"],
            set(expected_present(c)),
            set(c.expected["risk_codes"]),
        )
        for c in HELD_OUT
    ]
    return cases


def _same_percent(got: float | None, want: float | None) -> bool:
    if want is None:
        return got is None
    return got is not None and abs(float(got) - want) < 0.5


def _score(case: Case, out: dict[str, Any]) -> dict[str, Any]:
    ex = out["extracted_clauses"]
    found = set(ex.get("clauses", {}))
    got_pct = ex.get("advance_percent")
    fields = {
        "advance_percent": _same_percent(got_pct, case.advance_percent),
        "advance_refundable": ex.get("advance_refundable") == case.advance_refundable,
    }
    for clause in CLAUSES:
        fields[f"has_{clause}"] = (clause in found) == (clause in case.present)
    codes = {f["code"] for f in out["risk_flags"]}
    clauses = ex.get("clauses", {}).values()
    return {
        "key": case.key,
        "split": case.split,
        "fields": fields,
        "field_accuracy": sum(fields.values()) / len(fields),
        "wrong_fields": [k for k, ok in fields.items() if not ok],
        "risk_tp": len(codes & case.risk_codes),
        "risk_fp": sorted(codes - case.risk_codes),
        "risk_fn": sorted(case.risk_codes - codes),
        "risk_exact": codes == case.risk_codes,
        "quotes": len(clauses),
        "quotes_verified": sum(1 for c in clauses if c.get("verified")),
        "needs_human_review": out["needs_human_review"],
        "method": ex.get("method"),
    }


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    fields = [ok for r in rows for ok in r["fields"].values()]
    tp = sum(r["risk_tp"] for r in rows)
    fp = sum(len(r["risk_fp"]) for r in rows)
    fn = sum(len(r["risk_fn"]) for r in rows)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    quotes = sum(r["quotes"] for r in rows)
    per_field: dict[str, float] = {}
    for name in rows[0]["fields"]:
        per_field[name] = sum(r["fields"][name] for r in rows) / len(rows)
    return {
        "contracts": len(rows),
        "field_accuracy": sum(fields) / len(fields),
        "fields_scored": len(fields),
        "per_field": per_field,
        "risk_precision": precision,
        "risk_recall": recall,
        "risk_f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "risk_exact_match": sum(r["risk_exact"] for r in rows) / len(rows),
        "quote_verification": sum(r["quotes_verified"] for r in rows) / quotes if quotes else 1.0,
    }


async def run(client: httpx.AsyncClient) -> dict[str, Any]:
    headers = await login(client, "planner")
    wedding = await new_wedding(client, headers, "Eval: contracts")
    cases = _cases()
    ids: dict[str, str] = {}
    for case in cases:
        r = await client.post(
            f"/api/v1/weddings/{wedding}/contracts",
            files={"file": (f"{case.key}.pdf", case.pdf, "application/pdf")},
            headers=headers,
        )
        r.raise_for_status()
        ids[case.key] = r.json()["id"]
    await dispatch()

    rows = []
    for case in cases:
        r = await client.get(
            f"/api/v1/weddings/{wedding}/contracts/{ids[case.key]}", headers=headers
        )
        r.raise_for_status()
        out = r.json()
        if out["status"] != "analyzed":
            raise RuntimeError(f"contract {case.key} not analysed: {out['status']}")
        rows.append(_score(case, out))

    return {
        "overall": _aggregate(rows),
        "seed": _aggregate([r for r in rows if r["split"] == "seed"]),
        "held_out": _aggregate([r for r in rows if r["split"] == "held_out"]),
        "contracts": [{k: v for k, v in r.items() if k != "fields"} for r in rows],
    }
