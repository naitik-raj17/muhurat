"""Guest-list rules: family/household grouping. Pure."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

_HONORIFICS = frozenset(
    {"mr", "mrs", "ms", "miss", "dr", "shri", "smt", "sri", "kumari", "late", "prof", "col"}
)
_SUFFIX_FAMILY = re.compile(r"\b(family|parivar|ji)\b", re.IGNORECASE)


@dataclass(frozen=True)
class GroupingInput:
    """One guest row as seen by the grouping rules."""

    index: int
    name: str
    side: str
    phone_hash: str | None
    email_hash: str | None
    family_label: str | None
    address: str | None


def surname(name: str) -> str:
    tokens = [t for t in re.split(r"[\s.]+", _SUFFIX_FAMILY.sub("", name)) if t]
    tokens = [t for t in tokens if t.lower() not in _HONORIFICS]
    return tokens[-1].lower() if len(tokens) >= 2 else ""


def _norm(text: str | None) -> str:
    return re.sub(r"\W+", " ", text or "").strip().lower()


def detect_groups(rows: Sequence[GroupingInput]) -> dict[int, tuple[int, str]]:
    """Union-find over strong household signals.

    Two guests are in the same family group if they share any of:
    an explicit family label; a phone number; an email; or surname + side + address.
    Surname alone is NOT enough (common surnames would merge unrelated families).

    Returns {row index: (group number, group label)} for guests in groups of 2+.
    """
    parent = list(range(len(rows)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    keys: dict[str, int] = {}

    def link(key: str, pos: int) -> None:
        if key in keys:
            union(keys[key], pos)
        else:
            keys[key] = pos

    for pos, r in enumerate(rows):
        if r.family_label and _norm(r.family_label):
            link("family:" + _norm(r.family_label), pos)
        if r.phone_hash:
            link("phone:" + r.phone_hash, pos)
        if r.email_hash:
            link("email:" + r.email_hash, pos)
        sn = surname(r.name)
        if sn and r.address and _norm(r.address):
            link(f"addr:{sn}|{r.side}|{_norm(r.address)}", pos)

    members: dict[int, list[int]] = {}
    for pos in range(len(rows)):
        members.setdefault(find(pos), []).append(pos)

    result: dict[int, tuple[int, str]] = {}
    group_no = 0
    for _root, positions in sorted(members.items()):
        if len(positions) < 2:
            continue
        group_no += 1
        first = rows[positions[0]]
        label = first.family_label or (
            f"{surname(first.name).title()} family ({first.side})"
            if surname(first.name)
            else f"Household of {first.name}"
        )
        for pos in positions:
            result[rows[pos].index] = (group_no, label)
    return result
