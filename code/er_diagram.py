#!/usr/bin/env python3
"""Рисует ER-диаграмму первой версии базы из самих файлов схемы.

Зачем генерировать, а не рисовать руками: нарисованная руками диаграмма
расходится с кодом на первой же правке, и заметить это некому. Здесь схема
читается из code/schema_*.sql, поэтому диаграмма либо верна, либо падает.

Первая версия базы — это не все 88 таблиц, а те, через которые проходит
путь от события СМВУ до заявки. Остальные — НСИ ТОиР и наряды-допуски,
они нужны, но на диаграмме связей первой версии только мешают.

Связи берутся и из REFERENCES в CREATE TABLE, и из ALTER TABLE ... FOREIGN KEY:
ключи между схемами лежат в schema_xref.sql отдельными ALTER.
"""

import re
import sys
from pathlib import Path

# Ядро первой версии: по этим таблицам идёт расчёт прогноза и рождение заявки.
CORE = [
    "ref.object_xref",
    "smvu.sensor", "smvu.event", "smvu.event_type",
    "feat.section_daily", "feat.permit_window",
    "pred.run", "pred.forecast", "pred.forecast_current", "pred.feedback",
    "ref.feedback_reason",
    "maint.notification", "maint.equipment_life",
    "asset.equipment", "asset.func_location",
    "geo.geo_object", "geo.object_risk", "geo.risk_level", "geo.network_edge",
    "permit.location", "permit.permit",
]

RE_TABLE = re.compile(
    r'^CREATE TABLE (?:IF NOT EXISTS )?([a-z_]+\.[a-z_]+)\s*\((.*?)^\)[^;]*;',
    re.M | re.S)
RE_REF = re.compile(r'REFERENCES\s+([a-z_]+\.[a-z_]+)')
RE_ALTER_FK = re.compile(
    r'ALTER TABLE ([a-z_]+\.[a-z_]+)\s+ADD (?:CONSTRAINT [a-z_]+ )?FOREIGN KEY \(([a-z_]+)\)\s*'
    r'REFERENCES\s+([a-z_]+\.[a-z_]+)', re.S)


def strip_comments(sql):
    return "\n".join(ln.split("--")[0] for ln in sql.splitlines())


def parse(root):
    """Возвращает {таблица: [(колонка, тип, признак ключа)]} и список связей."""
    tables, links = {}, []
    for f in sorted(root.glob("schema_*.sql")):
        sql = strip_comments(f.read_text())
        for name, body in RE_TABLE.findall(sql):
            cols = []
            for line in body.split("\n"):
                line = line.strip().rstrip(",")
                if not line or line.upper().startswith(
                        ("PRIMARY KEY", "UNIQUE", "CHECK", "EXCLUDE", "CONSTRAINT", "FOREIGN")):
                    continue
                m = re.match(r'^([a-z_]+)\s+([a-zA-Z0-9_()," ]+?)(\s|$)', line)
                if not m:
                    continue
                col, typ = m.group(1), m.group(2).strip()
                key = "PK" if "PRIMARY KEY" in line else ("FK" if "REFERENCES" in line else "")
                cols.append((col, typ.split("(")[0].replace(" ", "_"), key))
                for tgt in RE_REF.findall(line):
                    links.append((name, tgt, col))
            tables[name] = cols
        for src, col, tgt in RE_ALTER_FK.findall(sql):
            links.append((src, tgt, col))
    # Колонка, получившая ключ через ALTER, на диаграмме тоже помечается FK.
    fk_cols = {(src, col) for src, _, col in links}
    for name, cols in tables.items():
        tables[name] = [(c, t, k or ("FK" if (name, c) in fk_cols else ""))
                        for c, t, k in cols]
    return tables, links


def mermaid(tables, links, only=None):
    keep = set(only) if only else set(tables)
    out = ["erDiagram"]
    for name in (only or sorted(tables)):
        if name not in tables:
            raise SystemExit(f"нет такой таблицы: {name}")
        out.append(f'    {name.replace(".", "__")} {{')
        for col, typ, key in tables[name][:9]:      # девять колонок, дальше нечитаемо
            out.append(f'        {typ} {col}{" " + key if key else ""}')
        out.append("    }")
    seen = set()
    for src, tgt, col in links:
        if src in keep and tgt in keep and (src, tgt) not in seen:
            seen.add((src, tgt))
            out.append(f'    {tgt.replace(".", "__")} ||--o{{ '
                       f'{src.replace(".", "__")} : "{col}"')
    return "\n".join(out)


def _selfcheck(tables, links):
    assert len(tables) >= 80, f"таблиц найдено мало: {len(tables)}"
    for t in CORE:
        assert t in tables, f"ядро ссылается на несуществующую таблицу {t}"
    fk = [l for l in links if l[0] == "pred.feedback"]
    assert any(tgt == "pred.forecast" for _, tgt, _ in fk), "вердикт не связан с прогнозом"
    assert ("maint.notification", "pred.forecast", "forecast_id") in links, "заявка не связана с прогнозом"
    # На диаграмме ядра не должно остаться таблицы без единой связи.
    core = set(CORE)
    linked = {t for src, tgt, _ in links if src in core and tgt in core for t in (src, tgt)}
    assert core <= linked, f"висят без связи: {sorted(core - linked)}"
    print(f"самопроверка: таблиц {len(tables)}, связей {len(links)}, "
          f"ядро из {len(CORE)} на месте")


if __name__ == "__main__":
    root = Path(__file__).parent
    tables, links = parse(root)
    _selfcheck(tables, links)
    text = mermaid(tables, links, only=CORE)
    out = root.parent / "diagrams" / "er-v1.mmd"
    out.write_text(text + "\n")
    print(f"записано: {out.relative_to(root.parent)}, {len(text.splitlines())} строк")
