#!/usr/bin/env python3
"""Пайплайн аудита: лоты госзакупок → рыночные цены → Red Flags → Excel для комиссии.

В отчёт попадают ТОЛЬКО сомнительные лоты (по умолчанию ВЫСОКИЙ и КРИТИЧЕСКИЙ),
отсортированные по риску, с прямыми ссылками на лот и на карточку Kaspi.

  # реальные лоты (нужен GOSZAKUP_API_TOKEN)
  python3 generate_excel_report.py --region Караганд --from 2026-01-01 --to 2026-03-31 \\
      --keyword ноутбук --min-amount 1000000 --limit 1000 --live
  # демонстрация масштаба без токена
  python3 generate_excel_report.py --demo --demo-size 300 --live

Листы:
  1. «Сомнительные лоты»   — реестр для комиссии + памятка шкалы справа
  2. «Параметры и итоги»   — фильтры, сколько проверено/отобрано, разрез по регионам
  3. «Методология»         — флажки, веса, формулы, оговорки
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import DataBarRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_goszakup  # noqa: E402
import risk_engine  # noqa: E402
import enrich_lots  # noqa: E402
import search_kaspi  # noqa: E402

THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD_FILL = PatternFill("solid", fgColor="1F3864")
HEAD_FONT = Font(bold=True, color="FFFFFF")
TITLE_FONT = Font(bold=True, size=14, color="1F3864")
LINK_FONT = Font(color="0563C1", underline="single")
MONEY = '#,##0 "₸"'
WRAP = Alignment(wrap_text=True, vertical="top")
STATUSES = ["Не проверено", "Подтверждено", "Ложное срабатывание", "Передано в КВФК", "Требует запроса"]


def header(ws, row, col, values):
    for i, v in enumerate(values):
        c = ws.cell(row=row, column=col + i, value=v)
        c.fill, c.font, c.border = HEAD_FILL, HEAD_FONT, BORDER
        c.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")


def cell(ws, row, col, value, fmt=None, bold=False, bg=None, link=None):
    c = ws.cell(row=row, column=col, value=value)
    c.border, c.alignment = BORDER, WRAP
    if fmt:
        c.number_format = fmt
    if bold:
        c.font = Font(bold=True)
    if bg:
        c.fill = PatternFill("solid", fgColor=bg)
    if link:
        c.hyperlink, c.font = link, LINK_FONT
    return c


def sheet_suspicious(wb, rows):
    ws = wb.active
    ws.title = "Сомнительные лоты"
    ws["A1"] = (f"Реестр сомнительных лотов госзакупок РК — {len(rows)} шт." if rows else
                "Сомнительных лотов по автоматическим признакам не найдено — см. лист «Требуют ручной проверки»")
    ws["A1"].font = TITLE_FONT
    ws["A2"] = "Флажок — индикатор риска, а не доказательство нарушения. Каждый лот требует ручной проверки."
    ws["A2"].font = Font(italic=True, color="7F7F7F")
    cols = ["№", "Уровень", "Балл", "Регион", "Заказчик (БИН)", "Лот → goszakup.gov.kz", "Способ закупки",
            "Кол-во", "План. цена за ед. с НДС", "Сумма с НДС", "Рыночная цена (Kaspi) →", "Отклонение плана",
            "Заявок", "Победитель и его цена", "Срок поставки (ТЗ)", "Возможная переплата",
            "Основания (с цифрами)", "Что проверить", "ТЗ (выдержка) → PDF", "Статус проверки",
            "Комментарий аудитора"]
    header(ws, 4, 1, cols)
    for i, r in enumerate(rows, start=5):
        k = r["kaspi"] or {}
        e = r.get("enriched") or {}
        cell(ws, i, 1, i - 4)
        cell(ws, i, 2, r["risk_label"], bold=True, bg=r["risk_fill"])
        cell(ws, i, 3, r["risk_score"])
        cell(ws, i, 4, r["region"])
        cell(ws, i, 5, f"{r['customer']}\nБИН {r['customer_bin']}" if r["customer_bin"] != r["customer"]
             else r["customer"])
        demo = " [DEMO]" if r.get("is_demo") else ""
        cell(ws, i, 6, f"{r['lot_id']}{demo}\n{r['name']}", link=r.get("lot_url"))
        cell(ws, i, 7, r["method"])
        cell(ws, i, 8, r["qty"])
        cell(ws, i, 9, r["unit_price"], MONEY)
        cell(ws, i, 10, f"=H{i}*I{i}", MONEY)
        if k:
            cell(ws, i, 11, f"{k['median_price']:,.0f} ₸ (медиана, {k.get('sellers_count')} прод.)\n"
                            f"{(k.get('title') or '')[:60]}".replace(",", " "), link=k.get("kaspi_url"))
            cell(ws, i, 12, r["deviation_pct"] / 100 if r["deviation_pct"] is not None else None, "+0%;-0%")
        else:
            cell(ws, i, 11, "не сверялось" if not r.get("kaspi_query") else "не найдено")
            cell(ws, i, 12, None)
        cell(ws, i, 13, r.get("bidders") if e else "н/д")
        w = e.get("winner")
        if w:
            def fm(x):
                return f"{x:,.0f}".replace(",", " ") if x else "?"
            others = ", ".join(fm(b["unit_price"]) for b in e.get("bidders", [])[:6])
            cell(ws, i, 14, f"{w['name'][:50]} (БИН {w['bin']})\n{fm(w['unit_price'])} ₸ за ед.\n"
                            f"Все цены: {others}", link=e.get("protocol_url"))
        else:
            cell(ws, i, 14, "итогов нет" if e else "н/д")
        dd = e.get("delivery_days")
        cell(ws, i, 15, (f"{e.get('delivery_term')}" + (f" (~{dd} дн.)" if dd is not None else ""))
             if e.get("delivery_term") else "н/д")
        cell(ws, i, 16, r["overpay"], MONEY)
        cell(ws, i, 17, "\n".join("• " + x for x in r["reasons"]))
        cell(ws, i, 18, "\n".join("☐ " + x for x in r["to_check"]))
        cell(ws, i, 19, (e.get("spec_full") or "")[:400] or "н/д", link=e.get("techspec_url"))
        rev = r.get("review") or {}
        cell(ws, i, 20, rev.get("status", "Не проверено"))
        cell(ws, i, 21, rev.get("note"))
        ws.row_dimensions[i].height = max(75, 16 * max(len(r["reasons"]), len(r["to_check"]), 3))
    last = 4 + len(rows)
    if rows:
        ws.conditional_formatting.add(f"C5:C{last}", DataBarRule(start_type="num", start_value=0,
                                                                 end_type="num", end_value=100, color="E06666"))
        dv = DataValidation(type="list", formula1='"' + ",".join(STATUSES) + '"', allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"T5:T{last}")
        ws.auto_filter.ref = f"A4:U{last}"

    # Памятка рядом с реестром (S–U)
    ws["W4"] = "Шкала опасности"
    ws["W4"].font = Font(bold=True, color="C00000")
    for j, lvl in enumerate(risk_engine.LEVELS, start=5):
        cell(ws, j, 23, lvl[2], bold=True, bg=lvl[3])
        cell(ws, j, 24, {75: "75–100", 50: "50–74", 25: "25–49", 0: "0–24"}[lvl[0]], bg=lvl[3])
        cell(ws, j, 25, lvl[4], bg=lvl[3])
    widths = (5, 15, 7, 16, 30, 40, 18, 7, 13, 14, 28, 10, 8, 34, 18, 15, 55, 45, 45, 18, 28, 3, 16, 8, 40)
    for idx, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = w
    ws.freeze_panes = "G5"


def sheet_summary(wb, all_rows, shown, params, excluded):
    ws = wb.create_sheet("Параметры и итоги")
    ws["A1"] = "Параметры проверки и итоги"
    ws["A1"].font = TITLE_FONT
    header(ws, 3, 1, ["Параметр", "Значение"])
    items = list(params.items()) + [
        ("Проверено лотов", len(all_rows)),
        ("Бюджет проверенных лотов, ₸", sum(r["total"] for r in all_rows)),
        ("Отобрано сомнительных", len(shown)),
        ("Исключено аудитором как ложные", excluded),
        ("Возможная переплата по сомнительным, ₸", sum(r["overpay"] or 0 for r in shown)),
        ("Лотов со сверкой Kaspi", sum(1 for r in all_rows if r["kaspi"])),
    ]
    for i, (k, v) in enumerate(items, start=4):
        cell(ws, i, 1, k, bold=True)
        cell(ws, i, 2, v, MONEY if "₸" in k else None)

    r0 = 6 + len(items)
    ws.cell(row=r0 - 1, column=1, value="Разрез по регионам").font = Font(bold=True, size=12)
    header(ws, r0, 1, ["Регион", "Проверено", "🔴 Крит.", "🟠 Высок.", "Возм. переплата, ₸"])
    regions = sorted({r["region"] for r in all_rows})
    stats = []
    for reg in regions:
        sel = [r for r in all_rows if r["region"] == reg]
        sus = [r for r in shown if r["region"] == reg]
        stats.append((reg, len(sel), sum(r["risk_code"] == "CRITICAL" for r in sus),
                      sum(r["risk_code"] == "HIGH" for r in sus), sum(r["overpay"] or 0 for r in sus)))
    for i, row in enumerate(sorted(stats, key=lambda x: -x[4]), start=r0 + 1):
        for j, v in enumerate(row, start=1):
            cell(ws, i, j, v, MONEY if j == 5 else None)

    r1 = r0 + len(stats) + 3
    ws.cell(row=r1 - 1, column=1, value="Топ-10 заказчиков по числу сомнительных лотов").font = Font(bold=True, size=12)
    header(ws, r1, 1, ["Заказчик", "БИН", "Лотов", "Возм. переплата, ₸"])
    by_c: dict = {}
    for r in shown:
        e = by_c.setdefault(r["customer_bin"], [r["customer"], 0, 0.0])
        e[1] += 1
        e[2] += r["overpay"] or 0
    for i, (b, (name, n, ov)) in enumerate(sorted(by_c.items(), key=lambda x: (-x[1][1], -x[1][2]))[:10],
                                           start=r1 + 1):
        cell(ws, i, 1, name)
        cell(ws, i, 2, b)
        cell(ws, i, 3, n)
        cell(ws, i, 4, ov, MONEY)
    for col, w in zip("ABCDE", (45, 22, 10, 10, 20)):
        ws.column_dimensions[col].width = w


def sheet_manual(wb, all_rows, shown, n=25):
    """Крупные лоты, которые движок не смог оценить по цене: нет ни Kaspi, ни сравнения с
    другими заказчиками. Пустой реестр не должен выглядеть как «всё чисто»."""
    ws = wb.create_sheet("Требуют ручной проверки")
    shown_ids = {id(r) for r in shown}
    rows = [r for r in all_rows if id(r) not in shown_ids and not r["kaspi"]
            and "PRICE_PEER_OUTLIER" not in r["flags"]]
    rows = sorted(rows, key=lambda r: -r["total"])[:n]
    ws["A1"] = f"Крупные лоты без ценовой сверки — {len(rows)} шт."
    ws["A1"].font = TITLE_FONT
    ws["A2"] = ("Модели нет ни в названии, ни в ТЗ, и аналогов у других заказчиков нет — рыночную цену нужно "
                "оценить вручную (коммерческие предложения, прайсы, Kaspi по характеристикам).")
    ws["A2"].font = Font(italic=True, color="7F7F7F")
    header(ws, 4, 1, ["№", "Лот → портал", "Заказчик", "Способ", "Кол-во", "Цена за ед. с НДС", "Сумма с НДС",
                      "Заявок", "Победитель / цена", "Срок поставки", "ТЗ (выдержка) → PDF", "Балл"])
    for i, r in enumerate(rows, start=5):
        e = r.get("enriched") or {}
        w = e.get("winner") or {}
        cell(ws, i, 1, i - 4)
        cell(ws, i, 2, f"{r['lot_id']}\n{r['name']}", link=r.get("lot_url"))
        cell(ws, i, 3, r["customer"])
        cell(ws, i, 4, r["method"])
        cell(ws, i, 5, r["qty"])
        cell(ws, i, 6, r["unit_price"], MONEY)
        cell(ws, i, 7, r["total"], MONEY)
        cell(ws, i, 8, e.get("bids_count", "н/д") if e else "н/д")
        cell(ws, i, 9, f"{w.get('name', '')[:40]}\n{w['unit_price']:,.0f} ₸".replace(",", " ")
             if w.get("unit_price") else ("итогов нет" if e else "н/д"), link=e.get("protocol_url"))
        cell(ws, i, 10, e.get("delivery_term") or "н/д")
        cell(ws, i, 11, (e.get("spec_full") or "")[:300] or "н/д", link=e.get("techspec_url"))
        cell(ws, i, 12, r["risk_score"])
        ws.row_dimensions[i].height = 60
    for col, w in zip("ABCDEFGHIJKL", (5, 38, 34, 20, 8, 15, 16, 8, 26, 22, 50, 7)):
        ws.column_dimensions[col].width = w


def sheet_method(wb):
    ws = wb.create_sheet("Методология")
    ws["A1"] = "Индикаторы риска (Red Flags)"
    ws["A1"].font = TITLE_FONT
    header(ws, 3, 1, ["Код", "Вес", "Условие", "Что проверить"])
    for i, (code, (w, desc)) in enumerate(risk_engine.FLAGS.items(), start=4):
        cell(ws, i, 1, code, bold=True)
        cell(ws, i, 2, w)
        cell(ws, i, 3, desc)
        cell(ws, i, 4, risk_engine.CHECKS.get(code, ""))
    r = 5 + len(risk_engine.FLAGS)
    for j, t in enumerate([
        "Балл = min(100, Σ весов). 75–100 КРИТИЧЕСКИЙ · 50–74 ВЫСОКИЙ · 25–49 СРЕДНИЙ · 0–24 НИЗКИЙ.",
        "Отклонение = (цена за ед. − медиана Kaspi) / медиана Kaspi. Переплата = max(0, цена − медиана) × кол-во.",
        "Сравнение с другими заказчиками — медиана цены того же ЕНС ТРУ/товара в выборке, без учёта самого заказчика.",
        "Розничная цена Kaspi включает НДС и наценку; спецоборудование и услуги на Kaspi часто отсутствуют.",
        "Карточка Kaspi принимается, только если совпадает ≥50% значимых слов запроса и медиана ≥ 20% цены лота.",
        "Нормативная база: Закон РК «О государственных закупках» — сверяйте номера статей на adilet.zan.kz.",
    ], start=r):
        ws.cell(row=j, column=1, value=t)
    for col, w in zip("ABCD", (32, 6, 60, 60)):
        ws.column_dimensions[col].width = w


def lots_from_json(path: str) -> list[dict]:
    """Лоты, собранные агентом через браузер (режим без сетевого доступа у скриптов).
    Минимум на лот: lot_id, name, customer, qty и amount (сумма без НДС) или unit_price_net."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    vat = risk_engine.VAT
    lots = []
    for x in raw:
        qty = float(x.get("qty") or 1) or 1
        net = float(x.get("unit_price_net") or (float(x.get("amount") or 0) / qty))
        text = f"{x.get('announce', '')} {x.get('name', '')} {x.get('spec_full') or x.get('spec', '')}"
        e = {k: x[k] for k in ("bids_count", "delivery_term", "delivery_days", "spec_full", "techspec_url",
                               "protocol_url", "winner", "bidders", "unit_measure") if x.get(k) is not None}
        lot = {
            "lot_id": x["lot_id"], "name": x["name"], "announce": x.get("announce", ""),
            "customer": x.get("customer", ""), "customer_bin": x.get("customer_bin") or x.get("customer", ""),
            "region": x.get("region") or fetch_goszakup.detect_region(x.get("customer", "")),
            "enstru": x.get("enstru", ""), "method": x.get("method", ""), "qty": qty,
            "unit_price_net": net, "unit_price": net * (1 + vat), "plan_unit_net": net,
            "kaspi_query": x.get("kaspi_query") or fetch_goszakup.kaspi_query_for(x["name"], text),
            "peer_key": x["name"].lower()[:40], "delivery_days": x.get("delivery_days"),
            "bidders": x.get("bids_count") or (1 if "одного источника" in x.get("method", "").lower() else None),
            "brand_lock": bool(fetch_goszakup.brands_in(text)) and bool(fetch_goszakup.MODEL_RE.search(text))
            and "эквивалент" not in text.lower(),
            "prev_year_qty": None, "spec": x.get("spec_full") or x.get("spec") or "",
            "trd_buy_id": x.get("trd_buy_id"), "lot_url": x.get("lot_url"), "is_demo": False,
        }
        if x.get("winner_unit_price") or (x.get("winner") or {}).get("unit_price"):
            lot["winner_unit_price"] = x.get("winner_unit_price") or x["winner"]["unit_price"]
        if e:
            lot["enriched"] = e
        lot["total"] = lot["qty"] * lot["unit_price"]
        lots.append(lot)
    return lots


def kaspi_from_json(path: str) -> dict:
    """Цены Kaspi, собранные агентом через браузер: {kaspi_query: {title, median_price, ...}}."""
    out = {}
    for q, k in json.loads(Path(path).read_text(encoding="utf-8")).items():
        if not k or not k.get("median_price"):
            out[q] = None
            continue
        med = float(k["median_price"])
        out[q] = {"query": q, "title": k.get("title"), "median_price": med,
                  "min_price": float(k.get("min_price") or med), "max_price": float(k.get("max_price") or med),
                  "p25": float(k.get("p25") or k.get("min_price") or med), "p75": float(k.get("p75") or med),
                  "sellers_count": k.get("sellers_count"), "kaspi_url": k.get("kaspi_url"), "source": "browser"}
    return out


def run(a) -> tuple[list[dict], list[dict]]:
    lots = lots_from_json(a.lots_json) if a.lots_json else fetch_goszakup.lots_from_args(a)
    if not lots:
        raise SystemExit("Нет лотов под заданные фильтры")
    # Kaspi — по уникальным товарам, начиная с самых дорогих лотов; лимит живых запросов
    order = sorted(lots, key=lambda l: -l["total"])
    if a.kaspi_json:
        kaspi_map = kaspi_from_json(a.kaspi_json)
    else:
        kaspi_map = search_kaspi.check_many([l["kaspi_query"] for l in order if l.get("kaspi_query")],
                                            demo=not a.live, budget=a.kaspi_limit)
    rows = risk_engine.assess_all(lots, kaspi_map)

    # Дозагрузка карточек лотов (ТЗ, срок, участники) — только для кандидатов, затем переоценка
    enriched = 0
    if a.enrich_limit and not a.demo and not a.lots_json:
        flagged = sorted((r for r in rows if r["risk_score"] >= 25 and r.get("trd_buy_id")),
                         key=lambda r: -r["risk_score"])
        # крупнейшие лоты проверяются всегда: у них часто нет модели в названии, и без ТЗ
        # движку нечего сравнивать — а деньги там самые большие
        biggest = sorted((r for r in rows if r.get("trd_buy_id")), key=lambda r: -r["total"])[:a.enrich_top]
        seen, cand = set(), []
        for r in flagged + biggest:
            k = (r["trd_buy_id"], str(r["lot_id"]))
            if k not in seen:
                seen.add(k)
                cand.append(r)
        cand = cand[:a.enrich_limit + a.enrich_top]
        by_key = {(l.get("trd_buy_id"), str(l["lot_id"])): i for i, l in enumerate(lots)}
        for n, r in enumerate(cand, 1):
            print(f"[enrich] {n}/{len(cand)} {r['lot_id']}", file=sys.stderr)
            try:
                info = enrich_lots.enrich(r)
            except Exception as e:
                print(f"[enrich] ошибка {r['lot_id']}: {e}", file=sys.stderr)
                continue
            i = by_key[(r.get("trd_buy_id"), str(r["lot_id"]))]
            lots[i] = enrich_lots.apply(lots[i], info, risk_engine.VAT)
            enriched += 1
        # модель/артикул часто есть только в ТЗ — строим запрос в Kaspi по полному тексту
        new_q = []
        for l in lots:
            spec = (l.get("enriched") or {}).get("spec_full")
            if spec and not l.get("kaspi_query"):
                l["kaspi_query"] = fetch_goszakup.kaspi_query_for(l["name"], spec)
                if l["kaspi_query"]:
                    new_q.append(l["kaspi_query"])
        if new_q and not a.kaspi_json:
            kaspi_map.update(search_kaspi.check_many(new_q, demo=not a.live, budget=a.kaspi_limit))
        rows = risk_engine.assess_all(lots, kaspi_map)

    review = json.loads(Path(a.review).read_text(encoding="utf-8")) if a.review else {}
    min_score = 25 if a.include_medium else 50
    shown, excluded = [], 0
    for r in rows:
        r["review"] = review.get(str(r["lot_id"]))
        if r["review"] and r["review"].get("status") == "Ложное срабатывание":
            excluded += 1
            continue
        if r["risk_score"] >= min_score:
            shown.append(r)
    shown.sort(key=lambda r: (-r["risk_score"], -(r["overpay"] or 0)))
    over_top = max(0, len(shown) - a.top)
    shown = shown[:a.top]

    params = {
        "Дата формирования": time.strftime("%Y-%m-%d %H:%M"),
        "Источник лотов": "Собрано агентом через браузер (goszakup.gov.kz)" if a.lots_json else "DEMO-данные (не для официального использования)" if a.demo
        else ("OWS API goszakup.gov.kz" if __import__("os").environ.get("GOSZAKUP_API_TOKEN") and a.source != "public"
              else "Публичный реестр лотов goszakup.gov.kz"),
        "НДС, учтённый в цене": f"{risk_engine.VAT:.0%}",
        "Регион (место поставки)": a.region or "все", "Ключевое слово": a.keyword or "—",
        "Период (дата обновления)": f"{a.date_from or '…'} — {a.date_to or '…'}",
        "Мин. сумма лота, ₸": a.min_amount, "Лимит лотов": a.limit,
        "Порог попадания в реестр": f"балл ≥ {min_score}",
        "Kaspi": "живой опрос + кэш" if a.live else "только кэш",
        "Дозагружено карточек лотов (ТЗ, участники)": enriched,
        "Не вошло в реестр сверх лимита --top": over_top,
    }
    wb = Workbook()
    sheet_suspicious(wb, shown)
    sheet_summary(wb, rows, shown, params, excluded)
    sheet_manual(wb, rows, shown)
    sheet_method(wb)
    wb.save(a.output)
    return rows, shown


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    fetch_goszakup.add_filter_args(ap)
    ap.add_argument("--lots-json", help="лоты, собранные через браузер (режим без сети), см. references/browser-mode.md")
    ap.add_argument("--kaspi-json", help="цены Kaspi, собранные через браузер")
    ap.add_argument("--live", action="store_true", help="живой опрос Kaspi (иначе только кэш)")
    ap.add_argument("--kaspi-limit", type=int, default=150, help="макс. живых запросов к Kaspi за прогон")
    ap.add_argument("--enrich-limit", type=int, default=50,
                    help="сколько кандидатов дозагрузить с портала (ТЗ, срок, участники); 0 — выкл.")
    ap.add_argument("--enrich-top", type=int, default=10,
                    help="сколько крупнейших лотов дозагрузить всегда, даже без флажков")
    ap.add_argument("--top", type=int, default=100, help="макс. лотов в реестре (самые рискованные)")
    ap.add_argument("--include-medium", action="store_true", help="включить СРЕДНИЙ уровень в реестр")
    ap.add_argument("--review", help="JSON с вердиктами аудитора {lot_id: {status, note}}")
    ap.add_argument("--output", default="goszakup_kaspi_audit.xlsx")
    ap.add_argument("--json", help="сохранить сомнительные лоты в JSON (для проверки аудитором)")
    a = ap.parse_args()
    rows, shown = run(a)
    if a.json:
        Path(a.json).write_text(json.dumps(shown, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"Проверено: {len(rows)} · сомнительных: {len(shown)}")
    for r in shown[:15]:
        print(f"  {r['risk_label']:<15} {r['risk_score']:>3}  {r['region']:<20} {r['lot_id']}  {r['name'][:50]}")
    print(f"\n✅ Отчёт: {a.output}")


if __name__ == "__main__":
    main()
