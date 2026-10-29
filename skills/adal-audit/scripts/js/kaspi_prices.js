// Цены Kaspi по списку запросов — запускать на вкладке kaspi.kz (javascript-инструмент браузера).
// Возвращает JSON {запрос: {title, median_price, min_price, max_price, sellers_count, kaspi_url} | null}
// для generate_excel_report.py --kaspi-json. QUERIES — значения kaspi_query из candidates.json.
const QUERIES = ["картридж Xerox 106R02773"];
const CITY = "710000000";

const tokens = (s) => new Set((s.toLowerCase().match(/[a-zа-яё0-9]+/g) || []).filter((w) => w.length >= 2));
function relevant(query, hay) {
  // та же логика, что в search_kaspi.relevance: модель/артикул с цифрами обязаны совпасть
  const q = tokens(query), t = tokens(hay), flat = hay.toLowerCase().replace(/[^a-zа-яё0-9]/g, "");
  if (!q.size) return false;
  for (const w of q) if (/\d/.test(w) && !t.has(w) && !flat.includes(w)) return false;
  const head = query.toLowerCase().match(/^[а-яё]{4,}/);
  if (head && !hay.toLowerCase().includes(head[0].slice(0, 5))) return false;
  let hit = 0; for (const w of q) if (t.has(w)) hit++;
  return hit / q.size >= 0.5;
}
const median = (a) => { const s = [...a].sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const out = {};
for (const q of QUERIES) {
  const p = new URLSearchParams({ text: q, page: "0", c: CITY, ui: "d", sort: "relevance" });
  const cards = ((await (await fetch(`/yml/product-view/pl/filters?${p}`, { headers: { Accept: "application/json" } })).json()).data || {}).cards || [];
  const card = cards.slice(0, 8).find((c) => (c.unitSalePrice || c.unitPrice) && relevant(q, `${c.title} ${(c.category || []).join(" ")}`));
  if (!card) { out[q] = null; continue; }
  const r = await fetch(`/yml/offer-view/offers/${card.id}`, {
    method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json", "X-KS-City": CITY },
    body: JSON.stringify({ cityId: CITY, id: String(card.id), merchantUID: "", limit: 50, page: 0, sort: true, installationId: "-1" }),
  });
  const offers = (await r.json()).offers || [];
  const prices = offers.map((o) => o.price).filter(Boolean);
  if (!prices.length) prices.push(card.unitSalePrice || card.unitPrice);
  out[q] = { title: card.title, median_price: median(prices), min_price: Math.min(...prices),
             max_price: Math.max(...prices), sellers_count: prices.length,
             kaspi_url: `${location.origin}/shop/p/-${card.id}/?c=${CITY}` };
  await new Promise((s) => setTimeout(s, 600));
}
JSON.stringify(out);
