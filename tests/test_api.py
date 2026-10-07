"""The API surface, the auth gates and the queue rules."""

from __future__ import annotations

import pytest

from .conftest import FRIEND_PASSWORD, OWNER_PASSWORD


def first_chest(client) -> dict:
    bases = client.get("/api/bases").json()["bases"]
    base = next(b for b in bases if b["chest_count"])
    chests = client.get(f"/api/bases/{base['base_guid']}/chests").json()["chests"]
    return chests[0]


# -- auth ------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/bases"),
        ("get", "/api/status"),
        ("get", "/api/edits"),
        ("get", "/api/items"),
        ("post", "/api/maintenance/run"),
    ],
)
def test_every_endpoint_needs_a_session(client, method, path):
    assert getattr(client, method)(path).status_code == 401


def test_login_with_the_friend_password(client):
    response = client.post("/api/session", json={"password": FRIEND_PASSWORD})
    assert response.status_code == 200
    assert response.json()["role"] == "friend"
    assert client.get("/api/session").json()["role"] == "friend"


def test_login_with_the_owner_password(client):
    response = client.post("/api/session", json={"password": OWNER_PASSWORD})
    assert response.json()["role"] == "owner"


def test_login_with_a_wrong_password(client):
    assert client.post("/api/session", json={"password": "nope"}).status_code == 401


def test_logout_clears_the_session(friend_client):
    assert friend_client.delete("/api/session").status_code == 204
    friend_client.cookies.clear()
    assert friend_client.get("/api/bases").status_code == 401


# -- browsing --------------------------------------------------------------


def test_bases_carry_counts_for_the_card_grid(friend_client):
    payload = friend_client.get("/api/bases").json()
    assert len(payload["bases"]) == 3
    assert payload["lock_codes_available"] is True
    for base in payload["bases"]:
        assert {"chest_count", "locked_count", "pending_chests"} <= base.keys()
    assert sum(b["chest_count"] for b in payload["bases"]) == 4


def test_a_chest_out_of_range_of_any_base_is_still_reachable(friend_client):
    payload = friend_client.get("/api/bases").json()
    assert payload["unassigned"]["chest_count"] == 1
    chests = friend_client.get("/api/bases/unassigned/chests").json()["chests"]
    assert len(chests) == 1


def test_chest_detail_has_slots_and_fill_level(friend_client):
    chest = first_chest(friend_client)
    detail = friend_client.get(f"/api/chests/{chest['container_guid']}").json()
    assert len(detail["slots"]) == detail["slot_count"] == 8
    assert detail["pending_edits"] == []
    assert 0.0 < detail["fill_ratio"] <= 1.0


def test_slots_resolve_display_names_and_fall_back_to_the_raw_id(friend_client):
    detail = friend_client.get("/api/chests/aaaa0003-0000-0000-0000-000000000003").json()
    by_index = {s["slot_index"]: s for s in detail["slots"]}
    # Seeded id resolves to its curated name.
    assert by_index[1]["display_name"] == "Ingot"
    # Unknown id renders as the raw string rather than being hidden.
    assert by_index[0]["item_id"] == "PalSphere_Mega"
    assert by_index[0]["display_name"] == "PalSphere_Mega"


def test_missing_chest_is_a_404(friend_client):
    assert friend_client.get("/api/chests/does-not-exist").status_code == 404
    assert friend_client.get("/api/bases/does-not-exist/chests").status_code == 404


# -- nicknames -------------------------------------------------------------


def test_set_and_read_back_a_nickname(friend_client):
    chest = first_chest(friend_client)
    guid = chest["container_guid"]
    response = friend_client.patch(
        f"/api/chests/{guid}/meta",
        json={"nickname": "  Ammo dump  ", "notes": "top shelf"},
    )
    assert response.status_code == 200
    assert response.json()["nickname"] == "Ammo dump"
    assert friend_client.get(f"/api/chests/{guid}").json()["notes"] == "top shelf"


def test_a_blank_nickname_clears_it(friend_client):
    chest = first_chest(friend_client)
    guid = chest["container_guid"]
    friend_client.patch(f"/api/chests/{guid}/meta", json={"nickname": "x"})
    friend_client.patch(f"/api/chests/{guid}/meta", json={"nickname": "   "})
    assert friend_client.get(f"/api/chests/{guid}").json()["nickname"] is None


def test_search_finds_a_chest_by_nickname_lock_code_and_contents(friend_client):
    guid = "aaaa0001-0000-0000-0000-000000000001"
    friend_client.patch(f"/api/chests/{guid}/meta", json={"nickname": "Sphere stash"})

    for term in ("Sphere stash", "1234", "PalSphere", "Pal Sphere"):
        hits = friend_client.get("/api/chests/search", params={"q": term}).json()["chests"]
        assert any(c["container_guid"] == guid for c in hits), f"{term!r} found nothing"


# -- the edit queue --------------------------------------------------------


def test_queue_an_edit_and_see_it_on_the_chest(friend_client):
    guid = "aaaa0004-0000-0000-0000-000000000004"
    response = friend_client.post(
        f"/api/chests/{guid}/edits",
        json={"slot_index": 0, "item_id": "Wood", "stack_count": 50},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["requested_by"] == "friend"
    # Every queued edit carries when it is expected to land, because a queued
    # edit not taking effect immediately is what surprises people.
    assert body["expected_window"]

    detail = friend_client.get(f"/api/chests/{guid}").json()
    assert len(detail["pending_edits"]) == 1
    slot = next(s for s in detail["slots"] if s["slot_index"] == 0)
    # Rendered as an overlay on the slot, not only as a separate list.
    assert slot["has_pending"] is True
    assert slot["pending_item_id"] == "Wood"
    assert slot["pending_stack_count"] == 50


def test_clearing_a_slot_is_a_null_item(friend_client):
    guid = "aaaa0001-0000-0000-0000-000000000001"
    response = friend_client.post(
        f"/api/chests/{guid}/edits",
        json={"slot_index": 1, "item_id": None, "stack_count": 0},
    )
    assert response.status_code == 201
    assert response.json()["item_id"] is None


def test_an_item_with_a_zero_stack_is_rejected_rather_than_treated_as_a_clear(
    friend_client,
):
    guid = "aaaa0001-0000-0000-0000-000000000001"
    response = friend_client.post(
        f"/api/chests/{guid}/edits",
        json={"slot_index": 2, "item_id": "Wood", "stack_count": 0},
    )
    assert response.status_code == 422
    assert "item_id = null" in response.json()["detail"]


def test_a_slot_out_of_range_is_rejected(friend_client):
    guid = "aaaa0001-0000-0000-0000-000000000001"
    response = friend_client.post(
        f"/api/chests/{guid}/edits",
        json={"slot_index": 99, "item_id": "Wood", "stack_count": 1},
    )
    assert response.status_code == 422
    assert "8 slots" in response.json()["detail"]


def test_a_stack_over_the_items_cap_is_rejected(friend_client):
    guid = "aaaa0001-0000-0000-0000-000000000001"
    response = friend_client.post(
        f"/api/chests/{guid}/edits",
        json={"slot_index": 2, "item_id": "PalSphere", "stack_count": 999},
    )
    assert response.status_code == 422
    assert "stacks to 50" in response.json()["detail"]


def test_two_open_edits_on_one_slot_are_refused(friend_client):
    guid = "aaaa0001-0000-0000-0000-000000000001"
    payload = {"slot_index": 2, "item_id": "Wood", "stack_count": 5}
    assert friend_client.post(f"/api/chests/{guid}/edits", json=payload).status_code == 201
    second = friend_client.post(f"/api/chests/{guid}/edits", json=payload)
    assert second.status_code == 409
    assert "already has edit" in second.json()["detail"]


def test_cancel_a_queued_edit(friend_client):
    guid = "aaaa0001-0000-0000-0000-000000000001"
    edit_id = friend_client.post(
        f"/api/chests/{guid}/edits",
        json={"slot_index": 2, "item_id": "Wood", "stack_count": 5},
    ).json()["id"]
    assert friend_client.delete(f"/api/edits/{edit_id}").status_code == 204
    assert friend_client.get("/api/edits", params={"status": "cancelled"}).json()["edits"]
    # The slot is free again once the edit is cancelled.
    assert friend_client.post(
        f"/api/chests/{guid}/edits",
        json={"slot_index": 2, "item_id": "Stone", "stack_count": 5},
    ).status_code == 201


def test_cancelling_a_missing_edit_is_a_404(friend_client):
    assert friend_client.delete("/api/edits/9999").status_code == 404


def test_the_queue_endpoint_reports_depth_and_the_next_window(friend_client):
    guid = "aaaa0001-0000-0000-0000-000000000001"
    friend_client.post(
        f"/api/chests/{guid}/edits",
        json={"slot_index": 2, "item_id": "Wood", "stack_count": 5},
    )
    payload = friend_client.get("/api/edits").json()
    assert payload["queue"]["queued"] == 1
    assert payload["next_window"]
    assert payload["edits"][0]["nickname"] is None


def test_an_unknown_status_filter_is_rejected(friend_client):
    assert friend_client.get("/api/edits", params={"status": "wat"}).status_code == 422


# -- items -----------------------------------------------------------------


def test_the_item_catalogue_is_searchable_for_the_picker(friend_client):
    payload = friend_client.get("/api/items", params={"q": "sphere"}).json()["items"]
    ids = {item["item_id"] for item in payload}
    assert "PalSphere" in ids
    assert "PalSphere_Mega" in ids  # observed, still offered


# -- status ----------------------------------------------------------------


def test_status_reports_ingest_queue_and_window(friend_client):
    payload = friend_client.get("/api/status").json()
    assert payload["last_good_ingest"]["chest_count"] == 5
    assert payload["stale"] is False
    assert payload["queue"]["queued"] == 0
    assert payload["next_window"]
    assert payload["window_in_progress"] is False
    assert payload["save_backend"] == "fixture"
    assert payload["lock_codes_available"] is True


# -- the owner gates -------------------------------------------------------


def test_a_friend_cannot_trigger_the_window(friend_client):
    response = friend_client.post("/api/maintenance/run", params={"confirm": True})
    assert response.status_code == 403
    assert "owner password" in response.json()["detail"]


def test_the_owner_must_confirm_before_the_window_runs(owner_client):
    response = owner_client.post("/api/maintenance/run")
    assert response.status_code == 400
    assert "confirm=true" in response.json()["detail"]


def test_a_friend_cannot_run_ingest(friend_client):
    assert friend_client.post("/api/ingest/run").status_code == 403


def test_the_window_refuses_to_run_while_another_holds_the_lock(owner_client, config):
    from paleditor.locking import FileLock

    with FileLock(config.maintenance.lock_file):
        response = owner_client.post("/api/maintenance/run", params={"confirm": True})
    assert response.status_code == 409
    assert "already running" in response.json()["detail"]
