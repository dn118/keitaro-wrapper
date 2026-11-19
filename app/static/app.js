"use strict";

const byId = id => document.getElementById(id);
const selector = byId("selection");
const creator = byId("creator");
const campaign = byId("campaign");
const workspace = byId("workspace");
const notice = byId("notice");
let catalogs;
let currentLoad = 0;

function element(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}

function message(text, error = false) {
  notice.textContent = text;
  notice.hidden = !text;
  notice.className = error ? "error" : "";
}

async function request(path, method = "GET", body) {
  const response = await fetch("/api" + path, {
    method,
    headers: body ? {"Content-Type": "application/json"} : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(item => item.msg).join(" ") : data.detail;
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return data;
}

function activate(panel) {
  for (const button of document.querySelectorAll("[data-panel]")) {
    const selected = button.dataset.panel === panel;
    byId(button.dataset.panel).hidden = !selected;
    button.setAttribute("aria-pressed", selected);
  }
}

function options(select, items, placeholder) {
  select.replaceChildren(new Option(placeholder, ""));
  for (const row of items) select.add(new Option(`${row.name} · #${row.id}`, row.id));
}

async function refreshCatalogs() {
  catalogs = await request("/catalogs");
  for (const [key, input] of Object.entries({
    domains: "domain_id", groups: "group_id", sources: "traffic_source_id", offers: "offer_id",
  })) {
    const field = creator.elements[input];
    const previous = field.value;
    options(field, catalogs[key], "Выберите из Keitaro");
    if ([...field.options].some(option => option.value === previous)) field.value = previous;
  }
}

async function refreshCampaigns(selected) {
  const list = await request("/campaigns");
  options(campaign, list, list.length ? "Выберите кампанию" : "Тестовых кампаний пока нет");
  if (selected) campaign.value = selected;
}

function number(value) {
  return value == null || value === "" ? "—" : new Intl.NumberFormat("ru-RU", {
    maximumFractionDigits: 2,
  }).format(value);
}

const metrics = [
  ["clicks", "Клики"], ["conversions", "Конверсии"], ["revenue", "Доход"],
  ["cost", "Расход"], ["profit", "Прибыль"], ["roi", "ROI, %"],
];

function table(headers) {
  const wrapper = element("div", undefined, "table-wrap");
  const grid = element("table");
  const head = grid.createTHead().insertRow();
  for (const [title, numeric] of headers) head.append(element("th", title, numeric ? "num" : ""));
  wrapper.append(grid);
  return [wrapper, grid.createTBody()];
}

async function updateOffer(campaignId, flow, offerId, add, control) {
  control.disabled = true;
  message("");
  try {
    const path = `/campaigns/${campaignId}/flows/${flow.id}/offers`;
    if (add) await request(path, "POST", {offer_id: Number(offerId), version: flow.version});
    else await request(`${path}/${offerId}?version=${encodeURIComponent(flow.version)}`, "DELETE");
    if (String(campaignId) === campaign.value) await loadCampaign();
  } catch (error) {
    message(error.message, true);
  } finally {
    control.disabled = false;
  }
}

function renderFlow(campaignId, flow, report) {
  const card = element("article", undefined, "card");
  card.append(element("h3", flow.name), element("p", `Поток #${flow.id} · позиция ${flow.position}`, "muted"));
  if (!flow.editable) return card;
  const [wrapper, body] = table([
    ["Оффер"], ["Доля, %", true], ...metrics.map(([, label]) => [label, true]), [""],
  ]);
  const stats = new Map((report?.rows || [])
    .filter(row => String(row.stream_id) === String(flow.id))
    .map(row => [String(row.offer_id), row]));
  const offerNames = new Map(catalogs.offers.map(row => [String(row.id), row.name]));
  for (const offer of flow.offers) {
    const offerId = String(offer.offer_id);
    const row = body.insertRow();
    const name = offerNames.get(offerId) || "Оффер";
    row.insertCell().textContent = `${name} · #${offerId}${offer.state === "active" ? "" : " · отключён"}`;
    row.append(element("td", number(offer.share), "num"));
    const values = stats.get(offerId);
    for (const [key] of metrics) {
      const value = values ? values[key] : (report && key !== "roi" ? 0 : null);
      row.append(element("td", number(value), "num"));
    }
    const remove = element("button", "Удалить", "danger");
    remove.type = "button";
    remove.setAttribute("aria-label", `Удалить ${name}`);
    remove.addEventListener("click", () => updateOffer(campaignId, flow, offerId, false, remove));
    row.insertCell().append(remove);
  }
  const form = element("form", undefined, "inline");
  const select = document.createElement("select");
  select.required = true;
  select.setAttribute("aria-label", "Оффер для добавления");
  const existing = new Set(flow.offers.map(offer => String(offer.offer_id)));
  const available = catalogs.offers.filter(row => !existing.has(String(row.id)));
  options(select, available, available.length ? "Выберите оффер" : "Все офферы уже добавлены");
  const add = element("button", "Добавить оффер", "primary");
  add.disabled = !available.length;
  form.append(select, add);
  form.addEventListener("submit", event => {
    event.preventDefault();
    updateOffer(campaignId, flow, select.value, true, add);
  });
  card.append(wrapper, form);
  return card;
}

async function loadCampaign() {
  const load = ++currentLoad;
  const id = campaign.value;
  message("");
  workspace.replaceChildren();
  if (!id || !catalogs || !selector.reportValidity()) return;
  workspace.append(element("p", "Загрузка кампании…", "muted"));
  const query = new URLSearchParams({start: selector.elements.start.value, end: selector.elements.end.value});
  const [details, report] = await Promise.allSettled([
    request(`/campaigns/${id}`), request(`/campaigns/${id}/report?${query}`),
  ]);
  if (load !== currentLoad) return;
  workspace.replaceChildren();
  if (details.status === "rejected") return message(details.reason.message, true);
  if (report.status === "rejected") message(`Статистика недоступна. ${report.reason.message}`, true);
  const data = details.value;
  workspace.append(element("h2", data.name));
  if (!data.flows.length) workspace.append(element("p", "В кампании нет потоков.", "muted"));
  for (const flow of data.flows) workspace.append(renderFlow(id, flow, report.value));
}

for (const button of document.querySelectorAll("[data-panel]")) {
  button.addEventListener("click", () => activate(button.dataset.panel));
}

selector.addEventListener("submit", async event => {
  event.preventDefault();
  try {
    await refreshCatalogs();
    await loadCampaign();
  } catch (error) {
    message(error.message, true);
  }
});
campaign.addEventListener("change", loadCampaign);
creator.addEventListener("submit", async event => {
  event.preventDefault();
  const button = creator.querySelector("button");
  button.disabled = true;
  message("");
  try {
    const data = Object.fromEntries(new FormData(creator));
    const created = await request("/campaigns", "POST", data);
    await refreshCampaigns(created.id);
    activate("edit");
    await loadCampaign();
    if (notice.hidden) message(`Кампания #${created.id} создана.`);
  } catch (error) {
    message(error.message, true);
  } finally {
    button.disabled = false;
  }
});

async function init() {
  try {
    const config = await request("/settings");
    selector.elements.start.value = config.start;
    selector.elements.end.value = config.end;
    creator.elements.name.value = config.prefix;
    creator.elements.name.placeholder = config.prefix + "campaign1";
    await Promise.all([refreshCatalogs(), refreshCampaigns()]);
  } catch (error) {
    message(error.message, true);
  }
}
init();
