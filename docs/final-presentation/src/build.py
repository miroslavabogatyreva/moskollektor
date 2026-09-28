"""Собирает docs/final-presentation/presentation.pptx из шаблона ЛЦТ2026.

Запуск: python build.py <шаблон.pptx> <выход.pptx>. Как править — ../README.md.

Слайды шаблона 7–11 — обязательные, их меняем только текстом (и убираем лишние
карточки на слайде команды). Описательная часть — на макетах шаблона 12–25.
Всё, чего я не знаю (контакты, история команды), — в TEAM, помечено [уточнить].
"""
import copy
import sys
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.util import Inches, Pt
from pptx.enum.text import PP_ALIGN
from pptx.dml.color import RGBColor
from PIL import Image

import os
import tempfile

SRC = os.path.dirname(os.path.abspath(__file__))  # логотип и скриншоты стенда
TPL = sys.argv[1]
OUT = sys.argv[2]
TMP = tempfile.mkdtemp()  # сюда падают обрезанные скриншоты

TEAM = {
    "name": "Скайнет",
    "capt_spec": "[специальность]",
    "about": ["познакомились в СИБУРе на проектах цифровой трансформации и внедрения SAP", "[место работы / учёбы участников]"],
    "city": "Москва",
    "history": ("Мы познакомились в СИБУРе: Николай был руководителем Мирославы. "
                "Вместе вели проекты цифровой трансформации, в том числе внедрение SAP, "
                "и на хакатон пришли уже сработавшейся командой."),
    "members": [
        ("Николай Тлехугов",
         ["Капитан; ML: признаки, обучение модели, метрики, объяснимость",
          "@ntlekhugov", "+7 917 506-25-11", "[место работы / учёбы]"]),
        ("Мирослава Богатырева",
         ["Бэкенд, фронтенд, база, заявки, развёртывание, документы",
          "@miroslavabogatyreva", "+7 916 269-81-55", "[место работы / учёбы]"]),
    ],
}

prs = Presentation(TPL)
S = {i + 1: s for i, s in enumerate(prs.slides)}  # номер шаблона -> слайд

# ---------- порядок: 7–11 обязательные, дальше описательная часть ----------
ORDER = [7, 8, 9, 10, 11, 16, 25, 27, 13, 22, 17, 24, 20, 12]
lst = prs.slides._sldIdLst
ids = list(lst)
for el in ids:
    lst.remove(el)
for n in ORDER:
    lst.append(ids[n - 1])


def ph(slide, idx):
    for sh in slide.placeholders:
        if sh.placeholder_format.idx == idx:
            return sh
    raise KeyError(idx)


def by_name(slide, name, nth=0):
    return [sh for sh in slide.shapes if sh.name == name][nth]


def drop(shape):
    shape._element.getparent().remove(shape._element)


DARK = RGBColor(0x1C, 0x1D, 0x22)
PURPLE = RGBColor(0x52, 0x09, 0x78)
ACCENT = RGBColor(0xFF, 0x00, 0x53)


def set_text(shape, lines, bold_first=False, size=None, color=DARK, bullets=False):
    """Пустой плейсхолдер. Макет красит его бледной подсказкой — цвет ставлю явно."""
    tf = shape.text_frame
    tf.clear()
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        if not bullets:
            pPr = p._p.get_or_add_pPr()
            pPr.set("marL", "0")
            pPr.set("indent", "0")
            pPr.insert(0, pPr.makeelement(
                "{http://schemas.openxmlformats.org/drawingml/2006/main}buNone", {}))
        r = p.add_run()
        r.text = line
        head = bold_first and i == 0
        if head:
            r.font.bold = True
        if size:
            r.font.size = Pt(size)
        if color is not None:
            r.font.color.rgb = PURPLE if head else color


def fill(shape, items, tpl=None):
    """Текст шаблона: копирую абзац-образец, чтобы сохранить его оформление.
    item — строка или (метка, значение); tpl[i] — номер абзаца-образца."""
    txb = shape.text_frame._txBody
    paras = txb.findall("{http://schemas.openxmlformats.org/drawingml/2006/main}p")
    ns = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    new = []
    for i, it in enumerate(items):
        src = paras[tpl[i] if tpl else min(i, len(paras) - 1)]
        p = copy.deepcopy(src)
        runs = p.findall(ns + "r")
        if isinstance(it, tuple):
            if len(runs) < 2:
                r2 = copy.deepcopy(runs[0])
                r2.find(ns + "rPr").attrib.pop("b", None)
                runs[0].addnext(r2)
                runs = p.findall(ns + "r")
            runs[0].find(ns + "t").text = it[0]
            runs[1].find(ns + "t").text = it[1]
            keep = 2
        else:
            runs[0].find(ns + "t").text = it
            keep = 1
        for r in runs[keep:]:
            p.remove(r)
        new.append(p)
    for p in paras:
        txb.remove(p)
    for p in new:
        txb.append(p)


def pill(slide, title, name="Скругленный прямоугольник 1"):
    """Плашка под заголовком: растягиваю под длину текста."""
    try:
        sh = by_name(slide, name)
    except IndexError:
        return
    sh.width = Inches(max(3.4, 0.7 + 0.22 * len(title)))


def title(slide, text, pill_name="Скругленный прямоугольник 1"):
    t = slide.shapes.title
    t.text_frame.text = text
    pill(slide, text, pill_name)


def crop(src, box, dst):
    im = Image.open(f"{SRC}/{src}.png")
    w, h = im.size
    im.crop((int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h))).save(dst)
    return dst


MARK = ACCENT

# ---------- 7. Титульный ----------
s = S[7]
set_text(ph(s, 0), ["Прогноз отказов в коллекторах Москвы"], color=None)
set_text(ph(s, 12), [f"Команда «{TEAM['name']}» · ДЖКХ Москвы, АО «Москоллектор»"], color=None)
logo = ph(s, 11).insert_picture(f"{SRC}/djkh.png")  # логотип постановщика, без обрезки
logo.crop_top = logo.crop_bottom = logo.crop_left = logo.crop_right = 0
logo.left, logo.top, logo.height = Inches(0.45), Inches(0.45), Inches(1.0)
logo.width = int(logo.height * 195 / 70)
s.shapes._spTree.append(logo._element)  # поверх рамки шаблона

# ---------- 8. О команде и решении ----------
s = S[8]
set_text(s.shapes.title, ["О КОМАНДЕ И РЕШЕНИИ"], color=PURPLE)
boxes = [sh for sh in s.shapes if sh.name == "Текст 8"]
by_text = {sh.text_frame.text.split("\n")[0][:12]: sh for sh in boxes}
fill(by_text["В чем суть в"], [
    "Сервис раз в час считает для 3 173 участков коллекторов риск, что датчик "
    "потеряет связь в ближайшие 24 часа, показывает риск на схеме по пикетам "
    "и сам создаёт заявку на осмотр до отказа."])
fill(by_text["Что делает в"], [
    "Модель учится на настоящем журнале СМВУ: 313,5 млн записей за 7,5 лет. "
    "Каждый риск объяснён словами, схема по пикетам — такую заказчик сам "
    "рекомендовал, а стек совпал с его СМВУ: PostgreSQL, Python, nginx, Debian."])
fill(by_text["Капитан: ФИО"], [
    ("Капитан: ", f"Николай Тлехугов, {TEAM['capt_spec']}"),
    ("Кол-во участников: ", "2 человека"),
    "Краткое описание: ",
    TEAM["about"][0], TEAM["about"][1],
    ("Город и регион: ", TEAM["city"]),
], tpl=[0, 1, 2, 3, 4, 5])

# ---------- 9. Команда: две карточки из пяти ----------
s = S[9]
set_text(s.shapes.title, ["СОСТАВ КОМАНДЫ"], color=None)
# карточки слева направо: 16 (с плейсхолдером 27), 55, 58, 61, 64
for card, pic in [("Скругленный прямоугольник 58", "Рисунок 3"),
                  ("Скругленный прямоугольник 61", "Рисунок 4"),
                  ("Скругленный прямоугольник 64", "Рисунок 5")]:
    x = by_name(s, card).left
    for sh in list(s.shapes):
        if sh.name in ("Текст 8", pic) and abs(sh.left - x) < Inches(0.8) and sh.left >= x:
            drop(sh)
    drop(by_name(s, card))
names = sorted([sh for sh in s.shapes if sh.name == "Текст 8" and "Имя" in sh.text_frame.text],
               key=lambda sh: sh.left)
infos = sorted([sh for sh in s.shapes if sh.name == "Текст 8" and "Роль" in sh.text_frame.text],
               key=lambda sh: sh.left)
for (nm, lines), n_sh, i_sh in zip(TEAM["members"], names, infos):
    fill(n_sh, [nm])
    fill(i_sh, lines)
    for para in i_sh.text_frame.paragraphs:  # длинный ник не должен рваться посередине
        if para.runs and para.runs[0].text.startswith("@") and len(para.runs[0].text) > 14:
            para.runs[0].font.size = Pt(10)
    i_sh.width = Inches(2.2)  # карточка 2,4", текст в ней был 2,07"

# ---------- 10. История ----------
s = S[10]
set_text(s.shapes.title, ["ИСТОРИЯ КОМАНДЫ"], color=None)
t10 = {sh.text_frame.text[:14]: sh for sh in s.shapes if sh.has_text_frame}
fill(ph(s, 27), [TEAM["history"]])
ph(s, 27).width = Inches(8.0)  # как у текста пункта 03
fill(t10["Почему вы выбр"], ["Почему выбрали эту задачу"])
fill(t10["Что вас вдохно"], [
    "Настоящие данные: 313,5 млн записей журнала СМВУ за 7,5 лет. "
    "И выход в работу: прогноз полезен, когда из него выходит заявка бригаде."])
fill(t10["С какими основ"], ["Сложности и как мы их прошли"])
fill(t10["Расскажите о с"], [
    "В выгрузке нет координат, актов выездов и истории ремонтов — заказчик "
    "подтвердил, что их не будет. Мы сами определили отказ (серия «Неисправен» "
    "дольше часа), нарисовали схему по пикетам вместо карты, выбросили 2021 год "
    "(53,7 млн строк переходного периода) и ускорили расчёт по парку с 32,7 до 3,6 с."])

# ---------- 11. Коротко о решении ----------
s = S[11]
set_text(ph(s, 38), [
    "Журнал СМВУ → эпизоды отказа → признаки → модель LightGBM → риск для "
    "3 173 участков раз в час.",
    "Python + FastAPI, PostgreSQL 18 + PostGIS, экран на Preact: меньше 150 КБ "
    "JS для старых АРМ диспетчеров.",
    "REST API, роли диспетчера, ОДС и техника; заявки со сроком реакции "
    "16 / 48 / 72 ч по Регламенту.",
    "Поставка — docker compose, пакет разворачивается одной командой.",
], bullets=True)
set_text(ph(s, 42), [
    "Диспетчер ОДС узнаёт, где откажет связь, за сутки до отказа, а не по факту.",
    "Бригада едет по заявке, в которой написано, почему участок в риске.",
    "Дальше — весь парк: около 100 000 датчиков ОПС и ДУ, 825 км коллекторов, "
    "связка с системой заявок заказчика.",
], bullets=True)

# ---------- 16 → Данные заказчика ----------
s = S[16]
title(s, "ДАННЫЕ ЗАКАЗЧИКА", "Скругленный прямоугольник 9")
cards = [
    ("825 км", "коллекторов АО «Москоллектор» — парк, для которого делаем прогноз"),
    ("11 485", "каналов датчиков в выгрузке; в парке около 108 500, у нас 10,6 %"),
    ("313,5 млн", "записей журнала СМВУ за 2019 — июнь 2026, это 7,5 года"),
    ("3 173", "участка по 10 м — единица прогноза; вместе покрывают 31,7 км"),
]
for k, (big, small) in enumerate(cards):
    set_text(ph(s, 49 + k), [f"0{k + 1}"], color=ACCENT)
    set_text(ph(s, 37 + 2 * k), [big], size=24, color=PURPLE)
    set_text(ph(s, 38 + 2 * k), [small])

# ---------- 25 → Как работает прогноз ----------
s = S[25]
title(s, "КАК РАБОТАЕТ ПРОГНОЗ", "Скругленный прямоугольник 11")
steps = [
    ("Журнал СМВУ", "313,5 млн записей в PostgreSQL, секции по времени"),
    ("Отказ — эпизод", "«Неисправен» дольше часа; отказы коллектора за 60 мин — один инцидент"),
    ("Модель", "LightGBM оценивает вероятность отказа участка, раз в час"),
    ("Риск и причина", "3 173 участка по убыванию риска, у каждого — причины словами"),
    ("Заявка", "высокий риск → заявка на осмотр, срок реакции 16, 48 или 72 ч"),
]
for k, (h, b) in enumerate(steps):
    set_text(ph(s, 26 + 2 * k), [h], color=PURPLE)
    set_text(ph(s, 27 + 2 * k), [b])

# ---------- 27 → Продукт: два экрана ----------
s = S[27]
title(s, "ПРОДУКТ: ЖИВОЙ СТЕНД")
ph(s, 14).insert_picture(crop("scr-dashboard", (0, 0, 1, 0.889), f"{TMP}/c-dash.png"))
ph(s, 18).insert_picture(crop("scr-map", (0.19, 0.12, 1, 0.84), f"{TMP}/c-map.png"))
for grp in [sh for sh in s.shapes if sh.shape_type == 6]:
    for sub in grp.shapes:
        if sub.has_text_frame and "lider" in sub.text_frame.text:
            sub.text_frame.paragraphs[0].runs[0].text = "moskollektor.mbogatyreva.ru"
            for r in sub.text_frame.paragraphs[0].runs[1:]:
                r.text = ""
set_text(ph(s, 15), ["Дашборд рисков",
                     "3 173 участка по убыванию вероятности; критический прогноз — "
                     "красной плашкой с кнопкой «Принял»"], bold_first=True)
set_text(ph(s, 16), ["Схема коллектора по пикетам",
                     "вместо карты — так рекомендовал заказчик; масштаб, сдвиг "
                     "по линии, фильтр по риску и типу объекта"], bold_first=True)

# ---------- 13 → Объяснимость ----------
s = S[13]
title(s, "ПОЧЕМУ ТАКОЙ РИСК", "Скругленный прямоугольник 1")
drop(ph(s, 1))
img = crop("scr-object", (0, 0.135, 0.56, 0.51), f"{TMP}/c-obj.png")
s.shapes.add_picture(img, Inches(0.6), Inches(1.75), width=Inches(7.4))
tb = s.shapes.add_textbox(Inches(8.4), Inches(1.9), Inches(4.3), Inches(4.6))
tb.text_frame.word_wrap = True
for i, line in enumerate([
    "Карточка участка объясняет прогноз обычными словами.",
    "Сколько суток прошло с прошлых аварий коллектора, как часто за аварией в течение "
    "месяца шла следующая, сколько каналов стали писать реже — это модель и видит.",
    "Ниже — паспорт участка и история отказов каждого канала: сколько раз, когда "
    "последний, сколько в среднем лежит.",
]):
    p = tb.text_frame.paragraphs[0] if i == 0 else tb.text_frame.add_paragraph()
    p.space_after = Pt(10)
    r = p.add_run()
    r.text = line
    r.font.size = Pt(14)
    r.font.bold = i == 0
    r.font.color.rgb = RGBColor(0x1C, 0x1D, 0x22)

# ---------- 22 → Метрики ----------
s = S[22]
title(s, "МЕТРИКИ")
ch = [sh for sh in s.shapes if sh.has_chart][0].chart
cd = CategoryChartData()
cd.categories = ["Recall: план", "Recall: наш", "Precision: план", "Precision: наш"]
cd.add_series("Значение", (0.5, 0.544, 0.7, 0.643))
ch.replace_data(cd)
va = ch.value_axis
va.minimum_scale, va.maximum_scale, va.major_unit = 0, 1, 0.25
va.tick_labels.number_format, va.tick_labels.number_format_is_linked = "0.00", False
pl = ch.plots[0]
pl.has_data_labels = True
pl.data_labels.number_format, pl.data_labels.number_format_is_linked = "0.000", False
pl.data_labels.font.size = Pt(12)
pairs = [
    ("Precision 0,643", "план заказчика > 0,7; 92 верных из 143 предупреждений"),
    ("Recall 0,544", "план > 0,5; поймано 92 из 169 инцидентов, апрель — июнь 2026"),
    ("3,6 с на весь парк", "расчёт 3 173 участков при норме меньше 5 минут"),
    ("Горизонт 24 часа", "числа выше сняты на модели с горизонтом 30 суток; "
                         "перемер на 24 ч — в работе"),
]
for (h, b), (hi, bi) in zip(pairs, [(21, 18), (22, 23), (24, 25), (26, 27)]):
    set_text(ph(s, hi), [h], color=ACCENT)
    set_text(ph(s, bi), [b])

# ---------- 17 → Почему точность ниже 0,7 ----------
s = S[17]
set_text(s.shapes.title, ["ПОЧЕМУ PRECISION НИЖЕ 0,7"], color=PURPLE)
why = [
    ("Потолок в данных",
     "У 41 % отказов канал молчит все 8 суток до события. Даже оракул, знающий "
     "будущее, не поднимет Recall выше 0,59."),
    ("Хронические места",
     "Три коллектора дают 55–85 % попаданий. Первый отказ после затишья не "
     "предсказать — это 29–45 % инцидентов."),
    ("Малая выборка",
     "На 143 предупреждениях интервал 95 % для Precision — 0,562…0,717: 0,70 и 0,63 "
     "неразличимы. Заказчик разрешил снижать план с обоснованием."),
]
for k, (h, b) in enumerate(why):
    set_text(ph(s, 49 + k), [f"0{k + 1}"], color=ACCENT)
    set_text(ph(s, 37 + 2 * k), [h], color=PURPLE)
    set_text(ph(s, 38 + 2 * k), [b])

# ---------- 24 → Архитектура ----------
s = S[24]
title(s, "АРХИТЕКТУРА")
arch = [
    ["База", "PostgreSQL 18 + PostGIS 3.6, секции по времени; DuckDB поверх Parquet — "
             "для истории и обучающих выборок"],
    ["Сервер", "Python + FastAPI, REST API; планировщик APScheduler с блокировкой "
               "в базе — расчёт не запустится дважды"],
    ["Экран", "Preact + TypeScript + Tailwind, меньше 150 КБ JS. Поставка — docker compose; "
              "стек совпал с СМВУ заказчика"],
]
for idx, lines in zip([26, 31, 32], arch):
    set_text(ph(s, idx), lines, bold_first=True)

# ---------- 20 → Планы развития ----------
s = S[20]
title(s, "ПЛАНЫ РАЗВИТИЯ")
set_text(ph(s, 14), [
    "Сейчас сервис работает на выгрузке: 11 485 каналов, около 10 % парка.",
    "Дальше — живой поток данных и весь парк Москоллектора: 825 км "
    "и около 100 000 датчиков.",
], size=18)
for para in ph(s, 14).text_frame.paragraphs:
    para.alignment = PP_ALIGN.LEFT
    para.space_after = Pt(14)
for k, line in enumerate([
    "Переобучить модель на горизонт 24 ч и перемерить Precision и Recall",
    "Прогноз до отдельного датчика, а не только до участка",
    "Живой поток СМВУ вместо выгрузки, задержка не больше 300 с",
    "Весь парк: около 100 000 датчиков, 178 диспетчерских пунктов",
    "Отметки бригад «ложная тревога» — в дообучение модели",
]):
    set_text(ph(s, 15 + k), [line])

# ---------- 12 → Спасибо ----------
s = S[12]
set_text(s.shapes.title, ["СПАСИБО!"], size=36, color=PURPLE)
set_text(ph(s, 1), [
    "Демо-стенд: moskollektor.mbogatyreva.ru",
    "Демо-учётки — на странице входа",
    "Код: github.com/miroslavabogatyreva/moskollektor",
    "Капитан: Николай Тлехугов",
    "@ntlekhugov · +7 917 506-25-11",
])

# ---------- выбросить слайды шаблона, которых нет в ORDER ----------
keep = {ids[n - 1].rId for n in ORDER}
for n, el in enumerate(ids, 1):
    if el.rId not in keep:
        prs.part.drop_rel(el.rId)

# подсветить всё, что надо уточнить
for sl in prs.slides:
    for sh in sl.shapes:
        if sh.has_text_frame:
            for p in sh.text_frame.paragraphs:
                for r in p.runs:
                    if "[" in r.text:
                        r.font.color.rgb = MARK
prs.save(OUT)
print("ok", OUT, len(prs.slides))
