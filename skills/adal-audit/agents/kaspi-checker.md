# Агент 2 — Kaspi Market Checker

**Задача:** по `kaspi_query` каждого лота найти рыночную цену на Kaspi Магазине.

## Как работает `scripts/search_kaspi.py`
1. Поиск карточки — публичный endpoint витрины `kaspi.kz/yml/product-view/pl/filters`.
2. Все продавцы — `POST https://kaspi.kz/yml/offer-view/offers/{id}`.
3. Считает `min_price`, `median_price`, `max_price`, `p25/p75` (для IQR), `sellers_count`, `rating`, `reviews_count`, `kaspi_url`, `delivery_label`.
4. Пишет результат в `data/kaspi_cache.json`; `--demo` или сетевая ошибка → берёт кэш (поле `source: cache`).

```bash
python3 scripts/search_kaspi.py --query "HP LaserJet Pro MFP M428fdn" --query "TP-Link Archer AX55"
```

## Качество матча
Проверяй `title` найденной карточки: модель должна совпадать с ТЗ. Если не совпадает — уточни запрос (модель, артикул). Медиана < 20% цены лота автоматически отбрасывается движком как нерелевантная.
