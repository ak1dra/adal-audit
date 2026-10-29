#!/usr/bin/env python3
"""Движок Red Flags: скоринг опасности 0–100 и юнит-экономика поставщика.

Методология: IQR-выбросы и spike_2x из nadir2k/goszakup-ai (docs/analytics.md),
SingleBidderRule из prozorro-risks (DOZORRO), сравнение с медианой Kaspi.
"""
from __future__ import annotations

VAT = float(__import__("os").environ.get("KZ_VAT", "0.16"))  # НДС РК (16% с 2026)
LOGISTICS = 0.03      # логистика, доля от закупочной стоимости
GUARANTEE = 0.03      # обеспечение исполнения договора (3% от суммы)

COMPLEX_KEYWORDS = ("сервер", "панел", "ноутбук", "мфу", "монитор", "смартфон", "iphone")

FLAGS = {
    "PRICE_CRITICAL_OVERPAY": (50, "Цена > медианы Kaspi более чем на 50%"),
    "PRICE_IQR_OUTLIER": (25, "Цена > медианы Kaspi на 30% или выше p75 + 1.5·IQR"),
    "PRICE_MARKUP_MEDIUM": (15, "Наценка 10–30% к медиане Kaspi"),
    "SHORT_DEADLINE": (15, "Срок поставки < 5 дней на сложное оборудование"),
    "BRAND_LOCK_NO_EQUIV": (15, "Указан бренд/модель без «или эквивалент»"),
    "SINGLE_SOURCE_OR_SINGLE_BIDDER": (15, "Из одного источника или 1 участник"),
    "VOLUME_SPIKE_YOY": (10, "Объём ≥ 2× к прошлому году (spike_2x / дробление)"),
    "LOW_KASPI_COMPETITION": (10, "На Kaspi всего 1–2 продавца"),
    "PRICE_PEER_OUTLIER": (25, "Цена > медианы такого же товара у других заказчиков на 30%+"),
    "SPLIT_PURCHASE": (15, "Один заказчик — 3+ лота одного товара (возможное дробление)"),
    "WINNER_OVERPRICED": (40, "Цена победителя (фактическая) > медианы Kaspi более чем на 30%"),
    "NO_PRICE_DROP": (15, "Цена победителя ≥ 98% плановой — торги не снизили цену"),
    "PLAN_INFLATED_ONLY": (-30, "План завышен, но конкуренция снизила цену до рынка"),
    "NO_MARKET_DATA": (5, "Не найден рыночный аналог на Kaspi"),
}

LEVELS = [  # (порог, код, подпись, цвет заливки, действие)
    (75, "CRITICAL", "🔴 КРИТИЧЕСКИЙ", "FFC7CE", "Жалоба в КВФК/УГД, проверка ТЗ и обоснования цены"),
    (50, "HIGH", "🟠 ВЫСОКИЙ", "FCE4D6", "Запрос разъяснений заказчику, мониторинг итогов"),
    (25, "MEDIUM", "🟡 СРЕДНИЙ", "FFEB9C", "Ручная проверка ТЗ и рыночной цены"),
    (0, "LOW", "🟢 НИЗКИЙ", "C6EFCE", "💰 Выгодно для участия — готовить заявку"),
]


def pack_size(lot: dict, kaspi: dict | None) -> int:
    import re
    if not kaspi:
        return 1
    text = f"{lot.get('announce', '')} {lot.get('name', '')} {lot.get('spec', '')}".lower()
    m = re.search(r"(?:комплект\w*\D{0,20}?(\d{1,3})\s*(?:шт|штук))|(?:\((\d{1,3})\s*(?:шт|штук)\w*,?\s*комплект)", text)
    n = int(next(g for g in m.groups() if g)) if m else 1
    title = (kaspi.get("title") or "").lower()
    if n > 1 and not re.search(rf"\b{n}\s*шт", title):  # на Kaspi не такой же комплект
        return n
    return 1


def level_for(score: int) -> tuple:
    for lvl in LEVELS:
        if score >= lvl[0]:
            return lvl
    return LEVELS[-1]


def assess(lot: dict, kaspi: dict | None, peer: dict | None = None) -> dict:
    flags: list[str] = []
    unit = float(lot["unit_price"])
    # Защита от нерелевантной карточки (напр. «кулер» вместо сервера): медиана < 20% цены лота
    if kaspi and kaspi.get("median_price") and kaspi["median_price"] < unit * 0.2:
        kaspi = None
    median = kaspi.get("median_price") if kaspi else None
    # «комплект из 4 шт» при количестве 1: цена лота — за комплект, Kaspi — за штуку
    pack = pack_size(lot, kaspi)
    if pack > 1:
        unit = unit / pack
    dev = None
    if median:
        dev = (unit - median) / median * 100
        iqr_cap = kaspi["p75"] + 1.5 * (kaspi["p75"] - kaspi["p25"])
        if dev > 50:
            flags.append("PRICE_CRITICAL_OVERPAY")
        elif dev > 30 or unit > iqr_cap and dev > 10:
            flags.append("PRICE_IQR_OUTLIER")
        elif dev > 10:
            flags.append("PRICE_MARKUP_MEDIUM")
        if (kaspi.get("sellers_count") or 0) <= 2:
            flags.append("LOW_KASPI_COMPETITION")
    elif lot.get("kaspi_query"):
        flags.append("NO_MARKET_DATA")

    peer = peer or {}
    # для общих названий («Ноутбук») комплектация разная — нужен больший запас и выборка
    k_mult, k_n = (1.5, 5) if peer.get("generic") else (1.3, 3)
    # > 10× почти всегда другой товар или другие единицы («контейнер 20 футов» vs мусорный бак)
    if peer.get("median") and peer["n"] >= k_n and peer["median"] * k_mult < unit < peer["median"] * 10:
        flags.append("PRICE_PEER_OUTLIER")
    if peer.get("same_customer", 0) >= 3:
        flags.append("SPLIT_PURCHASE")

    # Фактическая цена из протокола итогов (если лот дозагружен): она важнее плановой
    win = lot.get("winner_unit_price")
    if win and median:
        win_dev = (win - median) / median * 100
        if win_dev > 30:
            flags.append("WINNER_OVERPRICED")
        elif any(f in flags for f in ("PRICE_CRITICAL_OVERPAY", "PRICE_IQR_OUTLIER")):
            flags.append("PLAN_INFLATED_ONLY")
    if win and lot.get("plan_unit_net") and win >= lot["plan_unit_net"] * 0.98:
        flags.append("NO_PRICE_DROP")

    days = lot.get("delivery_days")
    if days is not None and days < 5 and any(k in lot["name"].lower() for k in COMPLEX_KEYWORDS):
        flags.append("SHORT_DEADLINE")
    if lot.get("brand_lock"):
        flags.append("BRAND_LOCK_NO_EQUIV")
    if "одного источника" in (lot.get("method") or "").lower() or lot.get("bidders") == 1:
        flags.append("SINGLE_SOURCE_OR_SINGLE_BIDDER")
    prev = lot.get("prev_year_qty")
    if prev and lot["qty"] >= 2 * prev:
        flags.append("VOLUME_SPIKE_YOY")

    score = max(0, min(100, sum(FLAGS[f][0] for f in flags)))
    lvl = level_for(score)

    # Юнит-экономика поставщика: цена лота включает НДС; закупаем по минимуму Kaspi
    margin_pct = overpay = None
    if kaspi and kaspi.get("min_price"):
        revenue_net = unit / (1 + VAT)
        cost = kaspi["min_price"] * (1 + LOGISTICS) + unit * GUARANTEE
        margin_pct = (revenue_net - cost) / unit * 100
        paid = win if win else unit  # переплата считается по фактической цене, если она известна
        overpay = max(0.0, (paid - median) * lot["qty"])

    if lvl[1] == "LOW" and margin_pct is not None and margin_pct > 5:
        verdict = "💰 Выгодно для участия: честный конкурс, маржа после НДС положительная"
    elif lvl[1] == "LOW":
        verdict = "Цена рыночная, маржа низкая — участие на усмотрение"
    else:
        verdict = lvl[4]

    if overpay is None and "PRICE_PEER_OUTLIER" in flags:
        overpay = (unit - peer["median"]) * lot["qty"]

    return {**lot, "kaspi": kaspi, "peer": peer, "deviation_pct": dev, "flags": flags,
            "risk_score": score, "risk_code": lvl[1], "risk_label": lvl[2], "risk_fill": lvl[3],
            "margin_pct": margin_pct, "overpay": overpay, "verdict": verdict,
            "reasons": explain(lot, kaspi, peer, flags, dev), "to_check": what_to_check(flags)}


def _m(x) -> str:
    return f"{x:,.0f}".replace(",", " ") + " ₸"


def explain(lot, kaspi, peer, flags, dev) -> list[str]:
    """Человекочитаемые основания с конкретными цифрами — для справки комиссии."""
    r = []
    win = lot.get("winner_unit_price")
    pack = pack_size(lot, kaspi)
    if pack > 1:
        r.append(f"Лот — комплект из {pack} шт.: сравнение с Kaspi за 1 шт. "
                 f"({_m(lot['unit_price'] / pack)} за штуку)")
    if kaspi and dev is not None and dev > 10:
        r.append(f"Цена за ед. {_m(lot['unit_price'] / pack)} выше медианы Kaspi {_m(kaspi['median_price'])} "
                 f"на {dev:.0f}% (мин. {_m(kaspi['min_price'])}, продавцов: {kaspi.get('sellers_count')})")
    if win:
        r.append(f"Победитель: {lot.get('enriched', {}).get('winner', {}).get('name', '—')} — {_m(win)} за ед."
                 + (f" ({(win / kaspi['median_price'] - 1) * 100:+.0f}% к Kaspi)" if kaspi else "")
                 + f"; заявок: {lot.get('bidders')}")
    if "PLAN_INFLATED_ONLY" in flags:
        r.append("Плановая цена завышена, но торги снизили её до рыночной — фактической переплаты нет; "
                 "вопрос к обоснованию плана")
    if "NO_PRICE_DROP" in flags:
        r.append(f"Цена победителя {_m(win)} почти равна плановой {_m(lot['plan_unit_net'])} без НДС")
    if "PRICE_PEER_OUTLIER" in flags:
        r.append(f"Другие заказчики покупают этот товар по медиане {_m(peer['median'])} "
                 f"({peer['n']} лотов) — здесь дороже на {(lot['unit_price'] / peer['median'] - 1) * 100:.0f}%"
                 + (" (сверить единицы измерения и комплектацию)" if peer.get("generic") else ""))
    if "SPLIT_PURCHASE" in flags:
        r.append(f"У заказчика {peer['same_customer']} лотов этого товара в выборке — проверить дробление")
    if "SHORT_DEADLINE" in flags:
        r.append(f"Срок поставки {lot['delivery_days']} дн. для сложного оборудования"
                 + (f" (ТЗ: «{lot['enriched'].get('delivery_term')}»)" if lot.get("enriched") else ""))
    if "BRAND_LOCK_NO_EQUIV" in flags:
        r.append("В ТЗ указан конкретный бренд/модель без «или эквивалент»"
                 + (" (по полному тексту ТЗ)" if lot.get("enriched", {}).get("spec_full") else " (по названию лота)"))
    if "SINGLE_SOURCE_OR_SINGLE_BIDDER" in flags:
        r.append(f"Способ: {lot.get('method')}; участников: {lot.get('bidders') or 'н/д'}")
    if "VOLUME_SPIKE_YOY" in flags:
        r.append(f"Объём {lot['qty']:g} шт. против {lot['prev_year_qty']:g} в прошлом году")
    if "LOW_KASPI_COMPETITION" in flags:
        r.append(f"На Kaspi всего {kaspi.get('sellers_count')} продавец(а) — возможен эксклюзив")
    if "NO_MARKET_DATA" in flags:
        r.append("Рыночный аналог на Kaspi не найден — цена не сверена")
    return r


CHECKS = {
    "PRICE_CRITICAL_OVERPAY": "Запросить расчёт/обоснование цены и коммерческие предложения",
    "PRICE_IQR_OUTLIER": "Сверить ценовые предложения участников с рынком",
    "PRICE_PEER_OUTLIER": "Сравнить ТЗ с аналогичными лотами других заказчиков",
    "SPLIT_PURCHASE": "Проверить, не обходится ли конкурс дроблением лотов",
    "SHORT_DEADLINE": "Проверить, не был ли поставщик известен заранее (поставка до договора)",
    "BRAND_LOCK_NO_EQUIV": "Проверить ТЗ на соответствие требованию «или эквивалент»",
    "SINGLE_SOURCE_OR_SINGLE_BIDDER": "Проверить основание для одного источника / почему был единственный участник",
    "VOLUME_SPIKE_YOY": "Сверить объём с годовым планом и фактической потребностью",
    "WINNER_OVERPRICED": "Запросить у заказчика обоснование принятия цены победителя",
    "NO_PRICE_DROP": "Проверить, не было ли сговора участников (цены у плана)",
    "LOW_KASPI_COMPETITION": "Проверить аффилированность поставщика с дистрибьютором",
}


def what_to_check(flags) -> list[str]:
    return [CHECKS[f] for f in flags if f in CHECKS]


def peer_stats(lots: list[dict]) -> list[dict]:
    """Межлотовое сравнение: тот же товар (ЕНС ТРУ или запрос) у разных заказчиков."""
    def key(l):
        return l.get("enstru") or l.get("kaspi_query") or l.get("peer_key") or l["name"].lower()

    groups: dict[str, list[dict]] = {}
    for l in lots:
        groups.setdefault(key(l), []).append(l)
    out = []
    for l in lots:
        g = groups[key(l)]
        others = sorted(x["unit_price"] for x in g if x["customer_bin"] != l["customer_bin"])
        med = others[len(others) // 2] if others else None
        same = sum(1 for x in g if x["customer_bin"] == l["customer_bin"])
        out.append({"median": med, "n": len(others), "same_customer": same,
                    "generic": not (l.get("enstru") or l.get("kaspi_query"))})
    return out


def assess_all(lots: list[dict], kaspi_map: dict) -> list[dict]:
    peers = peer_stats(lots)
    return [assess(l, kaspi_map.get(l.get("kaspi_query") or ""), p) for l, p in zip(lots, peers)]
