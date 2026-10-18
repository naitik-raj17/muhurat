"""Each role can do exactly what Section 3A allows."""

from __future__ import annotations

import httpx
import pytest

from app.domain.enums import Role
from app.domain.rbac import ROLE_PERMISSIONS, Permission
from tests.conftest import ORG_A, login, new_wedding_payload, wedding_id


def test_permission_matrix_matches_spec() -> None:
    admin, planner = ROLE_PERMISSIONS[Role.ORG_ADMIN], ROLE_PERMISSIONS[Role.PLANNER]
    viewer, client = ROLE_PERMISSIONS[Role.VIEWER], ROLE_PERMISSIONS[Role.CLIENT]
    # org_admin: everything, incl. user management and Tier 2 overrides.
    assert Permission.ORG_MANAGE_USERS in admin
    assert Permission.APPROVAL_DECIDE_OVERRIDE in admin
    # planner: runs weddings and approves recommendations, but no user management/overrides.
    assert Permission.APPROVAL_DECIDE in planner
    assert Permission.ORG_MANAGE_USERS not in planner
    assert Permission.APPROVAL_DECIDE_OVERRIDE not in planner
    # viewer: read-only.
    assert all(p.value.endswith(":read") for p in viewer)
    # client: read-only on own wedding + RSVPs/preferences; never contracts or agents.
    assert client == {
        Permission.WEDDING_READ,
        Permission.VENDOR_READ,
        Permission.GUEST_READ,
        Permission.TIMELINE_READ,
        Permission.BUDGET_READ,
        Permission.RSVP_SUBMIT,
        Permission.PREFERENCES_SUBMIT,
    }


async def test_unauthenticated_is_rejected(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/weddings")
    assert r.status_code == 401
    assert r.json()["type"].endswith("/unauthorized")
    r = await client.get("/api/v1/weddings", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


async def test_viewer_is_read_only(
    client: httpx.AsyncClient, viewer_a: dict[str, str], planner_a: dict[str, str]
) -> None:
    wid = await wedding_id(client, viewer_a, "Jaipur")
    assert (
        await client.get(f"/api/v1/weddings/{wid}/overview", headers=viewer_a)
    ).status_code == 200
    assert (await client.get(f"/api/v1/weddings/{wid}/guests", headers=viewer_a)).status_code == 200
    denied = [
        ("POST", "/api/v1/weddings", new_wedding_payload()),
        ("POST", f"/api/v1/weddings/{wid}/timeline/draft", {}),
        ("POST", f"/api/v1/weddings/{wid}/budget/items", {"category": "x"}),
        ("PUT", f"/api/v1/weddings/{wid}/preferences", {"styles": []}),
        ("GET", "/api/v1/users", None),
    ]
    for method, path, body in denied:
        r = await client.request(method, path, json=body, headers=viewer_a)
        assert r.status_code == 403, (method, path, r.status_code)
        assert r.json()["type"].endswith("/forbidden")


async def test_client_sees_only_own_wedding(
    client: httpx.AsyncClient, client_a: dict[str, str], planner_a: dict[str, str]
) -> None:
    weddings = (await client.get("/api/v1/weddings", headers=client_a)).json()
    assert [w["city"] for w in weddings] == ["Jaipur"]
    other = await wedding_id(client, planner_a, "Bengaluru")
    r = await client.get(f"/api/v1/weddings/{other}", headers=client_a)
    assert r.status_code == 404


async def test_client_can_rsvp_and_set_preferences_but_not_plan(
    client: httpx.AsyncClient, client_a: dict[str, str]
) -> None:
    wid = await wedding_id(client, client_a, "Jaipur")
    r = await client.put(
        f"/api/v1/weddings/{wid}/preferences",
        json={"styles": ["royal"], "must_haves": ["live shehnai"], "notes": "veg only"},
        headers=client_a,
    )
    assert r.status_code == 200
    assert r.json()["preferences"]["must_haves"] == ["live shehnai"]

    guests = (await client.get(f"/api/v1/weddings/{wid}/guests?limit=1", headers=client_a)).json()
    g = guests["items"][0]
    r = await client.put(
        f"/api/v1/weddings/{wid}/guests/{g['id']}/rsvp",
        json={"event_id": g["rsvps"][0]["event_id"], "status": "accepted", "plus_ones": 1},
        headers=client_a,
    )
    assert r.status_code == 200

    for method, path, body in [
        ("POST", f"/api/v1/weddings/{wid}/timeline/draft", {}),
        ("POST", f"/api/v1/weddings/{wid}/shortlist", {"vendor_id": g["id"]}),
        ("POST", f"/api/v1/weddings/{wid}/budget/items", {"category": "x"}),
        (
            "POST",
            "/api/v1/vendors",
            {"name": "x", "category": "dj", "city": "x", "price_min": 1, "price_max": 2},
        ),
    ]:
        assert (await client.request(method, path, json=body, headers=client_a)).status_code == 403


async def test_only_admin_manages_users(
    client: httpx.AsyncClient, admin_a: dict[str, str], planner_a: dict[str, str]
) -> None:
    assert (await client.get("/api/v1/users", headers=planner_a)).status_code == 403
    r = await client.get("/api/v1/users", headers=admin_a)
    assert r.status_code == 200
    assert {u["role"] for u in r.json()} == {"org_admin", "planner", "viewer", "client"}

    r = await client.post(
        "/api/v1/users",
        json={
            "email": "new.planner@shubh-vivah.example",
            "full_name": "New Planner",
            "password": "longenough1",
            "role": "planner",
        },
        headers=admin_a,
    )
    assert r.status_code == 201, r.text
    # The new user can log in and acts within org A.
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": "new.planner@shubh-vivah.example", "password": "longenough1"},
    )
    assert r.status_code == 200
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}
    )
    assert me.json()["organization_name"].startswith("Shubh Vivah")


@pytest.mark.parametrize("role", ["admin", "planner", "viewer"])
async def test_me_reports_role_permissions(client: httpx.AsyncClient, role: str) -> None:
    headers = await login(client, f"{role}@{ORG_A}.example")
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    expected = {"admin": "org_admin", "planner": "planner", "viewer": "viewer"}[role]
    assert me["role"] == expected
    assert set(me["permissions"]) == {p.value for p in ROLE_PERMISSIONS[Role(expected)]}
