#!/usr/bin/env python3
"""Контракт каждого GET-метода API: доступ, форма ответа, пустой результат, журнал.
Строки НФ-43 и НФ-77, условия готовности блока Q4 (docs/plan.md).

**Зачем.** Пять условий готовности Q4 до 23.09.2026 проверял только человек,
а методы в блоке прибавляются каждый день: MOS-107, MOS-44, MOS-42 добавят свои.
Проверка, у которой список методов лежит в коде, отстанет от первого же нового
метода и останется зелёной. Поэтому список путей здесь берётся из живого
`/openapi.json` стенда, и новый метод попадает под все четыре условия сам.

**Что меряем по каждому GET из openapi:**
1. без заголовка X-User-Login ответ 401 или 403, а не 200 с данными (НФ-43);
2. под admin1 ответ 200, тело — JSON; если у метода в openapi есть схема
   ответа, у тела есть её обязательные ключи верхнего уровня. Валидатора
   jsonschema в .venv нет, поэтому сверяем только ключи. Схемы в openapi нет
   вовсе — это СБОЙ, а не пропуск: условие готовности Q4 требует схему.
   Закрывает его response_model у метода: MOS-221, у маршрутов MOS-42 — сама MOS-42;
3. запрос с заведомо пустым результатом — даты в 2000 году, offset за краем,
   несуществующий id в фильтре — даёт 200 и пустой список, а не 404 и не 500.
   Метод без параметров-фильтров под условие не попадает, и итог это считает;
4. после каждого вызова, включая отказы из п. 1, в audit.user_action есть
   новая строка с этим методом, путём и кодом ответа (НФ-77). /health —
   наоборот: строки с путём /health быть не должно.

Журнал читаем через GET /api/audit, а не базой: это та же таблица, а
DATABASE_URL не нужен. Чужие запросы идут параллельно, поэтому ищем не «число
строк выросло», а строку с action_id больше запомненного и с нашими method,
path, status_code и логином.

Параметры пути (`{order_id}`, `{section_id}`) берём из живых данных: сперва из
первого элемента родительского списка (`/api/orders` для `/api/orders/{order_id}`,
там поле называется `id`), потом из любого списка, где есть ключ с тем же
именем. Источника нет — это СБОЙ с именем параметра, а не молчаливый пропуск.

Не покрывает: POST и PUT (PUT /api/settings/{key} меняет настройки, звать его
вслепую нельзя) и тело SSE-потока. Поток узнаём по ответу Content-Type
text/event-stream, а не по openapi: /api/alerts/stream объявлен там как
application/json. Тело потока не кончается, читать его — повесить check-all,
поэтому у потока проверяем только п. 1, код 200 и журнал, а в итоге называем
его «не покрыт».

Запуск:
    BASE_URL=https://135.106.216.101 CURL_OPTS=-k .venv/bin/python delivery/check-api-contract.py
    .venv/bin/python delivery/check-api-contract.py --selfcheck
"""

import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

ЛОГИН = os.environ.get("API_LOGIN", "admin1")
ДАЛЕКО_ЗА_КРАЕМ = 10**6
НЕТ_ТАКОГО_ID = 999_999_999
ПУСТОЙ_ДЕНЬ = date(2000, 1, 1)

_ctx = ssl._create_unverified_context() if "-k" in os.environ.get("CURL_OPTS", "").split() else None
# Методы, открытые без входа по договору API MOS-39: подсказка экрана входа нужна
# до того, как человек вошёл. Без логина — 200, а не 401; личность такой метод не
# выясняет, поэтому строка журнала у него без логина, даже если заголовок пришёл.
ОТКРЫТЫЕ = {"/api/auth/info"}
ПОТОК = object()  # тело SSE-потока: не читаем, оно не кончается


def запрос(base, путь, логин=ЛОГИН, params=None):
    """(код, тело или None). Сеть не ответила — код 0."""
    url = base + путь + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"X-User-Login": логин} if логин else {})
    try:
        with urllib.request.urlopen(req, context=_ctx, timeout=120) as r:
            if r.headers.get("Content-Type", "").startswith("text/event-stream"):
                return r.status, ПОТОК
            код, сырое = r.status, r.read()
    except urllib.error.HTTPError as e:
        код, сырое = e.code, e.read()
    except (urllib.error.URLError, TimeoutError) as e:
        return 0, str(e)
    try:
        return код, json.loads(сырое)
    except ValueError:
        return код, None


def записи(тело):
    """Список записей, как бы метод его ни завернул: голым списком, в items или,
    у GeoJSON FeatureCollection, в features. None — это не список."""
    if isinstance(тело, list):
        return тело
    for ключ in ("items", "features"):
        if isinstance(тело, dict) and isinstance(тело.get(ключ), list):
            return тело[ключ]
    return None


def раскрыть(spec, s):
    while "$ref" in s:
        s = spec["components"]["schemas"][s["$ref"].rsplit("/", 1)[1]]
    return s


def схема(spec, op):
    """Схема ответа 200 с раскрытым $ref; {} — схемы нет."""
    s = op.get("responses", {}).get("200", {}).get("content", {}).get("application/json", {}).get("schema", {})
    return раскрыть(spec, s)


def нет_ключей(spec, s, тело):
    """Обязательные ключи верхнего уровня, которых нет в теле.

    anyOf/oneOf (response_model вида `A | B`, у /api/geo/sections — GeoJSON или WKT):
    своего required у такой схемы нет, и без разбора ветвей сверка шла бы вхолостую.
    Тело обязано подойти хотя бы под одну ветвь; не подошло ни под одну — называем
    пропуски той ветви, к которой оно ближе всего.
    """
    ветви = s.get("anyOf") or s.get("oneOf")
    if ветви:
        пропуски = []
        for в in ветви:
            в = раскрыть(spec, в)
            if в.get("type") == "null":
                пропуски.append([] if тело is None else ["<не null>"])
            else:
                пропуски.append(нет_ключей(spec, в, тело))
        return [] if any(not п for п in пропуски) else min(пропуски, key=len)
    if s.get("type") == "array" and isinstance(тело, list):
        if not тело:
            return []
        return нет_ключей(spec, раскрыть(spec, s.get("items", {})), тело[0])
    if not isinstance(тело, dict):
        return ["<тело не объект>"] if s.get("required") else []
    return [k for k in s.get("required", []) if k not in тело]


def формат(p):
    s = p.get("schema", {})
    for вариант in [s] + s.get("anyOf", []):
        if вариант.get("format"):
            return вариант["format"]
    return None


def значение_даты(p, день):
    return f"{день.isoformat()}T00:00:00Z" if формат(p) == "date-time" else день.isoformat()


def обязательные_запроса(op, день):
    """Обязательные query-параметры с допустимыми значениями для обычного вызова."""
    out = {}
    for p in op.get("parameters", []):
        if p["in"] == "query" and p.get("required"):
            if p["name"] == "from":
                out["from"] = значение_даты(p, день - timedelta(days=1))
            elif p["name"] == "to":
                out["to"] = значение_даты(p, день)
            else:
                raise KeyError(f"не знаю, что подставить в обязательный параметр {p['name']}")
    return out


def пустой_запрос(op):
    """Параметры, при которых результат заведомо пуст; None — фильтров нет."""
    имена = {p["name"]: p for p in op.get("parameters", []) if p["in"] == "query"}
    out = {}
    if "from" in имена or "to" in имена:
        for имя in ("from", "to"):
            if имя in имена:
                out[имя] = значение_даты(имена[имя], ПУСТОЙ_ДЕНЬ + timedelta(days=имя == "to"))
    elif "offset" in имена:
        out["offset"] = ДАЛЕКО_ЗА_КРАЕМ
    for имя in имена:
        if имя.endswith("_id"):
            out[имя] = НЕТ_ТАКОГО_ID
    return out or None


def найти_ключ(тело, ключ):
    """Первое значение ключа в первом элементе списка."""
    строки = записи(тело)
    if строки and isinstance(строки[0], dict) and ключ in строки[0]:
        return строки[0][ключ]
    return None


def подставить_путь(путь, списки):
    """{param} → живое значение. списки: {путь списка: тело}."""
    while "{" in путь:
        имя = путь[путь.index("{") + 1 : путь.index("}")]
        родитель = путь[: путь.index("{") - 1]
        значение = None
        if родитель in списки:
            значение = найти_ключ(списки[родитель], имя)
            if значение is None:
                значение = найти_ключ(списки[родитель], "id")
        for тело in списки.values():
            if значение is None:
                значение = найти_ключ(тело, имя)
        if значение is None:
            raise KeyError(f"нет живого значения для {{{имя}}}: ни в {родитель}, ни в одном списке")
        путь = путь.replace("{" + имя + "}", str(значение), 1)
    return путь


def _selfcheck():
    assert записи([]) == [] and записи({"total": 0, "items": []}) == []
    assert записи({"type": "FeatureCollection", "features": []}) == [], "GeoJSON (MOS-45)"
    assert записи({"data_edge": "x"}) is None
    spec = {"components": {"schemas": {"R": {"type": "object", "required": ["a", "b"]}}}}
    op = {"responses": {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/R"}}}}}}
    assert нет_ключей(spec, схема(spec, op), {"a": 1}) == ["b"]
    assert нет_ключей(spec, {"type": "array", "items": {"$ref": "#/components/schemas/R"}}, [{"a": 1, "b": 2}]) == []
    assert нет_ключей(spec, {}, {"что": "угодно"}) == [], "схемы нет — сверять нечего"
    # anyOf, как у /api/geo/sections: GeoSections | WktSections.
    spec["components"]["schemas"]["Geo"] = {"type": "object", "required": ["type", "crs", "features"]}
    spec["components"]["schemas"]["Wkt"] = {"type": "object", "required": ["crs", "items"]}
    гео = {"anyOf": [{"$ref": "#/components/schemas/Geo"}, {"$ref": "#/components/schemas/Wkt"}]}
    assert нет_ключей(spec, гео, {"type": "FeatureCollection", "crs": "x", "features": []}) == []
    assert нет_ключей(spec, гео, {"crs": "x", "items": []}) == [], "подошла вторая ветвь"
    assert нет_ключей(spec, гео, {"type": "FeatureCollection", "crs": "x"}) == ["features"], (
        "без features не подходит ни одна ветвь — называем ближайшую"
    )
    assert нет_ключей(spec, {"anyOf": [{"$ref": "#/components/schemas/R"}, {"type": "null"}]}, None) == []
    assert нет_ключей(spec, {"type": "array", "items": гео}, [{"crs": "x"}]) in (["type", "features"], ["items"])
    op_дат = {"parameters": [
        {"name": "from", "in": "query", "schema": {"anyOf": [{"type": "string", "format": "date-time"}, {"type": "null"}]}},
        {"name": "to", "in": "query", "schema": {"type": "string", "format": "date"}},
        {"name": "offset", "in": "query", "schema": {"type": "integer"}},
    ]}
    assert пустой_запрос(op_дат) == {"from": "2000-01-01T00:00:00Z", "to": "2000-01-02"}
    assert пустой_запрос({"parameters": [{"name": "offset", "in": "query"}]}) == {"offset": ДАЛЕКО_ЗА_КРАЕМ}
    assert пустой_запрос({"parameters": [{"name": "section_id", "in": "query"}]}) == {"section_id": НЕТ_ТАКОГО_ID}
    assert пустой_запрос({"parameters": [{"name": "x-user-login", "in": "header"}]}) is None
    списки = {"/api/orders": {"items": [{"id": 82}]}, "/api/risks": [{"section_id": 1727}]}
    assert подставить_путь("/api/orders/{order_id}", списки) == "/api/orders/82", "родитель, поле id"
    assert подставить_путь("/api/objects/{section_id}/readings", списки) == "/api/objects/1727/readings"
    try:
        подставить_путь("/api/x/{nope}", списки)
    except KeyError:
        pass
    else:
        raise AssertionError("параметр без источника обязан быть СБОЕМ")
    # Поток шлёт пинг и не закрывается: запрос обязан вернуться по заголовку, не читая тело.
    import http.server, threading, time

    class Поток(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.end_headers()
            self.wfile.write(b": ping\n\n")
            self.wfile.flush()
            time.sleep(3)

        def log_message(self, *a):
            pass

    сервер = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Поток)
    threading.Thread(target=сервер.serve_forever, daemon=True).start()
    t = time.time()
    assert запрос(f"http://127.0.0.1:{сервер.server_port}", "/stream") == (200, ПОТОК), "SSE читать нельзя"
    assert time.time() - t < 2, "SSE: запрос ждал конца тела"
    сервер.shutdown()
    print("самопроверка ok: форма списка, ключи схемы, пустой запрос, подстановка пути, SSE-поток")


def main():
    _selfcheck()
    if "--selfcheck" in sys.argv:
        return 0
    base = os.environ.get("BASE_URL")
    if not base:
        print("СБОЙ: не задан BASE_URL")
        return 1

    код, spec = запрос(base, "/openapi.json", логин=None)
    if код != 200 or not isinstance(spec, dict) or "paths" not in spec:
        print(f"СБОЙ: /openapi.json ответил {код}")
        return 1
    методы = [(п, ops["get"]) for п, ops in spec["paths"].items() if "get" in ops]

    # Живые списки для подстановки параметров пути — все GET без {…} и без обязательных query.
    списки = {}
    for путь, op in методы:
        if "{" not in путь and not any(p.get("required") and p["in"] == "query" for p in op.get("parameters", [])):
            к, тело = запрос(base, путь, params={"limit": 1} if any(p["name"] == "limit" for p in op.get("parameters", [])) else None)
            if к == 200 and тело is not ПОТОК:
                списки[путь] = тело

    к, тело = запрос(base, "/api/audit", params={"limit": 1})
    if к != 200 or not записи(тело):
        print(f"СБОЙ: /api/audit под {ЛОГИН} ответил {к} — журнал читать нечем")
        return 1
    отсечка = записи(тело)[0]["action_id"]
    # Строку от /health ищем тоже за отсечкой, поэтому зовём его после неё.
    запрос(base, "/health", логин=ЛОГИН)

    сегодня = date.today()
    вызовы = []  # (path, status, login) — ищем их потом в журнале
    сбои, без_фильтра, потоки = [], [], []

    for шаблон, op in методы:
        if шаблон == "/health":
            continue
        try:
            путь = подставить_путь(шаблон, списки)
            обяз = обязательные_запроса(op, сегодня)
        except KeyError as e:
            сбои.append(f"{шаблон}: {e.args[0]}")
            continue

        # 1. Без заголовка.
        к, тело = запрос(base, путь, логин=None, params=обяз or None)
        вызовы.append((путь, к, None))
        if шаблон in ОТКРЫТЫЕ:
            if к != 200:
                сбои.append(f"{шаблон}: открытый метод без входа ответил {к}, ждали 200")
        elif к not in (401, 403):
            сбои.append(f"{шаблон}: без X-User-Login ответ {к}, ждали 401 или 403 (НФ-43)")

        # 2. С правом.
        к, тело = запрос(base, путь, params=обяз or None)
        вызовы.append((путь, к, None if шаблон in ОТКРЫТЫЕ else ЛОГИН))
        s = схема(spec, op)
        if к != 200:
            сбои.append(f"{шаблон}: под {ЛОГИН} ответ {к}, ждали 200")
        elif тело is ПОТОК:
            потоки.append(шаблон)
            continue
        elif тело is None:
            сбои.append(f"{шаблон}: ответ 200, но тело не JSON")
        elif not s:
            сбои.append(f"{шаблон}: в openapi нет схемы ответа (response_model), ключи сверять не с чем")
        elif нет := нет_ключей(spec, s, тело):
            сбои.append(f"{шаблон}: в ответе нет ключей схемы {нет}")

        # 3. Заведомо пусто.
        пусто = пустой_запрос(op)
        if пусто is None:
            без_фильтра.append(шаблон)
        else:
            к, тело = запрос(base, путь, params={**обяз, **пусто})
            вызовы.append((путь, к, ЛОГИН))
            if к != 200 or записи(тело) != []:
                сбои.append(f"{шаблон}?{urllib.parse.urlencode(пусто)}: ответ {к}, "
                            f"записей {len(записи(тело)) if записи(тело) is not None else 'не список'}, ждали 200 и []")

    # 4. Журнал: каждый вызов оставил строку. Чужой трафик идёт параллельно (фронт
    # опрашивает /api/risks раз в минуту), поэтому берём только строки новее отсечки
    # и сверяем логин: без заголовка login пуст, с ним — ЛОГИН.
    # ponytail: чужой клиент под тем же логином на том же пути в ту же минуту прикроет
    # пропавшую строку; лечится уникальной учёткой для проверки, если это случится.
    строки, offset = [], 0
    while True:
        _, тело = запрос(base, "/api/audit", params={"limit": 1000, "offset": offset})
        страница = записи(тело) or []
        строки += [r for r in страница if r.get("action_id", 0) > отсечка]
        if not страница or страница[-1].get("action_id", 0) <= отсечка:
            break
        offset += len(страница)
    найдено = {}
    for r in строки:
        ключ = (r.get("path"), r.get("status_code"), r.get("login"))
        if r.get("method") == "GET":
            найдено[ключ] = найдено.get(ключ, 0) + 1
    нужно = {}
    for в in вызовы:
        нужно[в] = нужно.get(в, 0) + 1
    for (путь, код_вызова, логин), n in нужно.items():
        есть = найдено.get((путь, код_вызова, логин), 0)
        if есть < n:
            сбои.append(f"журнал: GET {путь} → {код_вызова} от {логин or 'без логина'} вызван {n} раз, "
                        f"строк в audit {есть} (НФ-77)")
    health = [r for r in строки if r.get("path") == "/health"]
    if health:
        сбои.append(f"журнал: /health оставил {len(health)} строк, ждали 0")

    всего = len(методы) - 1
    print(f"методов GET в openapi: {всего} без /health; вызовов {len(вызовы)}, "
          f"пустой результат проверен у {всего - len(без_фильтра) - len(потоки)}, фильтров нет у {len(без_фильтра)}")
    if потоки:
        print(f"ВНИМАНИЕ: тело не проверено у SSE-потоков ({len(потоки)}): {', '.join(потоки)} — только отказ, 200 и журнал")
    for с in сбои:
        print(f"СБОЙ {с}")
    if not сбои:
        print(f"НФ-43, НФ-77 OK: {всего} методов — отказ без заголовка, 200 под {ЛОГИН}, пустое — пусто, журнал пишется")
    return 1 if сбои else 0


if __name__ == "__main__":
    sys.exit(main())
