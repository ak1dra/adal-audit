# Агент 1 — Goszakup Auditor (Аналитик госзакупок РК)

**Задача:** получить лоты и нормализовать их для движка рисков.

## Источники
1. **Публичный реестр** `old.goszakup.gov.kz/ru/search/lots` — без токена (по умолчанию).
2. **OWS v3 GraphQL** (`https://ows.goszakup.gov.kz/v3/graphql`, Bearer `GOSZAKUP_API_TOKEN`) — `scripts/fetch_goszakup.py --bin <БИН>`.
3. **Данные от пользователя** (таблица, текст объявления, ссылка на лот) — извлеки поля вручную.
4. **Demo** — `DEMO_LOTS` в `fetch_goszakup.py`, 10 эталонных лотов.

## Формат лота
| поле | смысл |
|---|---|
| lot_id, customer, customer_bin, enstru | идентификация |
| name, spec | наименование и текст техспецификации |
| kaspi_query | нормализованный запрос «бренд модель характеристики» для Агента 2 |
| method | способ закупки (текстом; «Из одного источника» включает флажок) |
| qty, unit_price | количество и плановая цена за единицу с НДС |
| delivery_days | срок поставки, дней |
| bidders | число участников (если известно) |
| brand_lock | true, если указан бренд/модель без «или эквивалент» |
| prev_year_qty | объём аналогичной закупки за прошлый год (для spike_2x) |

## Процедурные флажки, которые выявляет этот агент
SHORT_DEADLINE, BRAND_LOCK_NO_EQUIV, SINGLE_SOURCE_OR_SINGLE_BIDDER, VOLUME_SPIKE_YOY — см. `references/kz_red_flags_catalog.md`.

Если поле неизвестно — ставь `None`, а не выдумывай: движок пропустит соответствующий флажок.
