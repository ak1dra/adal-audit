#!/usr/bin/env python3
"""Дозагрузка карточки лота с портала goszakup (без токена) — только для сомнительных.

Для лота по номеру объявления (trd_buy_id) собирает:
  • шапку объявления: даты публикации и приёма заявок, число поданных заявок, статус;
  • вкладку «Лоты»: плановая цена за ед. без НДС, ед. изм., доп. характеристика;
  • техспецификацию (PDF): срок поставки, место поставки, полный текст описания;
  • протокол итогов (PDF): всех участников с ценами, победителя.

Результат кэшируется в data/lot_cache/ — повторные прогоны не ходят на портал.

  python3 enrich_lots.py --trd-buy 17844855 --lot 88254484-ЗЦП2
"""
from __future__ import annotations

import argparse
import html
import io
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_goszakup import PUBLIC_BASE, _get, brands_in, MODEL_RE  # noqa: E402

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "lot_cache"
TECHSPEC_DOC = 125  # тип документа «Техническая спецификация» на портале
MONTHS = ["январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр"]


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _input_value(page: str, label: str) -> str | None:
    m = re.search(re.escape(label) + r"</label>.*?value=\"([^\"]*)\"", page, re.S)
    return html.unescape(m.group(1)).strip() if m else None


def _pdf_text(url: str) -> str:
    from pypdf import PdfReader  # pip install pypdf
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = r.read()
    if not data.startswith(b"%PDF"):
        return ""
    text = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
    return re.sub(r"[\t ]+", " ", text)


def _russian_part(text: str, marker: str) -> str:
    """Документы двуязычные (каз. + рус.) — берём русскую половину."""
    i = text.find(marker)
    return text[i:] if i >= 0 else text


def _field(text: str, label: str, stop_labels: list[str]) -> str | None:
    stops = "|".join(re.escape(s) for s in stop_labels)
    m = re.search(re.escape(label) + r"\s*:?\s*(.+?)(?=\n(?:" + stops + r")|\Z)", text, re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else None


def parse_techspec(text: str) -> dict:
    ru = _russian_part(text, "Техническая спецификация")
    labels = ["Номер закупки", "Наименование закупки", "Номер лота", "Наименование лота", "Описание лота",
              "Дополнительное описание", "Количество", "Единица измерения", "Места поставки",
              "Срок поставки", "Описание и требуемые", "Приложение"]
    term = _field(ru, "Срок поставки:", labels)
    desc = _field(ru, "эксплуатационные\nхарактеристики закупаемых\nтоваров:", labels) or \
        _field(ru, "характеристики закупаемых товаров:", labels)
    extra = _field(ru, "Дополнительное описание\nлота:", labels) or _field(ru, "Дополнительное описание лота:", labels)
    return {"delivery_term": term, "delivery_place": _field(ru, "Места поставки:", labels),
            "spec_full": " ".join(x for x in [_field(ru, "Описание лота:", labels), extra, desc] if x)[:3000]}


def delivery_days(term: str | None, acceptance_end: str | None) -> int | None:
    """«в течение 10 календарных дней» → 10; «ноябрь» → дней от окончания приёма до конца месяца."""
    if not term:
        return None
    t = term.lower()
    m = re.search(r"(\d+)\s*\(?[^)]*\)?\s*(?:календарн|рабоч)?\w*\s*дн", t)
    if m:
        return int(m.group(1))
    if acceptance_end:
        for i, stem in enumerate(MONTHS):
            if re.search(r"\b" + stem, t) and not (stem == "ма" and "март" in t):
                y, mo, d = map(int, acceptance_end[:10].split("-"))
                month = i + 1
                year = y + (1 if month < mo else 0)
                import calendar
                from datetime import date
                end = date(year, month, calendar.monthrange(year, month)[1])
                return max(0, (end - date(y, mo, d)).days)
    return None


def parse_protocol(text: str, qty: float = 1, plan_unit: float | None = None) -> list[dict]:
    """Участники из протокола итогов: наименование, БИН/ИИН, цена за ед., сумма."""
    ru = _russian_part(text, "Протокол об итогах")
    if "приглашенном потенциальном поставщике" in ru:  # из одного источника: один приглашённый
        m = re.search(r"приглашенном потенциальном поставщике:.*?УНП\s*\n(.+?)\n(\d{12})", ru, re.S)
        p = re.search(r"ценового предложения\s*\n([\d.]+)\s*\n([\d.]+)\s*\n", ru)
        if not m:
            return []
        price = float(p.group(2)) if p else None
        unit = price if price and (not plan_unit or price <= plan_unit * 1.5) else (price / qty if price else None)
        return [{"name": re.sub(r"^Реквизиты\s*", "", re.sub(r"\s+", " ", m.group(1)).strip()), "bin": m.group(2), "unit_price": unit,
                 "total": unit * qty if unit else None, "winner": True, "invited": True}]
    bidders = []
    # строка участника: порядковый №, наименование (может переноситься), 12-значный БИН, цена за ед., …
    for m in re.finditer(r"\n(\d{1,3})\n(.+?)\n(\d{12})\n([\d.]+)\n([\d.]+)\n([\d.]+)\n", ru, re.S):
        name = re.sub(r"\s+", " ", m.group(2)).strip()
        name = re.sub(r"^.*хронологии\)\s*\d+\s*", "", name)  # шапка таблицы прилипает к первой строке
        bidders.append({"name": name, "bin": m.group(3),
                        "unit_price": float(m.group(4)), "total": float(m.group(6))})
    w = re.search(r"победител\w*[^\d]{0,80}?(\d{12})", ru)
    for b in bidders:
        b["winner"] = bool(w and b["bin"] == w.group(1))
    return bidders


def enrich(lot: dict, use_cache: bool = True) -> dict:
    tid, lot_no = lot.get("trd_buy_id"), str(lot.get("lot_id"))
    if not tid:
        return {}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"{tid}_{re.sub(r'[^0-9A-Za-zА-Яа-я-]', '_', lot_no)}.json"
    if use_cache and cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))

    base = f"{PUBLIC_BASE}/ru/announce/index/{tid}"
    info: dict = {"announce_url": base}
    page = _get(base)
    plain = _text(page)
    m = re.search(r"Кол-во поданных заявок:\s*(\d+)", plain)
    info["bids_count"] = int(m.group(1)) if m else None
    m = re.search(r"Приглашенный поставщик (.+?) Информация об организаторе", plain)
    if m:
        info["invited_supplier"] = m.group(1).strip()
        info["bids_count"] = 1
    m = re.search(r"Способ несостоявшейся закупки (.+?) Вид предмета", plain)
    if m:
        info["failed_method"] = m.group(1).strip()
    info["published"] = _input_value(page, "Дата публикации объявления")
    info["acceptance_start"] = _input_value(page, "Срок начала приема заявок")
    info["acceptance_end"] = _input_value(page, "Срок окончания приема заявок")
    info["announce_status"] = _input_value(page, "Статус объявления")

    lots_page = _get(base, {"tab": "lots"})
    for row in re.findall(r"<tr.*?</tr>", lots_page, re.S):
        if lot_no.split("-")[0] in row:
            cells = [_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            if len(cells) >= 8:
                info["lot_extra"] = cells[4]
                info["plan_unit_net"] = float(cells[5].replace(" ", "") or 0)
                info["unit_measure"] = cells[7]
            break

    try:
        # тип документа ТЗ отличается по способам закупки — перебираем все и ищем файл techspec_*
        docs = _get(base, {"tab": "documents"})
        types = list(dict.fromkeys(re.findall(r"actionModalShowFiles\(\d+,(\d+)\)", docs))) or [str(TECHSPEC_DOC)]
        link = None
        for t in types:
            files = _get(f"{PUBLIC_BASE}/ru/announce/actionAjaxModalShowFiles/{tid}/{t}")
            rows = [r for r in re.findall(r"<tr.*?</tr>", files, re.S) if "techspec" in r.lower()]
            rows = [r for r in rows if lot_no in r] or rows
            link = re.search(r'href="(https://v3bl[^"]+download_file[^"]+)"', rows[0]) if rows else None
            if link:
                break
        if link:
            info["techspec_url"] = link.group(1)
            info.update(parse_techspec(_pdf_text(link.group(1))))
            info["delivery_days"] = delivery_days(info.get("delivery_term"), info.get("acceptance_end"))
    except Exception as e:
        info["techspec_error"] = str(e)

    try:
        prot = _get(base, {"tab": "protocols"})
        links = re.findall(r'href="(https://v3bl[^"]+download_file[^"]+)"[^>]*>\s*<span[^>]*></span>\s*Просмотреть протокол', prot)
        if links:
            info["protocol_url"] = links[0]
            bidders = parse_protocol(_pdf_text(links[0]), lot.get("qty") or 1, info.get("plan_unit_net"))
            info["bidders"] = bidders
            if bidders and info.get("bids_count") is None:
                info["bids_count"] = len(bidders)
            if bidders:
                info["winner"] = next((b for b in bidders if b["winner"]),
                                      min(bidders, key=lambda b: b["unit_price"]))
    except Exception as e:
        info["protocol_error"] = str(e)

    text = f"{info.get('spec_full', '')} {info.get('lot_extra', '')}"
    info["brand_lock_spec"] = bool(brands_in(text)) and bool(MODEL_RE.search(text)) and "эквивалент" not in text.lower()
    cache.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    time.sleep(0.5)
    return info


def apply(lot: dict, info: dict, vat: float) -> dict:
    """Переносит данные карточки в лот для повторной оценки рисков."""
    if not info:
        return lot
    lot = dict(lot, enriched=info)
    if info.get("bids_count") is not None:
        lot["bidders"] = info["bids_count"]
    if info.get("delivery_days") is not None:
        lot["delivery_days"] = info["delivery_days"]
    if info.get("spec_full"):
        lot["spec"] = info["spec_full"]
        lot["brand_lock"] = info["brand_lock_spec"]
    if info.get("winner"):
        lot["winner_unit_price"] = info["winner"]["unit_price"]
    if info.get("plan_unit_net"):
        lot["plan_unit_net"] = info["plan_unit_net"]
    return lot


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trd-buy", required=True)
    ap.add_argument("--lot", required=True)
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args()
    print(json.dumps(enrich({"trd_buy_id": a.trd_buy, "lot_id": a.lot}, not a.no_cache), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
