#!/usr/bin/env python3
"""Постраничность журнала прогнозов: строка М-06.

**Зачем.** 16.09.2026 строку М-06 закрыли словами «задержки нет, замерено
performance.now()» — тогда GET /api/forecasts отдавал 12 692 прогноза.
18.09.2026 их 196 727, ответ весит 36 МБ и идёт 12 секунд (замер 76 в браузере
на стенде: 1 180 405 узлов в документе, 323 МБ кучи, страница перестаёт
слушаться). Галочка в приёмочном листе осталась от старого замера.

**Что проверяем и почему именно это.** Не время и не размер: и то и другое
зависит от сети и от того, сколько прогнозов накопилось, а порог пришлось бы
выдумывать — требования к размеру ответа у нас нет ни в постановке, ни в ТЗ.
Проверяем то, что от объёма не зависит: если клиент попросил `limit=10`,
ответ обязан содержать не больше десяти записей. Сейчас метод параметр молча
игнорирует и отдаёт всё — а молчаливое игнорирование хуже отказа: клиент
уверен, что взял страницу.

Размер и время печатаются рядом как факты, без вердикта: они объясняют, почему
строка важна, но сами ничего не доказывают.

Запуск:
    BASE_URL=https://135.106.216.101 CURL_OPTS=-k python3 code/check_api_paging.py
    python3 code/check_api_paging.py --selfcheck
"""

import json
import os
import subprocess
import sys
import time

ROWS = "М-06"
ПУТЬ = "/api/forecasts"
ПРОСИМ = 10


def сколько_записей(тело):
    """Длина списка, как бы метод его ни завернул: голым списком или в items."""
    if isinstance(тело, list):
        return len(тело)
    if isinstance(тело, dict) and isinstance(тело.get("items"), list):
        return len(тело["items"])
    raise ValueError(f"не список и не {{items: [...]}}: ключи {list(тело)[:5]}")


def вердикт(записей, просили):
    """Больше запрошенного — параметр не работает. Меньше или ровно — работает."""
    if записей > просили:
        return False, (
            f"limit={просили} не сработал: в ответе {записей} записей. "
            f"Метод отдаёт журнал целиком, постраничности нет — клиент, "
            f"попросивший страницу, получает всё и не узнаёт об этом"
        )
    return True, f"limit={просили} соблюдён: в ответе {записей} записей"


def _selfcheck():
    assert сколько_записей([1, 2, 3]) == 3
    assert сколько_записей({"total": 9, "items": [1, 2]}) == 2
    try:
        сколько_записей({"data": []})
    except ValueError:
        pass
    else:
        raise AssertionError("неизвестная форма ответа обязана быть ошибкой")
    assert вердикт(196727, 10)[0] is False
    assert вердикт(10, 10)[0] is True, "ровно столько, сколько просили, — это работает"
    assert вердикт(3, 10)[0] is True, "меньше запрошенного — тоже работает"
    print("самопроверка ok: форма ответа, limit соблюдён и проигнорирован")


def curl(url):
    cmd = ["curl", "-s", "-w", "\n%{size_download} %{time_total}"]
    cmd += os.environ.get("CURL_OPTS", "").split()
    cmd += ["-H", f"X-User-Login: {os.environ.get('API_LOGIN', 'admin1')}", url]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout
    тело, _, хвост = out.rpartition("\n")
    байт, секунд = хвост.split()
    return тело, int(байт), float(секунд)


def main():
    if "--selfcheck" in sys.argv:
        _selfcheck()
        return 0
    _selfcheck()
    base = os.environ.get("BASE_URL")
    if not base:
        print(f"{ROWS} СБОЙ: не задан BASE_URL")
        return 1
    начало = time.time()
    тело, байт, секунд = curl(f"{base}{ПУТЬ}?limit={ПРОСИМ}")
    try:
        записей = сколько_записей(json.loads(тело))
    except Exception as e:
        print(f"{ROWS} СБОЙ: ответ не разобрался — {type(e).__name__}: {e}")
        return 1
    ок, текст = вердикт(записей, ПРОСИМ)
    print(f"{ROWS} {'OK' if ок else 'СБОЙ'} {текст}; "
          f"ответ {байт / 1048576:.1f} МБ за {секунд:.1f} с "
          f"(запрошено за {time.time() - начало:.1f} с)")
    return 0 if ок else 1


if __name__ == "__main__":
    sys.exit(main())
