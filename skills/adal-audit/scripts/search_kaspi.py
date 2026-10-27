#!/usr/bin/env python3
"""Агент 2 — Kaspi Market Checker: рыночные цены Kaspi Магазина по лотам.

Для каждого запроса:
  1. поиск карточки товара — публичный endpoint витрины /yml/product-view/pl/filters;
  2. полный список продавцов — POST /yml/offer-view/offers/{product_id}
     → min / median / max / sellers_count.
Только стандартная библиотека Python.

Живые результаты пишутся в data/kaspi_cache.json; режим --demo (или ошибка сети)
читает этот кэш — то есть отдаёт данные, ранее реально снятые с Kaspi.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.parse
import urllib.request
import statistics
import sys
import time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
CACHE = SKILL_DIR / "data" / "kaspi_cache.json"

DELIVERY_LABELS = {"TODAY": "Сегодня", "TOMORROW": "Завтра", "TILL_2_DAYS": "До 2 дней",
                   "TILL_5_DAYS": "До 5 дней", "TILL_7_DAYS": "До 7 дней"}


def _load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-zа-яё0-9]+", text.lower())
    return {w for w in words if len(w) >= 2 and w not in STOP}


STOP = {"для", "или", "не", "менее", "более", "гб", "шт", "с", "и", "в", "на", "эквивалент"}


def relevance(query: str, title: str) -> float:
    """Доля значимых слов запроса, найденных в названии карточки (защита от «кулера вместо сервера»).
    Любой токен с цифрами (модель, артикул, ёмкость) обязан совпасть, иначе 0:
    StarLine S97 ≠ B97, маска 6200 ≠ 6800."""
    q, t = _tokens(query), _tokens(title)
    if not q:
        return 0.0
    t_compact = re.sub(r"[^a-zа-яё0-9]", "", title.lower())
    if any(re.search(r"\d", w) and w not in t and w not in t_compact for w in q):
        return 0.0
    head = re.match(r"[а-яё]{4,}", query.lower())  # вид товара: «блок» ≠ «проектор»
    if head and head.group(0)[:5] not in title.lower():
        return 0.0
    return len(q & t) / len(q)


BASE = "https://kaspi.kz"
CITY = os.environ.get("KASPI_CITY", "710000000")  # Алматы; цены по стране близки
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")


def _kaspi(path: str, referer: str, body: dict | None = None) -> dict:
    headers = {"User-Agent": UA, "Accept": "application/json, text/*", "Referer": referer,
               "Accept-Language": "ru-RU,ru;q=0.9", "X-Requested-With": "XMLHttpRequest",
               "Cookie": f"kaspi.storefront.cookie.city={CITY}"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers.update({"Content-Type": "application/json", "Origin": BASE, "X-KS-City": CITY})
    req = urllib.request.Request(BASE + path, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def kaspi_search(text: str) -> list[dict]:
    q = urllib.parse.urlencode({"text": text, "page": 0, "c": CITY, "ui": "d", "sort": "relevance"})
    cards = (_kaspi(f"/yml/product-view/pl/filters?{q}", f"{BASE}/shop/search/").get("data") or {}).get("cards") or []
    return [{"id": c.get("id"), "title": c.get("title"), "price": c.get("unitSalePrice") or c.get("unitPrice"),
             "rating": c.get("rating"), "reviews": c.get("reviewsQuantity"), "delivery": c.get("deliveryDuration"),
             "category": c.get("category") or []} for c in cards]


def kaspi_offers(product_id: str) -> dict:
    link = f"{BASE}/shop/p/-{product_id}/?c={CITY}"
    data = _kaspi(f"/yml/offer-view/offers/{product_id}", link,
                  {"cityId": CITY, "id": str(product_id), "merchantUID": "", "limit": 50, "page": 0,
                   "sort": True, "installationId": "-1"})
    prices = [o["price"] for o in data.get("offers", []) if o.get("price")]
    return {"sellers_count": data.get("offersCount") or len(prices), "prices": prices, "link": link}


def check_live(query: str) -> dict | None:
    found = kaspi_search(query)[:8]
    # категория Kaspi помогает: у аккумуляторов в названии нет слова «аккумулятор»
    found = [p for p in found if p.get("price") and relevance(
        query, f"{p.get('title') or ''} {' '.join(p.get('category') or [])}") >= 0.5]
    if not found:
        return None
    card = found[0]
    offers = kaspi_offers(card["id"])
    prices = offers["prices"] or [card["price"]]
    return {
        "query": query,
        "product_id": card["id"],
        "title": card.get("title"),
        "min_price": min(prices),
        "median_price": statistics.median(prices),
        "max_price": max(prices),
        "p25": statistics.quantiles(prices, n=4)[0] if len(prices) >= 2 else prices[0],
        "p75": statistics.quantiles(prices, n=4)[2] if len(prices) >= 2 else prices[0],
        "sellers_count": offers.get("sellers_count") or len(prices),
        "rating": card.get("rating"),
        "reviews_count": card.get("reviews"),
        "kaspi_url": offers["link"],
        "delivery_label": DELIVERY_LABELS.get(card.get("delivery"), card.get("delivery")),
        "source": "live",
        "fetched_at": time.strftime("%Y-%m-%d %H:%M"),
    }


def check_many(queries: list[str], demo: bool = False, delay: float = 0.8,
               budget: int | None = None) -> dict[str, dict | None]:
    """Уникальные запросы в порядке приоритета. В live-режиме не больше budget живых
    обращений к Kaspi (свежий кэш < 7 дней не перезапрашивается); остальное — из кэша."""
    cache = _load_cache()
    out: dict[str, dict | None] = {}
    live_calls = 0
    fresh = time.strftime("%Y-%m-%d", time.localtime(time.time() - 7 * 86400))
    for q in dict.fromkeys(queries):
        cached = cache.get(q)
        is_fresh = bool(cached and cached.get("fetched_at", "") >= fresh)
        res = None
        if not demo and not is_fresh and (budget is None or live_calls < budget):
            live_calls += 1
            try:
                res = check_live(q)
                cache[q] = res or {"query": q, "not_found": True, "fetched_at": time.strftime("%Y-%m-%d %H:%M")}
                time.sleep(delay)
            except Exception as e:
                print(f"[search_kaspi] live ошибка для «{q}»: {e}; беру кэш", file=sys.stderr)
        if res is None and cached and not cached.get("not_found"):
            res = dict(cached, source="cache")
        out[q] = res
    if not demo:
        _save_cache(cache)
    print(f"[search_kaspi] уникальных запросов: {len(out)}, живых обращений: {live_calls}", file=sys.stderr)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--query", action="append", required=True)
    ap.add_argument("--demo", action="store_true", help="только кэш, без сети")
    a = ap.parse_args()
    print(json.dumps(check_many(a.query, a.demo), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
