#!/usr/bin/env python3
"""Шаг 0: есть ли у этого окружения доступ к порталу госзакупок и Kaspi.

Печатает JSON с результатом и выбранным режимом:
  "scripts" — всё доступно, работает полный пайплайн;
  "browser" — сайты заблокированы (типично для песочницы claude.ai): данные собирает
              агент через браузер, см. references/browser-mode.md;
и недостающие Python-пакеты.
"""
from __future__ import annotations

import importlib.util
import json
import urllib.request

TARGETS = {
    "goszakup": "https://old.goszakup.gov.kz/ru/search/lots?count_record=50",
    "kaspi": "https://kaspi.kz/shop/",
}


def reachable(url: str) -> tuple[bool, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200, f"HTTP {r.status}"
    except Exception as e:  # 403 от прокси песочницы, DNS, таймаут
        return False, str(e)[:120]


def main() -> None:
    res = {name: dict(zip(("ok", "detail"), reachable(url))) for name, url in TARGETS.items()}
    missing = [m for m in ("openpyxl", "pypdf") if importlib.util.find_spec(m) is None]
    mode = "scripts" if res["goszakup"]["ok"] else "browser"
    print(json.dumps({"mode": mode, "access": res, "missing_packages": missing,
                      "install": f"pip install {' '.join(missing)}" if missing else None},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
