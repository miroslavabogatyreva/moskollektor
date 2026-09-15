#!/usr/bin/env python3
"""Сколько весит ответ дашборда по участкам и геометрия коллекторов.
Меряет JSON/MessagePack/бинарь без сжатия, gzip -9 и brotli -q11.
Нужна консольная утилита brotli. Запуск: python3 payload_budget.py
Числа из этого скрипта приведены в docs/HLD.md, раздел 4 (бюджет фронта и трафика).

ЧИСЛО УЧАСТКОВ ИЗМЕНИЛОСЬ. Раньше здесь стояло 4125 — расчётная оценка «825 км
делить на 200 м». Выгрузка заказчика от 09.09.2026 дала фактические 3173 участка
на 30 коллекторах (backend/app/ingest/tag_to_section.py, разбор пары коллектор-пикет). Все размеры
ответа поэтому меньше прежних примерно на 23 %, и числа в docs/HLD.md разд. 3.4
надо перенести отсюда заново, а не пересчитывать в уме.

ВТОРОЕ, ЧТО ЗДЕСЬ БЫЛО НЕВЕРНО: строка ответа несла одно значение риска на участок.
А pred.forecast хранит прогноз по каждому направлению отдельно, и строка приёмки Ф-27
требует на карте отдельный слой на каждое сдаваемое направление. Значит ответ карты
умножается на число направлений. Замер по обоим случаям ниже: DIRECTIONS = 1 — как
считали раньше, DIRECTIONS = 4 — как будет, если сдаём все четыре направления.

Самопроверка: python3 payload_budget.py --selfcheck. Она не меряет байты (для этого
нужна утилита brotli), а проверяет, что соотношения между способами упаковки
не перевернулись: компактный JSON меньше форматированного, колонки меньше объектов,
бинарь меньше любого JSON, дельта меньше полного кадра."""

import json, gzip, random, struct, subprocess, os, sys, tempfile
random.seed(42)

# Фактическое число участков по выгрузке заказчика, а не расчётное 825 км / 200 м.
N = 3173
# Сколько направлений прогноза показываем на карте. Ф-27: по слою на направление.
DIRECTIONS = 4
D = tempfile.mkdtemp()

statuses = ["ok","warn","alarm","nodata"]
def mkrow(i):
    return {
        "section_id": f"KL-{i//50+1:03d}-{i%50+1:02d}",
        "name": f"Коллектор {i//50+1}, участок {i%50+1}",
        "status": random.choices(statuses, weights=[80,13,2,5])[0],
        "risk_score": round(random.random(), 4),
        "risk_level": random.choices([1,2,3],weights=[80,15,5])[0],
        "updated_at": f"2026-09-12T{random.randint(0,23):02d}:{random.randint(0,59):02d}:{random.randint(0,59):02d}Z",
        "open_alarms": random.choices([0,1,2],weights=[92,6,2])[0],
        "district_id": i % 12 + 1,
    }
rows = [mkrow(i) for i in range(N)]

# 1. verbose JSON: array of objects, pretty
v1 = json.dumps(rows, ensure_ascii=False, indent=2).encode()
# 2. compact JSON array of objects
v2 = json.dumps(rows, ensure_ascii=False, separators=(",",":")).encode()
# 3. compact, ASCII-escaped (default ensure_ascii) — типичная ошибка
v2e = json.dumps(rows, separators=(",",":")).encode()
# 4. columnar: keys once + parallel arrays, без name (имя берётся из справочника)
cols = {
  "ids":[r["section_id"] for r in rows],
  "st":[statuses.index(r["status"]) for r in rows],
  "risk":[r["risk_score"] for r in rows],
  "lvl":[r["risk_level"] for r in rows],
  "al":[r["open_alarms"] for r in rows],
}
v3 = json.dumps(cols, separators=(",",":")).encode()
# 5. минимальный дельта-кадр: только статусы, без id (порядок фиксирован справочником)
v4 = json.dumps({"v":12345,"st":cols["st"],"lvl":cols["lvl"]}, separators=(",",":")).encode()
# 6. бинарно: 1 байт статус+уровень, 1 байт риск(0..255) на секцию
bin6 = bytes()
buf = bytearray()
for r in rows:
    buf.append(statuses.index(r["status"]) | (r["risk_level"]<<2))
    buf.append(int(r["risk_score"]*255))
bin6 = bytes(buf)
# 7. дельта: изменилось 40 участков из 3173
changed = random.sample(range(N), 40)
v7 = json.dumps([{"i":i,"st":cols["st"][i],"r":cols["risk"][i]} for i in changed], separators=(",",":")).encode()
# 10. то же колонками, но риск по каждому направлению отдельно (Ф-27: слой на направление).
# Это ответ карты, а не дашборда: дашборд показывает сводный риск, карта — слои.
dir_cols = dict(cols)
del dir_cols["risk"], dir_cols["lvl"]
for d in range(DIRECTIONS):
    dir_cols[f"risk{d}"] = [round(random.random(), 4) for _ in range(N)]
    dir_cols[f"lvl{d}"] = [random.choices([1, 2, 3], weights=[80, 15, 5])[0] for _ in range(N)]
v10 = json.dumps(dir_cols, separators=(",", ":")).encode()

# минимальный msgpack-энкодер (map/str/int/float/array)
def mp(o):
    if isinstance(o, dict):
        out = bytearray()
        n=len(o)
        out += bytes([0x80|n]) if n<16 else b"\xde"+struct.pack(">H",n)
        for k,v in o.items(): out += mp(k)+mp(v)
        return bytes(out)
    if isinstance(o, list):
        out=bytearray(); n=len(o)
        out += bytes([0x90|n]) if n<16 else (b"\xdc"+struct.pack(">H",n) if n<65536 else b"\xdd"+struct.pack(">I",n))
        for v in o: out += mp(v)
        return bytes(out)
    if isinstance(o, bool): return b"\xc3" if o else b"\xc2"
    if isinstance(o, int):
        if 0<=o<128: return bytes([o])
        if -32<=o<0: return bytes([0xe0|(o+32)])
        return b"\xd2"+struct.pack(">i",o)
    if isinstance(o, float): return b"\xcb"+struct.pack(">d",o)
    if isinstance(o, str):
        b=o.encode(); n=len(b)
        if n<32: return bytes([0xa0|n])+b
        if n<256: return b"\xd9"+bytes([n])+b
        return b"\xda"+struct.pack(">H",n)+b
    raise TypeError(o)
v8 = mp(rows)          # msgpack, тот же массив объектов
v9 = mp(cols)          # msgpack колонками

# ------------------------------------------------------------ самопроверка


def selfcheck():
    """Проверяет не байты, а соотношения: они не должны переворачиваться.

    Абсолютные числа зависят от машины и от версии brotli, и закреплять их ассертом
    бессмысленно. А вот порядок способов упаковки — это и есть вывод скрипта, ради
    которого его читают. Если компактный JSON вдруг окажется больше форматированного,
    сломан замер, а не формат.
    """
    assert N == 3173, "число участков берётся из выгрузки, а не из оценки 825 км / 200 м"
    assert len(rows) == N

    assert len(v2) < len(v1), "компактный JSON обязан быть меньше форматированного"
    assert len(v2) < len(v2e), "UTF-8 меньше, чем \\uXXXX-экранирование кириллицы"
    assert len(v3) < len(v2), "колонки меньше массива объектов: ключи не повторяются"
    assert len(v4) < len(v3), "кадр статусов меньше полных колонок"
    assert len(bin6) < len(v4), "бинарь меньше любого JSON"
    assert len(bin6) == 2 * N, "два байта на участок"
    assert len(v7) < len(v4), "дельта на 40 участков меньше полного кадра"
    assert len(v9) < len(v8), "MessagePack колонками меньше, чем объектами"

    # Главное новое соотношение: карта с четырьмя направлениями тяжелее сводки.
    assert len(v10) > len(v3), "слой на направление не может весить меньше одного риска"
    ratio = len(v10) / len(v3)
    assert 1.5 < ratio < 4.5, f"ответ карты вырос в {ratio:.1f} раза — проверьте DIRECTIONS"

    # gzip обязан сжимать колонки лучше, чем объекты: в колонках однородные значения.
    assert len(gzip.compress(v3, 9)) < len(gzip.compress(v2, 9))

    print(f"selfcheck ok: N={N}, направлений={DIRECTIONS}, "
          f"ответ карты тяжелее сводки в {ratio:.1f} раза")


# Самопроверка идёт ДО замеров: ей не нужна ни утилита brotli, ни минуты работы,
# а нужны только уже собранные байтовые строки. Так её можно гонять на любой машине.
if "--selfcheck" in sys.argv:
    selfcheck()
    sys.exit(0)


def br(data, q=11):
    p=os.path.join(D,"t.bin"); open(p,"wb").write(data)
    subprocess.run(["brotli","-f","-q",str(q),p,"-o",p+".br"],check=True)
    n=os.path.getsize(p+".br"); return n

names = ["1 JSON pretty (объекты, indent=2)","2 JSON compact (объекты, UTF-8)","3 JSON compact с \\uXXXX-экранированием",
         "4 JSON колонками, без имён","5 кадр статусов (только st+lvl)","6 бинарно 2 байта/секцию",
         "7 дельта на 40 изменившихся участков","8 MessagePack (объекты)","9 MessagePack (колонки)",
         f"10 колонками, риск по {DIRECTIONS} направлениям (карта, Ф-27)"]
for nm,d in zip(names,[v1,v2,v2e,v3,v4,bin6,v7,v8,v9,v10]):
    g=len(gzip.compress(d,9)); b=br(d)
    print(f"{nm:48s} raw={len(d):8,d}  gzip={g:7,d}  br11={b:7,d}  байт/секцию_br={b/N:6.2f}")

# --- геометрия ---
import json,gzip,random,os,subprocess,struct
random.seed(7)
D = tempfile.mkdtemp()
def br(d,q=11):
    p=os.path.join(D,"g.bin");open(p,"wb").write(d)
    subprocess.run(["brotli","-f","-q",str(q),p,"-o",p+".br"],check=True)
    return os.path.getsize(p+".br")
def rep(nm,d):
    print(f"{nm:52s} raw={len(d):9,d} gzip={len(gzip.compress(d,9)):8,d} br11={br(d):8,d}")

N = 3173
# участок 200 м ≈ 0.0026 градуса широты; ломаная из 8 точек
feats=[]
lat0,lon0=55.75,37.62
for i in range(N):
    lat=lat0+random.uniform(-0.15,0.15); lon=lon0+random.uniform(-0.25,0.25)
    pts=[]
    for k in range(8):
        pts.append([lon+k*0.00035+random.uniform(-2e-5,2e-5), lat+k*0.00033+random.uniform(-2e-5,2e-5)])
    feats.append({"type":"Feature","id":f"KL-{i//50+1:03d}-{i%50+1:02d}",
      "properties":{"risk":round(random.random(),4),"lvl":random.choice([1,1,1,1,2,3]),"kind":"section"},
      "geometry":{"type":"LineString","coordinates":pts}})
fc={"type":"FeatureCollection","features":feats}
rep(f"GeoJSON {N} участков, 8 точек, полная точность", json.dumps(fc,separators=(",",":")).encode())

def round_coords(o,nd):
    if isinstance(o,float): return round(o,nd)
    if isinstance(o,list): return [round_coords(x,nd) for x in o]
    if isinstance(o,dict): return {k:round_coords(v,nd) for k,v in o.items()}
    return o
for nd in (6,5):
    rep(f"GeoJSON, координаты округлены до {nd} знаков", json.dumps(round_coords(fc,nd),separators=(",",":")).encode())
# упрощение: 8 точек -> 2 точки (концы)
simp=json.loads(json.dumps(fc))
for f in simp["features"]: f["geometry"]["coordinates"]=[f["geometry"]["coordinates"][0],f["geometry"]["coordinates"][-1]]
rep("GeoJSON, упрощено до 2 точек (концы участка), 5 знаков", json.dumps(round_coords(simp,5),separators=(",",":")).encode())
# точки-центроиды (для обзорного зума)
pts={"type":"FeatureCollection","features":[{"type":"Feature","id":f["id"],"properties":{"lvl":f["properties"]["lvl"]},
     "geometry":{"type":"Point","coordinates":[round(f["geometry"]["coordinates"][0][0],5),round(f["geometry"]["coordinates"][0][1],5)]}} for f in feats]}
rep("GeoJSON точки-центроиды, 5 знаков, только уровень", json.dumps(pts,separators=(",",":")).encode())

# честное сравнение бинарь vs JSON: только статус (2 бита) и уровень
buf=bytearray(bytes([random.choices([0,1,2,3],weights=[80,13,2,5])[0]|(random.choices([1,2,3],weights=[80,15,5])[0]<<2) for _ in range(N)]))
rep("бинарь: 1 байт на секцию (статус+уровень)", bytes(buf))
cols={"st":[b&3 for b in buf],"lvl":[b>>2 for b in buf]}
rep("JSON колонками: те же статус+уровень", json.dumps(cols,separators=(",",":")).encode())
