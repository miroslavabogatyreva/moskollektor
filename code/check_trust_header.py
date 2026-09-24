#!/usr/bin/env python3
"""Подделка заголовка X-User-Login при AUTH_TRUST_HEADER=0 — строка НФ-43. MOS-39 (Q4.2).

**Зачем отдельно от check_auth.py.** На нашем стенде AUTH_TRUST_HEADER=1: на заголовке
держатся 19 старых проверок (решение Славы 24.09.2026). У эксперта будет 0, и подделка
заголовка обязана дать 401. Рабочий api стенда ради этого не перенастраиваем. Вместо
этого поднимаем РАЗОВЫЙ контейнер из того же образа moskollektor-api, с тем же
DATABASE_URL и ключом, в той же сети compose, и проверяем сдаваемый образ, а не его подобие.

Внутри контейнера api слушает 127.0.0.1:8001 в своём потоке, запрос идёт туда же:
- AUTH_TRUST_HEADER=0: GET /api/settings с X-User-Login: admin1 → 401;
- AUTH_TRUST_HEADER=1 — положительный контроль: тот же запрос → 200. Без него ноль
  на первом шаге мог бы означать «api вообще не поднялся», а не «заголовок отвергнут».
Контейнер с --rm, после прогона сверяем, что его не осталось. Каждый запрос пишет
строку в audit.user_action стенда: для 401 с user_id NULL, для контроля — за admin1.

Запуск:
    STAND_SSH=root@135.106.216.101 python3 code/check_trust_header.py
"""

import os
import subprocess
import sys

ОБРАЗ = "moskollektor-api"
СЕТЬ = "moskollektor_default"
ENV_ФАЙЛ = "/srv/moskollektor/deploy/.env"
ИМЯ = "mos39-nf43"


def внутри():
    """Выполняется в разовом контейнере: поднять api и спросить его с поддельным заголовком."""
    import threading
    import time
    import urllib.error
    import urllib.request

    import uvicorn

    from app.api.main import app

    сервер = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8001, log_level="error"))
    threading.Thread(target=сервер.run, daemon=True).start()
    for _ in range(100):
        if сервер.started:
            break
        time.sleep(0.1)
    req = urllib.request.Request("http://127.0.0.1:8001/api/settings", headers={"X-User-Login": "admin1"})
    try:
        код = urllib.request.urlopen(req, timeout=15).status
    except urllib.error.HTTPError as e:
        код = e.code
    print(f"КОД {код}")


def прогон(ssh, доверие):
    """→ код ответа api в разовом контейнере с AUTH_TRUST_HEADER=доверие."""
    with open(__file__, encoding="utf-8") as f:
        скрипт = f.read()
    # DATABASE_URL берём у рабочего api: compose собирает его из .env подстановкой,
    # в самом .env готовой строки нет.
    команда = (
        f"docker run --rm -i --name {ИМЯ}-{доверие} --network {СЕТЬ} --env-file {ENV_ФАЙЛ}"
        f" -e DATABASE_URL=\"$(docker inspect moskollektor-api-1 -f '{{{{range .Config.Env}}}}{{{{println .}}}}{{{{end}}}}'"
        f" | sed -n 's/^DATABASE_URL=//p')\""
        f" -e AUTH_TRUST_HEADER={доверие} -e AUTH_DEMO_HINTS=0 {ОБРАЗ} python - --inside"
    )
    вывод = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "LogLevel=ERROR", ssh, команда],
        input=скрипт, capture_output=True, text=True, timeout=180,
    )
    строка = next((s for s in вывод.stdout.splitlines() if s.startswith("КОД ")), None)
    if строка is None:
        raise AssertionError(f"разовый контейнер не ответил (код {вывод.returncode}): {вывод.stderr.strip()[-400:]}")
    return int(строка.split()[1])


def main():
    if "--inside" in sys.argv:
        внутри()
        return 0
    ssh = os.environ.get("STAND_SSH")
    if not ssh:
        print("задайте STAND_SSH", file=sys.stderr)
        return 1
    try:
        код_0 = прогон(ssh, 0)
        код_1 = прогон(ssh, 1)
        assert код_1 == 200, f"контроль AUTH_TRUST_HEADER=1: {код_1}, ждали 200 — проверка ничего не доказывает"
        assert код_0 == 401, f"НФ-43: AUTH_TRUST_HEADER=0, заголовок admin1 → {код_0}, ждали 401"
    except AssertionError as e:
        print(f"УПАЛА: {e}")
        return 1
    finally:
        осталось = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "LogLevel=ERROR", ssh,
             f"docker ps -aq --filter name={ИМЯ}"],
            capture_output=True, text=True, timeout=60,
        ).stdout.split()
        if осталось:
            print(f"ВНИМАНИЕ: на стенде остались разовые контейнеры {ИМЯ}: {осталось}")
    print(f"НФ-43: подделка X-User-Login при AUTH_TRUST_HEADER=0 → {код_0}; контроль при 1 → {код_1}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
