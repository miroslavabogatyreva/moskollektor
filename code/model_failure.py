"""Цель М-18…М-20 в определении модели: контракт contracts/failure.v3.json.

Модель v3 обучена на метке D5, и мерить её надо на ней же, а не на узкой
«Неисправен» из smvu.fault_episode: иначе отказы «Много неисправных устройств»,
которые модель научилась предсказывать, у нас не существуют, и её попадание
в них считается ложной тревогой. Здесь три вещи, которыми пользуется
code/check_metrics.py: чтение контракта, запрос эпизодов модели и склейка
по окну контракта. Всё без базы и без сторонних пакетов — самопроверка
и тесты работают на голом Python.
"""

import json
from pathlib import Path

from predictive_metrics import collector_key, group_incidents

# code/ и contracts/ — соседи и в репозитории, и в образе api (backend/Dockerfile
# кладёт оба в /app), поэтому путь от этого файла верен в обоих местах.
КОНТРАКТ = Path(__file__).resolve().parent.parent / "contracts" / "failure.v3.json"

# Эпизоды модели в окне выборки. Незакрытый эпизод в таблицу попадает, только если
# уже длиннее порога на конец архива (backend/app/ingest/model_failure_episodes.py),
# и модель считает его отказом (keep_open), поэтому условие на длительность —
# только для закрытых. model_version — чтобы эпизоды другой модели не приехали
# в замер молча, если таблицу однажды перестроят под новый контракт.
ЭПИЗОДЫ_МОДЕЛИ = """
SELECT e.channel_id, e.started_at, t2.object_id AS collector_id
  FROM smvu.model_failure_episode e JOIN smvu.channel c USING (channel_id)
  LEFT JOIN smvu.object_tree t3 ON t3.object_id = c.object_id
  LEFT JOIN smvu.object_tree t2 ON t2.object_id = t3.parent_id
 WHERE e.fault_value = ANY($3::text[])
   AND e.model_version = $5
   AND e.started_at >= $1 AND e.started_at < $2
   AND (e.ended_at IS NULL
        OR e.ended_at - e.started_at > make_interval(secs => $4::int))
"""


def загрузить_контракт(путь=КОНТРАКТ) -> dict:
    """Контракт failure.v3 с проверкой полей, от которых зависит замер.

    Падаем на кривом файле, а не берём значения по умолчанию: замер по молча
    подставленному окну склейки дал бы другое число инцидентов без единой ошибки.
    """
    к = json.loads(Path(путь).read_text(encoding="utf-8"))
    assert к.get("schema_version") == "failure.v3", f"{путь}: не failure.v3"
    значения = к["failure_values"]
    assert значения and len(значения) == len(set(значения)), f"{путь}: пустой словарь или дубли"
    assert all(isinstance(з, str) and з.strip() == з and з for з in значения), значения
    порог = к["episode"]["min_duration_seconds"]
    assert isinstance(порог, int) and порог > 0, порог
    склейка = к["incident"]["merge_minutes"]
    assert isinstance(склейка, int) and склейка >= 0, склейка
    assert к["model_version"], f"{путь}: не названа модель"
    return к


def инциденты(отказы, коллектор_канала, окно_мин):
    """Отказы -> инциденты коллектора: та же group_incidents, что у узкой цели."""
    return group_incidents(отказы, window_minutes=окно_мин,
                           group_key=collector_key(коллектор_канала))


def подпись(к: dict) -> str:
    """Одна строка про цель для текста М-18…М-20: что именно мерили."""
    return (f"цель {к['definition_id']} модели {к['model_version']}: "
            f"{len(к['failure_values'])} значения, эпизод длиннее "
            f"{к['episode']['min_duration_seconds'] // 60} мин, "
            f"склейка {к['incident']['merge_minutes']} мин")
