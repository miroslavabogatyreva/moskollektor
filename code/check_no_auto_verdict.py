"""Ф-75 наполовину: worker сам не пишет вердикты в pred.feedback и исходы
в pred.forecast_outcome (US-10, миграция 056).

Строка Ф-75 (docs/acceptance-test.md): «Сервис не должен закрывать инцидент
сам: вердикт по прогнозу ставит человек». Проверка стоит наполовину: она не
доказывает, что человек вердикт поставил, — она доказывает, что его не поставил
автомат. Две половины:

  А (код) — worker не содержит INSERT/UPDATE по pred.feedback или
  pred.forecast_outcome (и записи через
  copy_records_to_table). Если содержит, кто-то вкатил автозакрытие и строка
  Ф-75 рушится. Ищем по всему тексту файла, включая подкаталоги; строки-
  комментарии перед поиском выкидываем, чтобы цитата в комментарии не считалась
  находкой.
  Б (база) — в pred.feedback нет вердиктов от учётки без диспетчерской роли
  (ref.user_role: dispatcher или ods_dispatcher). Строк 0 допустимо (никто ещё
  ничего не решил — честный ноль), а вердикт от техника или администратора —
  это след автоматического писателя, которого в коде мы не нашли.

Ноль в проверке А — не «тихо», а СБОЙ: каталог worker не пустой, если он не
прочитался — мы ничего не проверили, а не проверили всё.

Без DATABASE_URL проверка Б печатает строку ВНИМАНИЕ и пропускается (код 0):
check-all при нулевом коде показывает только строки со словом ВНИМАНИЕ.

Запуск: python3 code/check_no_auto_verdict.py
Самопроверка: python3 code/check_no_auto_verdict.py --selftest
"""

import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "backend" / "app" / "worker"

# Два вида записи в таблицу вердиктов. Оба без учёта регистра, DOTALL — вдруг
# SQL разложен по строкам или отформатирован.
ПИШЕТ_В_ФИДБЕК = re.compile(
    r'(INSERT\s+INTO|UPDATE)\s+pred\."?(feedback|forecast_outcome)"?', re.IGNORECASE | re.DOTALL
)
ПИШЕТ_ЧЕРЕЗ_COPY = re.compile(
    r'copy_records_to_table\(\s*["\'](feedback|forecast_outcome)["\']', re.IGNORECASE | re.DOTALL
)


def имя_файла(файл: Path) -> str:
    """Путь для сообщения: относительно корня, а если файл вне корня
    (selftest работает во временном каталоге) — как есть."""
    try:
        return str(файл.relative_to(ROOT))
    except ValueError:
        return str(файл)


def без_комментариев(текст: str) -> str:
    """Строки, начинающиеся с # (после пробелов), выкидываем, но сохраняем
    перевод строки — иначе номера строк в сообщении о находке съедут."""
    строки = []
    for строка in текст.splitlines(keepends=True):
        if re.match(r"^\s*#", строка):
            строки.append("\n" if строка.endswith("\n") else "")
        else:
            строки.append(строка)
    return "".join(строки)


def проверка_а(каталог: Path) -> tuple[bool, str]:
    """True — чисто. Сообщение всегда печатает N файлов просмотрено."""
    файлы = sorted(каталог.rglob("*.py"))
    n = len(файлы)
    if n == 0:
        return False, f"СБОЙ: в {каталог} нет ни одного .py — проверять нечего, N=0"
    for файл in файлы:
        текст = без_комментариев(файл.read_text(encoding="utf-8"))
        for шаблон in (ПИШЕТ_В_ФИДБЕК, ПИШЕТ_ЧЕРЕЗ_COPY):
            совпадение = шаблон.search(текст)
            if совпадение:
                номер = текст.count("\n", 0, совпадение.start()) + 1
                фрагмент = совпадение.group(0).replace("\n", " ")
                return (
                    False,
                    f"СБОЙ: worker пишет вердикт или исход — {имя_файла(файл)}:{номер}: "
                    f"{фрагмент}",
                )
    return True, f"OK: worker не пишет pred.feedback и pred.forecast_outcome, файлов просмотрено {n}"


def _база(запрос: str):
    """Один запрос через asyncpg — psql на машине проверяющего может не быть
    (образец: code/check_auth.py). Импорты внутри, чтобы без DATABASE_URL и в
    selftest библиотека не требовалась."""
    import asyncio
    import asyncpg

    async def шаг():
        соединение = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            return await соединение.fetchrow(запрос)
        finally:
            await соединение.close()

    return asyncio.run(шаг())


def проверка_б() -> tuple[bool, str]:
    """Считает строки pred.feedback и вердикты от учёток без диспетчерской роли."""
    if not os.environ.get("DATABASE_URL"):
        return True, "ВНИМАНИЕ: ПРОПУСК проверки Б — нет DATABASE_URL"
    запрос = (
        "SELECT count(*) AS total, "
        "count(*) FILTER (WHERE NOT EXISTS ("
        "SELECT 1 FROM ref.user_role r WHERE r.login = f.decided_by "
        "AND r.role_code IN ('dispatcher','ods_dispatcher'))) AS not_dispatcher "
        "FROM pred.feedback f"
    )
    try:
        строка = _база(запрос)
    except Exception as ошибка:
        return False, f"СБОЙ: запрос к базе не выполнился: {ошибка}"
    всего, без_роли = строка["total"], строка["not_dispatcher"]
    if без_роли > 0:
        return (
            False,
            f"СБОЙ: вердикт от учётки без роли диспетчера или диспетчера ОДС: {без_роли}",
        )
    if всего == 0:
        return True, "ВНИМАНИЕ: вердиктов в базе нет, проверять нечего"
    return True, f"OK: pred.feedback — строк {всего}, без роли диспетчера {без_роли}"


# Восемь образцов проверки А: (имя, путь файла относительно временного каталога,
# содержимое, ожидаемый ответ чисто?, маркер, который обязан быть в сообщении).
# Маркер отличает находку от N=0: «пишет» говорит, что нашли именно запись.
СЛУЧАИ = [
    (
        "находка INSERT",
        "bad.py",
        'cur.execute("INSERT INTO pred.feedback VALUES (1)")',
        False,
        "пишет",
    ),
    (
        "чистый UPDATE не по feedback",
        "good.py",
        'cur.execute("UPDATE other.table SET x = 1")',
        True,
        "OK",
    ),
    ("пустой каталог", None, None, False, "N=0"),
    (
        "исход прогноза пишет автомат (US-10)",
        "outcome.py",
        'cur.execute("INSERT INTO pred.forecast_outcome VALUES (1)")',
        False,
        "пишет",
    ),
    (
        "INSERT, разложенный по двум строкам",
        "multi.py",
        'q = """INSERT INTO\n pred.feedback"""',
        False,
        "пишет",
    ),
    (
        "запись через copy_records_to_table",
        "copy.py",
        'copy_records_to_table("feedback", schema_name="pred")',
        False,
        "пишет",
    ),
    (
        "вставка только в комментарии",
        "comment.py",
        "# НЕ делаем INSERT INTO pred.feedback\n",
        True,
        "OK",
    ),
    (
        "файл в подкаталоге",
        "sub/nested.py",
        'cur.execute("INSERT INTO pred.feedback VALUES (1)")',
        False,
        "nested.py",
    ),
]


def selftest() -> int:
    """Восемь образцов: находки, чистые файлы, пустой каталог, подкаталог."""
    всего_ошибок = 0
    for имя, путь, содержимое, ждём_чисто, маркер in СЛУЧАИ:
        with tempfile.TemporaryDirectory(prefix="f75-") as врем:
            каталог = Path(врем)
            if путь is not None:
                файл = каталог / путь
                файл.parent.mkdir(parents=True, exist_ok=True)
                файл.write_text(содержимое, encoding="utf-8")
            чисто, сообщение = проверка_а(каталог)
        подходит = чисто == ждём_чисто and маркер in сообщение
        if подходит:
            print(f"SELFTEST OK: {имя} → «{сообщение}»")
        else:
            ждали = "OK" if ждём_чисто else "СБОЙ"
            всего_ошибок += 1
            print(
                f"SELFTEST СБОЙ: {имя}: ждали {ждали} с маркером «{маркер}», "
                f"получили «{сообщение}»"
            )
    if всего_ошибок:
        print(f"SELFTEST СБОЙ: провалено проверок {всего_ошибок}")
        return 1
    print("SELFTEST OK: восемь образцов дали восемь ожидаемых ответов")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()

    код_возврата = 0
    чисто, сообщение = проверка_а(WORKER)
    print(сообщение)
    if not чисто:
        код_возврата = 1

    чисто, сообщение = проверка_б()
    print(сообщение)
    if not чисто:
        код_возврата = 1

    return код_возврата


if __name__ == "__main__":
    sys.exit(main())
