"""
Route tests - the HTTP surface via TestClient.

Covers the fact CRUD, search, info routes, and the bearer-auth posture.
"""
from __future__ import annotations


# ── info ────────────────────────────────────────────────────────────────────

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_root_reports_service_and_finder(client):
    body = client.get("/").json()
    assert body["service"] == "SerenLoci"
    assert body["finder"] == "lexical"
    assert "counts" in body


# ── fact CRUD ───────────────────────────────────────────────────────────────

def test_set_fresh_fact_has_null_superseded(client):
    r = client.post("/fact", json={"key": "k", "value": "v", "why": "because"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["superseded"] is None


def test_set_replacing_fact_names_superseded_id(client):
    client.post("/fact", json={"key": "k", "value": "v1"})
    r = client.post("/fact", json={"key": "k", "value": "v2"})
    assert r.json()["superseded"] is not None


def test_get_fact_and_404(client):
    client.post("/fact", json={"key": "k", "value": "v"})
    assert client.get("/fact", params={"key": "k"}).json()["value"] == "v"
    assert client.get("/fact", params={"key": "missing"}).status_code == 404


def test_get_fact_defaults_to_fundamentals(client):
    client.post("/fact", json={"key": "k", "value": "fund"})
    # no project param -> fundamentals
    assert client.get("/fact", params={"key": "k"}).json()["project"] == "*"


def test_history_endpoint(client):
    client.post("/fact", json={"key": "k", "value": "v1"})
    client.post("/fact", json={"key": "k", "value": "v2"})
    body = client.get("/fact/history", params={"key": "k"}).json()
    assert body["count"] == 2


def test_forget_then_404(client):
    client.post("/fact", json={"key": "k", "value": "v"})
    assert client.delete("/fact", params={"key": "k"}).status_code == 200
    assert client.get("/fact", params={"key": "k"}).status_code == 404
    # second forget has nothing live to retire
    assert client.delete("/fact", params={"key": "k"}).status_code == 404


# ── search + bulk ───────────────────────────────────────────────────────────

def test_search_endpoint(client):
    client.post("/fact", json={"key": "posh.brace_style", "value": "curlies new line"})
    body = client.post("/search", json={"query": "posh.brace_style"}).json()
    assert body["finder"] == "lexical"
    assert body["hits"][0]["match_kind"] == "exact"
    assert body["hits"][0]["score"] == 1.0


def test_facts_and_counts_endpoints(client):
    client.post("/fact", json={"key": "a", "value": "1"})
    client.post("/fact", json={"project": "p", "key": "b", "value": "2"})
    assert client.get("/facts").json()["count"] == 2
    assert client.get("/facts", params={"project": "p"}).json()["count"] == 1
    assert client.get("/counts").json()["live"] == 2


# ── auth posture ────────────────────────────────────────────────────────────

def test_auth_public_paths_open(auth_client):
    assert auth_client.get("/health").status_code == 200
    assert auth_client.get("/").status_code == 200


def test_auth_protected_route_401_without_token(auth_client):
    r = auth_client.post("/fact", json={"key": "k", "value": "v"})
    assert r.status_code == 401


def test_auth_protected_route_200_with_token(auth_client):
    r = auth_client.post("/fact", json={"key": "k", "value": "v"},
                         headers={"Authorization": "Bearer sekret"})
    assert r.status_code == 200


def test_auth_wrong_token_401(auth_client):
    r = auth_client.get("/counts", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
