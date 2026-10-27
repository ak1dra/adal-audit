#!/usr/bin/env python3
"""Агент 1 — Goszakup Auditor: получение лотов госзакупок РК.

Режимы:
  --demo [--demo-size N] демо-набор (10 эталонных лотов, размножаемых по городам до N)
  public (по умолчанию)  публичный поиск лотов goszakup.gov.kz, без токена (до 2000 лотов/страница)
  api (GOSZAKUP_API_TOKEN) — выгрузка лотов OWS v3 GraphQL с пагинацией

Фильтры (live и demo): --region, --from/--to (дата обновления), --keyword,
--min-amount, --bins (БИН через запятую или файл), --limit (по умолчанию 1000).

Вывод: JSON-массив нормализованных лотов в stdout или в --output.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request

OWS_GRAPHQL = os.environ.get("GOSZAKUP_OWS_URL", "https://ows.goszakup.gov.kz") + "/v3/graphql"

# Запрос лотов OWS v3 GraphQL (схема: https://ows.goszakup.gov.kz/help/v3/schema/)
LOTS_QUERY = """
query($limit:Int,$after:Int,$filter:LotsFiltersInput!){
  Lots(limit:$limit, after:$after, filter:$filter){
    id lotNumber refTradeMethodsId customerBin customerNameRu lastUpdateDate
    trdBuyId trdBuyNumberAnno count amount nameRu descriptionRu
  }
}
"""

# refTradeMethodsId → название способа закупки (основные)
TRADE_METHODS = {
    1: "Открытый конкурс", 2: "Запрос ценовых предложений", 3: "Из одного источника",
    6: "Из одного источника (по итогам)", 7: "Электронный магазин", 32: "Аукцион",
}

DEMO_LOTS = [
    {"lot_id": "GZ-2026-0001", "customer": "КГУ «Школа-гимназия №12» г. Астана", "customer_bin": "010140003951",
     "enstru": "262011.000.000003", "name": "Ноутбук Lenovo IdeaPad 3 15IAU7 (Intel Core i5, 8 ГБ, 512 ГБ SSD)",
     "kaspi_query": "Lenovo IdeaPad 3 15IAU7 i5 8 512", "method": "Из одного источника", "qty": 25,
     "unit_price": 489000, "delivery_days": 3, "bidders": 1, "brand_lock": True, "prev_year_qty": 10,
     "spec": "Ноутбук Lenovo IdeaPad 3 15IAU7, строго указанная модель."},
    {"lot_id": "GZ-2026-0002", "customer": "ГУ «Управление образования Карагандинской области»", "customer_bin": "990240001234",
     "enstru": "262016.300.000001", "name": "МФУ лазерное HP LaserJet Pro MFP M428fdn",
     "kaspi_query": "HP LaserJet Pro MFP M428fdn", "method": "Запрос ценовых предложений", "qty": 12,
     "unit_price": 545000, "delivery_days": 10, "bidders": 3, "brand_lock": True, "prev_year_qty": 12,
     "spec": "МФУ HP LaserJet Pro M428fdn или эквивалент отсутствует."},
    {"lot_id": "GZ-2026-0003", "customer": "РГУ «Департамент госдоходов по г. Алматы»", "customer_bin": "980540002211",
     "enstru": "268012.900.000010", "name": "Интерактивная панель 75\" 4K, Android, сенсор 20 касаний",
     "kaspi_query": "интерактивная панель 75", "method": "Открытый конкурс", "qty": 6,
     "unit_price": 2450000, "delivery_days": 20, "bidders": 4, "brand_lock": False, "prev_year_qty": 5,
     "spec": "Интерактивная панель 75 дюймов или эквивалент, 4K UHD."},
    {"lot_id": "GZ-2026-0004", "customer": "АО «Национальный информационный центр»", "customer_bin": "050140004567",
     "enstru": "262014.000.000002", "name": "Сервер Dell PowerEdge R750 (2x Xeon Silver, 128 ГБ RAM)",
     "kaspi_query": "сервер Dell PowerEdge", "method": "Из одного источника", "qty": 2,
     "unit_price": 9800000, "delivery_days": 2, "bidders": 1, "brand_lock": True, "prev_year_qty": 1,
     "spec": "Сервер Dell PowerEdge R750, без эквивалента, поставка 2 дня."},
    {"lot_id": "GZ-2026-0005", "customer": "КГУ «Средняя школа №45» г. Шымкент", "customer_bin": "070840005432",
     "enstru": "310112.500.000001", "name": "Кресло офисное эргономичное с подлокотниками",
     "kaspi_query": "кресло офисное эргономичное", "method": "Запрос ценовых предложений", "qty": 40,
     "unit_price": 62000, "delivery_days": 15, "bidders": 5, "brand_lock": False, "prev_year_qty": 35,
     "spec": "Кресло офисное, сетчатая спинка, газлифт, или эквивалент."},
    {"lot_id": "GZ-2026-0006", "customer": "ГКП «Городская поликлиника №3» г. Павлодар", "customer_bin": "120340001111",
     "enstru": "262017.000.000005", "name": "Монитор Samsung 27\" IPS Full HD",
     "kaspi_query": "монитор Samsung 27 IPS", "method": "Запрос ценовых предложений", "qty": 30,
     "unit_price": 118000, "delivery_days": 12, "bidders": 2, "brand_lock": True, "prev_year_qty": 8,
     "spec": "Монитор Samsung 27 дюймов."},
    {"lot_id": "GZ-2026-0007", "customer": "ГУ «Аппарат акима Абайского района»", "customer_bin": "000940002222",
     "enstru": "262011.000.000001", "name": "Ноутбук 15.6\" Intel Core i3 / 8 ГБ / SSD 256 ГБ",
     "kaspi_query": "ноутбук core i3 8 гб 256 ssd", "method": "Открытый конкурс", "qty": 15,
     "unit_price": 255000, "delivery_days": 25, "bidders": 6, "brand_lock": False, "prev_year_qty": 14,
     "spec": "Ноутбук с характеристиками не хуже указанных, или эквивалент."},
    {"lot_id": "GZ-2026-0008", "customer": "РГП «Казгидромет»", "customer_bin": "990340003333",
     "enstru": "263023.300.000001", "name": "Смартфон Apple iPhone 15 128 ГБ",
     "kaspi_query": "Apple iPhone 15 128Gb", "method": "Из одного источника", "qty": 5,
     "unit_price": 780000, "delivery_days": 3, "bidders": 1, "brand_lock": True, "prev_year_qty": 2,
     "spec": "Apple iPhone 15 128 ГБ, только оригинал указанной модели."},
    {"lot_id": "GZ-2026-0009", "customer": "КГУ «Детский сад №7» г. Костанай", "customer_bin": "150240004444",
     "enstru": "275111.300.000002", "name": "Холодильник двухкамерный, объём не менее 300 л",
     "kaspi_query": "холодильник двухкамерный 300 л", "method": "Запрос ценовых предложений", "qty": 3,
     "unit_price": 205000, "delivery_days": 20, "bidders": 4, "brand_lock": False, "prev_year_qty": 3,
     "spec": "Холодильник двухкамерный, No Frost, или эквивалент."},
    {"lot_id": "GZ-2026-0010", "customer": "ГУ «Управление цифровизации г. Актобе»", "customer_bin": "180540005555",
     "enstru": "263012.000.000001", "name": "Wi-Fi роутер TP-Link Archer AX55",
     "kaspi_query": "TP-Link Archer AX55", "method": "Открытый конкурс", "qty": 50,
     "unit_price": 46000, "delivery_days": 14, "bidders": 3, "brand_lock": True, "prev_year_qty": 20,
     "spec": "Роутер TP-Link Archer AX55."},
]


def _normalize_query(name: str) -> str:
    q = re.sub(r"[«»\"()]", " ", name)
    q = re.sub(r"\s+", " ", q)
    return q.strip()[:60]


# Регионы РК: подстроки в названии заказчика / месте поставки (рус., нижний регистр)
REGIONS = {
    "Астана": ["астан", "нур-султан"], "Алматы": ["алматы", "г. алмат"], "Шымкент": ["шымкент"],
    "Абайская обл.": ["абайск", "семей"], "Акмолинская обл.": ["акмолин", "кокшет"],
    "Актюбинская обл.": ["актюбин", "актобе"], "Алматинская обл.": ["алматинск", "конаев", "қонаев"],
    "Атырауская обл.": ["атырау"], "ВКО": ["восточно-казахстан", "усть-каменогор", "өскемен"],
    "Жамбылская обл.": ["жамбыл", "тараз"], "Жетысуская обл.": ["жетісу", "жетысу", "талдыкорган"],
    "ЗКО": ["западно-казахстан", "уральск"], "Карагандинская обл.": ["карагандин", "караганд"],
    "Костанайская обл.": ["костанай"], "Кызылординская обл.": ["кызылорд"],
    "Мангистауская обл.": ["мангист", "актау"], "Павлодарская обл.": ["павлодар", "экибастуз"],
    "СКО": ["северо-казахстан", "петропавл"], "Туркестанская обл.": ["туркестан"],
    "Улытауская обл.": ["улытау", "жезказган"],
}

DEMO_CITIES = [("г. Астана", "Астана"), ("г. Алматы", "Алматы"), ("г. Шымкент", "Шымкент"),
               ("Карагандинской области", "Карагандинская обл."), ("г. Павлодар", "Павлодарская обл."),
               ("г. Актобе", "Актюбинская обл."), ("г. Костанай", "Костанайская обл."),
               ("Туркестанской области", "Туркестанская обл."), ("г. Атырау", "Атырауская обл."),
               ("г. Усть-Каменогорск", "ВКО")]


def detect_region(text: str) -> str:
    t = (text or "").lower()
    for region, keys in REGIONS.items():
        if any(k in t for k in keys):
            return region
    return "Не определён"


def lot_url(trd_buy_id) -> str | None:
    return f"https://goszakup.gov.kz/ru/announce/index/{trd_buy_id}?tab=lots" if trd_buy_id else None


def _graphql(token: str, variables: dict) -> dict:
    body = json.dumps({"query": LOTS_QUERY, "variables": variables}).encode()
    req = urllib.request.Request(OWS_GRAPHQL, data=body, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as e:  # 429/5xx/таймаут — экспоненциальная пауза
            if attempt == 3:
                raise
            print(f"[fetch_goszakup] повтор {attempt + 1}: {e}", file=sys.stderr)
            time.sleep(2 ** attempt)
    return {}


def _normalize_live(x: dict) -> dict:
    qty = float(x.get("count") or 1) or 1
    text = f"{x.get('nameRu') or ''} {x.get('descriptionRu') or ''}"
    method = TRADE_METHODS.get(x.get("refTradeMethodsId"), str(x.get("refTradeMethodsId")))
    return {
        "lot_id": x.get("lotNumber") or x.get("id"), "customer": x.get("customerNameRu"),
        "customer_bin": x.get("customerBin"), "region": detect_region(x.get("customerNameRu") or ""),
        "enstru": "", "name": x.get("nameRu"), "kaspi_query": _normalize_query(x.get("nameRu") or ""),
        "method": method, "qty": qty, "unit_price": float(x.get("amount") or 0) / qty,
        "delivery_days": None, "bidders": 1 if "одного источника" in method else None,
        "brand_lock": "эквивалент" not in text.lower() and bool(re.search(r"[A-Z][a-z]+ [A-Z0-9]", text)),
        "prev_year_qty": None, "spec": x.get("descriptionRu") or "",
        "updated": x.get("lastUpdateDate"), "trd_buy_id": x.get("trdBuyId"),
        "lot_url": lot_url(x.get("trdBuyId")), "is_demo": False,
    }


def fetch_live(bins: list[str] | None, date_from: str | None, date_to: str | None, limit: int):
    """Генератор лотов OWS v3 с пагинацией по курсору after=lastId."""
    token = os.environ.get("GOSZAKUP_API_TOKEN")
    if not token:
        raise RuntimeError("GOSZAKUP_API_TOKEN не задан. Получите токен на goszakup.gov.kz "
                           "(«Открытые данные / API») или запустите с --demo")
    base: dict = {}
    if date_from or date_to:
        base["lastUpdateDate"] = [v for v in (date_from, date_to) if v]
    filters = [dict(base, customerBin=b) for b in bins] if bins else [base]
    got = 0
    for flt in filters:
        after = 0
        while got < limit:
            page = min(200, limit - got)
            data = _graphql(token, {"limit": page, "after": after, "filter": flt})
            if data.get("errors"):
                raise RuntimeError(f"OWS GraphQL: {data['errors']}")
            items = (data.get("data") or {}).get("Lots") or []
            if not items:
                break
            for x in items:
                got += 1
                yield _normalize_live(x)
            info = (data.get("extensions") or {}).get("pageInfo") or {}
            nxt = info.get("lastId") or max(int(i["id"]) for i in items)
            if nxt == after or (not info.get("hasNextPage") and len(items) < page):
                break
            after = nxt
            time.sleep(0.3)


PUBLIC_BASE = "https://old.goszakup.gov.kz"
MODEL_RE = re.compile(r"\b(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9-]{3,}\b")


def _get(url: str, params: dict | None = None, timeout: int = 90) -> str:
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "ru-RU"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:
            if attempt == 3:
                raise
            print(f"[fetch_goszakup] повтор {attempt + 1}: {e}", file=sys.stderr)
            time.sleep(2 ** attempt)
    return ""


def kato_lookup(region: str) -> tuple[str, str] | None:
    """Название города/области → код КАТО через автодополнение портала."""
    data = json.loads(_get(f"{PUBLIC_BASE}/ru/search/getKato", {"name": region}) or "{}")
    items = data.get("items") or []
    # предпочитаем верхний уровень: «Карагандинская область», а не её районы
    items.sort(key=lambda x: (x["text"].count(","), len(x["text"])))
    return (items[0]["id"], items[0]["text"]) if items else None


def _txt(fragment: str) -> str:
    import html as _html
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


BRAND_RE = re.compile(r"\b[A-Z][A-Za-z]{2,}\b")
NOT_BRANDS = {"GPS", "LTE", "USB", "LED", "LCD", "IPS", "SSD", "HDD", "RAM", "CAN", "LIN", "FFP", "ГОСТ"}


def _model_tokens(text: str) -> list[str]:
    t = re.sub(r"(ГОСТ|СТ РК|ISO)\s*[\d.\-]+", " ", text)
    models = MODEL_RE.findall(t)
    if not models and not brands_in(t):
        return []  # одиночные числа («знак 470», «бумага 500») — не модель
    nums = [n for n in re.findall(r"(?<![\d.,])\b\d{3,5}\b(?![\d.,])", t) if not re.fullmatch(r"(19|20)\d\d", n)]
    return models + nums


def brands_in(text: str) -> list[str]:
    return [b for b in BRAND_RE.findall(text) if b.upper() not in NOT_BRANDS and not MODEL_RE.fullmatch(b)]


def kaspi_query_for(name: str, text: str) -> str | None:
    """Запрос в Kaspi только если указана модель/артикул: «товар + бренд + модель».
    Для общих названий («Ноутбук») Kaspi даст случайную карточку — такие лоты
    сверяются с ценами других заказчиков."""
    models = _model_tokens(text)
    if not models:
        return None
    word = (re.findall(r"[А-Яа-яЁё]{4,}", name) or [""])[0].lower()
    return " ".join(dict.fromkeys([word] + brands_in(text)[:2] + models[:2])).strip()[:70]


def fetch_public(keyword, kato, date_from, date_to, min_amount, limit, trade_type="g"):
    """Публичный реестр лотов (HTML). Сумма на портале — без НДС."""
    if date_to:  # портал сравнивает с полуночью: «по 27-е» без +1 день теряет лоты 27-го
        from datetime import date, timedelta
        date_to = (date.fromisoformat(date_to[:10]) + timedelta(days=1)).isoformat()
    got, page = 0, 1
    # портал принимает только фиксированные размеры страницы, иначе молча отдаёт 30 строк
    per = next((n for n in (50, 100, 500, 2000) if n >= limit), 2000)
    while got < limit:
        params = {"filter[name]": keyword or "", "filter[kato]": kato or "",
                  "filter[trade_type]": trade_type, "filter[amount_from]": int(min_amount) or "",
                  "filter[start_date_from]": date_from or "", "filter[start_date_to]": date_to or "",
                  "count_record": per, "page": page}
        html_ = _get(f"{PUBLIC_BASE}/ru/search/lots", {k: v for k, v in params.items() if v != ""})
        if 'id="search-result"' not in html_:
            break
        t = html_[html_.index('id="search-result"'):]
        t = t[:t.index("</table>")]
        rows = re.findall(r"<tr.*?</tr>", t, re.S)[1:]
        if not rows:
            break
        for r in rows:
            # первая ячейка на портале не закрыта </td> — режем по открывающим тегам
            tds = [re.sub(r"</td>\s*$", "", x.split(">", 1)[1].strip()) for x in re.split(r"<td", r)[1:]]
            if len(tds) < 7:
                continue
            m = re.search(r"/ru/announce/index/(\d+)", r)
            anno = _txt(re.sub(r"<small.*", "", tds[1], flags=re.S))
            customer = _txt(tds[1].split("Заказчик:</b>")[-1]) if "Заказчик:" in tds[1] else ""
            name = _txt(re.sub(r"<a[^>]*btn-select-history.*?</a>", "", tds[2], flags=re.S))
            qty = float(_txt(tds[3]).replace(" ", "").replace(",", ".") or 1) or 1
            amount = float(_txt(tds[4]).replace(" ", "").replace(",", ".") or 0)
            method = _txt(tds[5])
            text = f"{anno} {name}"
            got += 1
            yield {
                "lot_id": _txt(tds[0]), "customer": customer, "customer_bin": customer,
                "region": detect_region(customer), "enstru": "", "name": name, "announce": anno,
                "kaspi_query": kaspi_query_for(name, text), "peer_key": re.sub(r"[^а-яёa-z0-9 ]", "", name.lower())[:40],
                "method": method, "status": _txt(tds[6]), "qty": qty,
                "unit_price_net": amount / qty, "unit_price": amount / qty * (1 + VAT_RATE),
                "delivery_days": None, "bidders": 1 if "одного источника" in method.lower() else None,
                # по названию лота; полный текст ТЗ на портале — проверяет аудитор
                "brand_lock": bool(brands_in(text)) and bool(MODEL_RE.search(text))
                and "эквивалент" not in text.lower(),
                "prev_year_qty": None, "spec": anno,
                "trd_buy_id": m.group(1) if m else None,
                "lot_url": PUBLIC_BASE + m.group(0) if m else None, "is_demo": False,
            }
            if got >= limit:
                return
        if len(rows) < per:
            break
        page += 1
        time.sleep(1)


VAT_RATE = float(os.environ.get("KZ_VAT", "0.16"))  # НДС РК с 01.01.2026 — 16%


def demo_lots(size: int) -> list[dict]:
    """Эталонные лоты, размноженные по городам с разбросом цены (помечены is_demo)."""
    rnd = random.Random(42)
    out = []
    for i in range(max(size, len(DEMO_LOTS))):
        base = dict(DEMO_LOTS[i % len(DEMO_LOTS)])
        city, region = DEMO_CITIES[(i // len(DEMO_LOTS)) % len(DEMO_CITIES)]
        if i >= len(DEMO_LOTS):  # копии: другой город и цена ±35%
            base["lot_id"] = f"{base['lot_id']}-{i // len(DEMO_LOTS):03d}"
            base["customer"] = re.sub(r"(г\. \S+|\S+ской области)$", city, base["customer"])
            if city not in base["customer"]:
                base["customer"] = f"{base['customer'].split(' г.')[0]} {city}"
            base["unit_price"] = round(base["unit_price"] * rnd.uniform(0.75, 1.35), -2)
            base["delivery_days"] = rnd.choice([2, 3, 7, 10, 15, 20, 30])
            base["method"] = rnd.choices(["Открытый конкурс", "Запрос ценовых предложений",
                                          "Аукцион", "Из одного источника"], [35, 35, 15, 15])[0]
            base["bidders"] = 1 if base["method"] == "Из одного источника" else rnd.choice([1, 2, 3, 4, 6, 8])
            base["brand_lock"] = rnd.random() < 0.3
            base["prev_year_qty"] = base["qty"] if rnd.random() < 0.8 else max(1, base["qty"] // 3)
            base["customer_bin"] = base["customer_bin"][:-3] + f"{i // len(DEMO_LOTS):03d}"
        base["region"] = detect_region(base["customer"])
        base["trd_buy_id"] = None
        base["lot_url"] = "https://goszakup.gov.kz/ru/search/lots?filter%5Bname%5D=" + \
            urllib.parse.quote(base["name"][:40])
        base["is_demo"] = True
        out.append(base)
    return out[:size] if size else out


def get_lots(demo: bool, bins: list[str] | None = None, keyword: str | None = None,
             region: str | None = None, date_from: str | None = None, date_to: str | None = None,
             min_amount: float = 0, limit: int = 1000, demo_size: int = 10,
             source: str = "auto") -> list[dict]:
    if source == "auto":
        source = "demo" if demo else ("api" if os.environ.get("GOSZAKUP_API_TOKEN") else "public")
    if source == "public":
        kato = None
        if region:
            found = kato_lookup(region)
            if not found:
                raise RuntimeError(f"Регион «{region}» не найден в справочнике КАТО портала")
            kato = found[0]
            print(f"[fetch_goszakup] место поставки: {found[1]} (КАТО {kato})", file=sys.stderr)
        lots = list(fetch_public(keyword, kato, date_from, date_to, min_amount, limit))
        if bins:
            lots = [l for l in lots if any(b in l["customer"] for b in bins)]
        if region:  # регион уже применён через КАТО; подписываем им лоты без явного города
            for l in lots:
                if l["region"] == "Не определён":
                    l["region"] = found[1]
        for l in lots:
            l["total"] = l["qty"] * l["unit_price"]
        print(f"[fetch_goszakup] публичный реестр: {len(lots)} лотов", file=sys.stderr)
        return sorted(lots, key=lambda l: -l["total"])
    if source == "demo":
        lots = demo_lots(demo_size)
        if bins:
            lots = [l for l in lots if l["customer_bin"] in bins]
    else:  # в live-режиме ошибки не подменяются демо-данными — комиссии нужны только реальные лоты
        lots = []
        for l in fetch_live(bins, date_from, date_to, limit * 3):
            lots.append(l)
            if len(lots) >= limit * 3:
                break
    if keyword:
        kw = keyword.lower()
        lots = [l for l in lots if kw in f"{l['name']} {l.get('spec', '')}".lower()]
    if region:
        rg = region.lower()
        lots = [l for l in lots if rg in l["region"].lower() or rg in (l["customer"] or "").lower()]
    for l in lots:
        l["total"] = l["qty"] * l["unit_price"]
    lots = [l for l in lots if l["total"] >= min_amount]
    lots.sort(key=lambda l: -l["total"])
    return lots[:limit]


def parse_bins(value: str | None) -> list[str] | None:
    if not value:
        return None
    if os.path.exists(value):
        value = open(value, encoding="utf-8").read()
    return [b for b in re.findall(r"\d{12}", value)] or None


def add_filter_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--demo", action="store_true", help="демо-данные")
    ap.add_argument("--source", choices=["auto", "public", "api", "demo"], default="auto",
                    help="auto: api при наличии GOSZAKUP_API_TOKEN, иначе public")
    ap.add_argument("--demo-size", type=int, default=10, help="сколько демо-лотов сгенерировать")
    ap.add_argument("--bins", help="БИН заказчиков через запятую или путь к файлу")
    ap.add_argument("--region", help="город/область, напр. «Астана», «Карагандинская»")
    ap.add_argument("--keyword", help="слово в наименовании/ТЗ, напр. «ноутбук»")
    ap.add_argument("--from", dest="date_from", help="дата обновления лота с (YYYY-MM-DD)")
    ap.add_argument("--to", dest="date_to", help="дата обновления лота по (YYYY-MM-DD)")
    ap.add_argument("--min-amount", type=float, default=0, help="мин. сумма лота, ₸")
    ap.add_argument("--limit", type=int, default=1000, help="макс. лотов к проверке")


def lots_from_args(a) -> list[dict]:
    return get_lots(a.demo, parse_bins(a.bins), a.keyword, a.region, a.date_from, a.date_to,
                    a.min_amount, a.limit, a.demo_size, a.source)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_filter_args(ap)
    ap.add_argument("--output")
    a = ap.parse_args()
    out = json.dumps(lots_from_args(a), ensure_ascii=False, indent=2)
    if a.output:
        open(a.output, "w", encoding="utf-8").write(out)
    else:
        print(out)


if __name__ == "__main__":
    main()
