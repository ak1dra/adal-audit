// Выгрузка лотов из публичного реестра goszakup — запускать на вкладке old.goszakup.gov.kz
// (javascript-инструмент браузера). Возвращает JSON-массив для generate_excel_report.py --lots-json.
// Отредактируйте FILTER и MAX_LOTS. Даты: start_date_to — на день ПОЗЖЕ последнего дня периода.
const FILTER = {
  kato: "790000000",              // код места поставки (getKato)
  trade_type: "g",                // g — товары
  start_date_from: "2026-10-23",
  start_date_to: "2026-10-28",
  name: "",                       // товар, необязательно
  amount_from: "",                // мин. сумма, необязательно
};
const MAX_LOTS = 2000;

const txt = (el) => (el ? el.textContent.replace(/\s+/g, " ").trim() : "");
const num = (s) => parseFloat(String(s).replace(/\s/g, "").replace(",", ".")) || 0;
const lots = [];
for (let page = 1; lots.length < MAX_LOTS; page++) {
  const u = new URL("/ru/search/lots", location.origin);
  for (const [k, v] of Object.entries(FILTER)) if (v) u.searchParams.set(`filter[${k}]`, v);
  u.searchParams.set("count_record", "500");
  u.searchParams.set("page", String(page));
  const doc = new DOMParser().parseFromString(await (await fetch(u)).text(), "text/html");
  const rows = [...doc.querySelectorAll("#search-result tr")].filter((r) => r.querySelector("td"));
  for (const r of rows) {
    const td = r.querySelectorAll("td");
    if (td.length < 7) continue;
    const a = td[1].querySelector('a[href*="/announce/index/"]');
    const small = td[1].querySelector("small");
    const id = a ? a.getAttribute("href").match(/index\/(\d+)/)[1] : null;
    lots.push({
      lot_id: txt(td[0]),
      announce: txt(a),
      customer: txt(small).replace(/^Заказчик:\s*/, ""),
      trd_buy_id: id,
      lot_url: id ? `${location.origin}/ru/announce/index/${id}` : null,
      name: txt(td[2].querySelector("a")) || txt(td[2]),
      qty: num(txt(td[3])),
      amount: num(txt(td[4])),          // сумма без НДС
      method: txt(td[5]),
      status: txt(td[6]),
    });
  }
  if (rows.length < 500) break;
}
JSON.stringify(lots);
