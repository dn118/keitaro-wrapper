import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main
from app.db import Base
from app.keitaro import Tracker, tracker


@pytest.fixture
def system(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(main, "Session", sessionmaker(engine, expire_on_commit=False))
    campaigns = {}
    streams = []
    calls = []
    failures = {"stream": False}
    catalogs = {
        "domains": [{"id": 1, "name": "Domain"}],
        "groups": [{"id": 2, "name": "Group"}],
        "traffic_sources": [{"id": 3, "name": "Source"}],
        "offers": [{"id": n, "name": f"Offer {n}"} for n in (4, 5, 6)],
    }

    def handle(request):
        path = request.url.path.removeprefix("/admin_api/v1/")
        method = request.method
        body = json.loads(request.content) if request.content else None
        calls.append((method, path, body))
        assert request.headers["api-key"] == "fixture"
        if path in catalogs:
            if path == "groups":
                assert request.url.params["type"] == "campaigns"
            return httpx.Response(200, json=catalogs[path])
        if path == "campaigns":
            if method == "GET":
                return httpx.Response(200, json=list(campaigns.values()))
            campaigns[10] = {"id": 10, **body}
            return httpx.Response(200, json=campaigns[10])
        if path == "campaigns/10":
            if method == "PUT":
                campaigns[10].update(body)
            return httpx.Response(200, json=campaigns[10])
        if path == "campaigns/10/streams":
            return httpx.Response(200, json=streams)
        if path == "streams" and method == "POST":
            if body["position"] == 2 and failures["stream"]:
                return httpx.Response(500)
            stream = {"id": len(streams) + 100, **body}
            streams.append(stream)
            return httpx.Response(200, json=stream)
        if path == "streams/101" and method == "PUT":
            assert list(body) == ["offers"]
            streams[1].update(body)
            return httpx.Response(200, json=streams[1])
        if path == "report/build":
            assert body["filters"][0]["expression"] == 10
            assert body["grouping"] == ["stream_id", "offer_id"]
            return httpx.Response(200, json={
                "rows": [{"stream_id": 101, "offer_id": 4, "clicks": 7, "profit": -2.5}],
                "total": 1,
            })
        return httpx.Response(404)

    with httpx.Client(base_url="https://tracker.invalid/admin_api/v1/",
                      headers={"Api-Key": "fixture"}, transport=httpx.MockTransport(handle)) as http:
        main.app.dependency_overrides[tracker] = lambda: Tracker(http)
        try:
            yield TestClient(main.app), campaigns, streams, calls, failures
        finally:
            main.app.dependency_overrides.clear()
            engine.dispose()


DATA = {
    "name": "test_belov_demo", "country": "uz", "offer_id": 4,
    "domain_id": 1, "group_id": 2, "traffic_source_id": 3,
}


def test_create_recovers_partial_failure_without_duplicates(system):
    client, campaigns, streams, calls, failures = system
    failures["stream"] = True
    assert client.post("/api/campaigns", json=DATA).status_code == 502
    assert campaigns[10]["state"] == "disabled"
    assert len(streams) == 1
    failures["stream"] = False
    assert client.post("/api/campaigns", json=DATA).json() == {"id": 10, "name": DATA["name"]}
    assert client.post("/api/campaigns", json=DATA).status_code == 201
    assert campaigns[10]["state"] == "active"
    assert campaigns[10]["domain_id"] == 1
    assert len(streams) == 2
    assert streams[0]["filters"][0]["payload"] == ["UZ"]
    assert streams[1]["type"] == "default"
    assert sum(method == "POST" and path == "campaigns" for method, path, _ in calls) == 1


def test_editor_balances_shares_and_rejects_stale_edits(system):
    client, _, streams, _, _ = system
    assert client.post("/api/campaigns", json=DATA).status_code == 201
    original = client.get("/api/campaigns/10").json()["flows"][1]["version"]
    path = "/api/campaigns/10/flows/101/offers"
    first = client.post(path, json={"offer_id": 5, "version": original})
    assert first.status_code == 200
    assert client.post(path, json={"offer_id": 6, "version": original}).status_code == 409
    second = client.post(path, json={"offer_id": 6, "version": first.json()["version"]})
    assert [row["share"] for row in second.json()["offers"]] == [34, 33, 33]
    removed = client.delete(path + "/5", params={"version": second.json()["version"]})
    assert [row["offer_id"] for row in removed.json()["offers"]] == [4, 6]
    assert [row["share"] for row in streams[1]["offers"]] == [50, 50]


def test_report_and_validation(system):
    client, _, _, _, _ = system
    assert client.get("/").status_code == 200
    assert client.get("/app.js").status_code == 200
    assert client.get("/api/catalogs").json()["groups"][0]["id"] == 2
    assert client.post("/api/campaigns", json={**DATA, "country": "ZZ"}).status_code == 422
    assert client.post("/api/campaigns", json=DATA).status_code == 201
    report = client.get("/api/campaigns/10/report", params={"start": "2026-11-01", "end": "2026-11-19"})
    assert report.json()["rows"][0]["profit"] == -2.5
