import hashlib
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pycountry
from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError

from app.db import CampaignOperation, Session
from app.keitaro import Tracker, tracker

app = FastAPI(title="Keitaro Wrapper")
API = Annotated[Tracker, Depends(tracker)]
ID = Annotated[int, Field(gt=0)]
PREFIX = os.getenv("CAMPAIGN_PREFIX", "test_belov_")
WRITE_LOCK = Lock()
CATALOGS = {
    "domains": "domains",
    "groups": "groups?type=campaigns",
    "sources": "traffic_sources",
    "offers": "offers",
}


class CampaignInput(BaseModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    country: str
    offer_id: ID
    domain_id: ID
    group_id: ID
    traffic_source_id: ID

    @field_validator("name")
    @classmethod
    def test_prefix(cls, value):
        if not value.startswith(PREFIX) or len(value) == len(PREFIX):
            raise ValueError(f"Введите название с префиксом {PREFIX}.")
        return value

    @field_validator("country")
    @classmethod
    def country_code(cls, value):
        value = value.strip().upper()
        if not pycountry.countries.get(alpha_2=value):
            raise ValueError("Введите двухбуквенный код страны ISO 3166-1.")
        return value


class OfferChange(BaseModel):
    offer_id: ID
    version: str


def settings():
    zone = os.getenv("KEITARO_TIMEZONE", "UTC")
    try:
        return ZoneInfo(zone)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(503, "Проверьте KEITARO_TIMEZONE в .env.") from exc


def by_id(items, item_id):
    return next((item for item in items if str(item.get("id")) == str(item_id)), None)


def active_items(api, path):
    return [item for item in api.list(path) if item.get("state") not in {"deleted", "archived"}]


@app.get("/api/settings")
def get_settings():
    today = datetime.now(settings()).date()
    return {"prefix": PREFIX, "timezone": settings().key,
            "start": today - timedelta(days=6), "end": today}


@app.get("/api/catalogs")
def catalogs(api: API):
    return {
        name: [{"id": item["id"], "name": item["name"]} for item in active_items(api, path)]
        for name, path in CATALOGS.items()
    }


@app.get("/api/campaigns")
def campaigns(api: API):
    return [{"id": item["id"], "name": item["name"], "state": item.get("state")}
            for item in active_items(api, "campaigns") if item["name"].startswith(PREFIX)]


def flows(api, campaign_id):
    return active_items(api, f"campaigns/{campaign_id}/streams")


def owned_campaign(api, campaign_id):
    campaign = api.get(f"campaigns/{campaign_id}")
    if not campaign["name"].startswith(PREFIX) or campaign.get("state") == "deleted":
        raise HTTPException(404, "Кампания с вашим тестовым префиксом не найдена.")
    return campaign


def desired_flows(data, campaign_id):
    common = {"campaign_id": campaign_id, "state": "active", "collect_clicks": True}
    return [
        dict(common, name=f"Google · {data.country}", position=1, type="regular",
             schema="redirect", action_type="location", action_payload="https://www.google.com/",
             filters=[{"name": "geo", "mode": "accept", "payload": [data.country]}]),
        dict(common, name="Офферы", position=2, type="default", schema="landings",
             offers=[{"offer_id": data.offer_id, "state": "active", "share": 100}]),
    ]


@app.post("/api/campaigns", status_code=201)
def create_campaign(data: CampaignInput, api: API):
    alias = data.name.lower()
    request_json = data.model_dump_json()
    with WRITE_LOCK, Session() as db:
        operation = db.get(CampaignOperation, alias)
        if operation is None:
            if any(item.get("alias", "").lower() == alias for item in api.list("campaigns")):
                raise HTTPException(409, "Кампания с таким алиасом уже существует.")
            for name, path in CATALOGS.items():
                field = {"domains": "domain_id", "groups": "group_id",
                         "sources": "traffic_source_id", "offers": "offer_id"}[name]
                if not by_id(active_items(api, path), getattr(data, field)):
                    raise HTTPException(422, f"Выбранное значение отсутствует в справочнике {name}.")
            operation = CampaignOperation(alias=alias, request_json=request_json, state="pending")
            db.add(operation)
            try:
                db.commit()
            except IntegrityError as exc:
                raise HTTPException(409, "Создание уже запущено. Повторите запрос позже.") from exc
        if operation.request_json != request_json:
            raise HTTPException(409, "Для этого названия уже сохранены другие параметры.")
        if operation.state == "completed":
            return {"id": operation.campaign_id, "name": data.name}

        if operation.campaign_id is None:
            existing = next((row for row in active_items(api, "campaigns")
                             if row.get("alias") == alias and row.get("name") == data.name), None)
            campaign = existing or api.call("POST", "campaigns", json={
                "alias": alias, "name": data.name, "type": "position", "state": "disabled",
                "domain_id": data.domain_id, "group_id": str(data.group_id),
                "traffic_source_id": data.traffic_source_id,
            })
            operation.campaign_id = campaign["id"]
            db.commit()

        campaign_id = operation.campaign_id
        owned_campaign(api, campaign_id)
        existing_flows = flows(api, campaign_id)
        for payload in desired_flows(data, campaign_id):
            if not any(row["name"] == payload["name"] and row["position"] == payload["position"]
                       for row in existing_flows):
                api.call("POST", "streams", json=payload)
        api.call("PUT", f"campaigns/{campaign_id}", json={"state": "active"})
        operation.state = "completed"
        db.commit()
        return {"id": campaign_id, "name": data.name}


def offer_rows(flow):
    return flow.get("offers") or []


def version(flow):
    raw = json.dumps(offer_rows(flow), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def flow_view(flow):
    return {
        "id": flow["id"], "name": flow["name"], "type": flow["type"],
        "position": flow["position"], "editable": flow["schema"] == "landings",
        "offers": [{key: row.get(key) for key in ("offer_id", "state", "share")}
                   for row in offer_rows(flow)],
        "version": version(flow),
    }


@app.get("/api/campaigns/{campaign_id}")
def campaign_details(campaign_id: int, api: API):
    campaign = owned_campaign(api, campaign_id)
    return {"id": campaign["id"], "name": campaign["name"], "state": campaign.get("state"),
            "flows": [flow_view(flow) for flow in sorted(flows(api, campaign_id),
                                                        key=lambda item: item["position"])]}


def change_offer(api, campaign_id, flow_id, offer_id, expected_version, add):
    with WRITE_LOCK:
        owned_campaign(api, campaign_id)
        flow = by_id(flows(api, campaign_id), flow_id)
        if not flow or flow["schema"] != "landings":
            raise HTTPException(404, "Поток с офферами не найден.")
        if expected_version != version(flow):
            raise HTTPException(409, "Состав офферов изменился. Обновите кампанию.")
        rows = offer_rows(flow)
        exists = any(row["offer_id"] == offer_id for row in rows)
        if exists == add:
            raise HTTPException(409, "Оффер уже добавлен." if add else "Оффер уже удалён.")
        if add and not by_id(active_items(api, "offers"), offer_id):
            raise HTTPException(422, "Оффер не найден в Keitaro.")
        offers = [{key: row[key] for key in ("id", "offer_id", "state", "share") if key in row}
                  for row in rows if row["offer_id"] != offer_id]
        if add:
            offers.append({"offer_id": offer_id, "state": "active", "share": 0})
        active = sorted((row for row in offers if row["state"] == "active"),
                        key=lambda row: row["offer_id"])
        share, remainder = divmod(100, len(active)) if active else (0, 0)
        for index, row in enumerate(active):
            row["share"] = share + (index < remainder)
        if add and share == 0:
            raise HTTPException(422, "Поток поддерживает до 100 активных офферов по 1% и выше.")
        api.call("PUT", f"streams/{flow_id}", json={"offers": offers})
        updated = by_id(flows(api, campaign_id), flow_id)
        if updated is None:
            raise HTTPException(502, "Keitaro не вернул обновлённый поток.")
        return flow_view(updated)


@app.post("/api/campaigns/{campaign_id}/flows/{flow_id}/offers")
def add_offer(campaign_id: int, flow_id: int, data: OfferChange, api: API):
    return change_offer(api, campaign_id, flow_id, data.offer_id, data.version, True)


@app.delete("/api/campaigns/{campaign_id}/flows/{flow_id}/offers/{offer_id}")
def remove_offer(campaign_id: int, flow_id: int, offer_id: int, version: str, api: API):
    return change_offer(api, campaign_id, flow_id, offer_id, version, False)


@app.get("/api/campaigns/{campaign_id}/report")
def report(campaign_id: int, start: date, end: date, api: API):
    owned_campaign(api, campaign_id)
    if start > end:
        raise HTTPException(422, "Дата начала должна быть раньше даты окончания или совпадать с ней.")
    payload = {
        "range": {"from": str(start), "to": str(end), "timezone": settings().key},
        "grouping": ["stream_id", "offer_id"],
        "metrics": ["clicks", "conversions", "revenue", "cost", "profit", "roi"],
        "filters": [{"name": "campaign_id", "operator": "EQUALS", "expression": campaign_id}],
        "sort": [{"name": "stream_id", "order": "ASC"}, {"name": "offer_id", "order": "ASC"}],
        "limit": 1000,
    }
    rows = []
    while True:
        result = api.call("POST", "report/build", json={**payload, "offset": len(rows)})
        if not isinstance(result, dict) or not isinstance(result.get("rows"), list):
            raise HTTPException(502, "Keitaro вернул неожиданный формат отчёта.")
        page = result["rows"]
        if any("stream_id" not in row or "offer_id" not in row for row in page):
            raise HTTPException(502, "В отчёте Keitaro отсутствуют ID потоков или офферов.")
        rows.extend(page)
        total = result.get("total")
        if not page or (isinstance(total, int) and len(rows) >= total) or len(page) < payload["limit"]:
            return {"rows": rows}



app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="web")
