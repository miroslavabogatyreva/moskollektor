# Опись выгрузки заказчика: файлы, колонки, периоды, строки, дыры в календаре.
# Числами отсюда живёт раздел «Выгрузка заказчика» в docs/day-one.md.
# Запуск: python3 analysis/inventory.py dataset/ [рабочий_каталог]
# Восемь годовых файлов читает awk (15,9 ГБ, восемь процессов параллельно, ~10 минут),
# результат кладёт в рабочий каталог и при повторном запуске берёт оттуда.
import csv, io, sys, os, subprocess, datetime, collections, re

DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
NUM = re.compile(r'^\d+$')

AWK = r'''BEGIN { FS = "," }
NR == 1 { hdr = $0; next }
{
  n++
  if (NF != 6) { bad++; if (badex == "") badex = $0 }
  d = $3; t = $4; v = $6
  if (mind == "" || d < mind) mind = d
  if (d > maxd) maxd = d
  day[d]++
  ch[$2]++
  if (chmin[$2] == "" || d < chmin[$2]) chmin[$2] = d
  if (d > chmax[$2]) chmax[$2] = d
  al[$5 == "" ? "<пусто>" : $5]++
  if (v == "") emptyv++
  else if (v ~ /^-?[0-9]+(\.[0-9]+)?$/) { numv++; numch[$2]++ }
  else { cat[v]++; catch_[$2]++ }
  if ($1 == "") emptyid++
  if (d == "") emptyd++
  if (t == "") emptyt++
}
END {
  printf "META\trows\t%d\n", n
  printf "META\tbadfields\t%d\n", bad
  printf "META\tbadexample\t%s\n", badex
  printf "META\tmindate\t%s\n", mind
  printf "META\tmaxdate\t%s\n", maxd
  printf "META\tdays_present\t%d\n", length(day)
  printf "META\tchannels\t%d\n", length(ch)
  printf "META\tnumeric_rows\t%d\n", numv
  printf "META\tcategory_rows\t%d\n", n - numv - emptyv
  printf "META\tempty_value\t%d\n", emptyv
  printf "META\tempty_id\t%d\n", emptyid
  printf "META\tempty_date\t%d\n", emptyd
  printf "META\tempty_time\t%d\n", emptyt
  printf "META\tchannels_numeric\t%d\n", length(numch)
  printf "META\tchannels_category\t%d\n", length(catch_)
  for (k in al)  printf "ALARM\t%s\t%d\n", k, al[k]
  for (k in cat) printf "CAT\t%s\t%d\n", k, cat[k]
  for (k in day) printf "DAY\t%s\t%d\n", k, day[k]
  for (k in ch)  printf "CH\t%s\t%d\t%s\t%s\n", k, ch[k], chmin[k], chmax[k]
}
'''

YEARS = range(2019, 2027)


def read_small(path):
    """Три исходных файла заказчика: кавычки и переводы строки внутри поля,
    поэтому разбираем настоящим csv-разбором, а не по \n."""
    raw = open(path, 'rb').read()
    text = raw.decode('utf-8')
    rows = list(csv.reader(io.StringIO(text)))
    return {
        'bytes': len(raw),
        'crlf': raw.count(b'\r\n'),
        'lf_only': raw.count(b'\n') - raw.count(b'\r\n'),
        'bom': raw[:3] == b'\xef\xbb\xbf',
        'header': rows[0],
        'records': len(rows) - 1,
        'physical_lines': raw.count(b'\n'),
        'rows': rows[1:],
    }


def scan_year(dataset, workdir, year):
    """Один проход awk по годовому файлу. Уже посчитанное не пересчитываем."""
    out = os.path.join(workdir, f'{year}.tsv')
    if not (os.path.exists(out) and os.path.getsize(out) > 0):
        src = os.path.join(dataset, f'ext-journal-{year}.csv')
        with open(out, 'w') as fh:
            subprocess.run(['awk', AWK, src], stdout=fh, check=True)
    meta, cat, day, alarm, ch = {}, {}, {}, {}, {}
    for line in open(out, encoding='utf-8'):
        p = line.rstrip('\n').split('\t')
        if p[0] == 'META':
            meta[p[1]] = p[2] if len(p) > 2 else ''
        elif p[0] == 'CAT':
            cat[p[1]] = int(p[2])
        elif p[0] == 'DAY':
            day[p[1]] = int(p[2])
        elif p[0] == 'ALARM':
            alarm[p[1]] = int(p[2])
        elif p[0] == 'CH':
            ch[p[1]] = (int(p[2]), p[3], p[4])
    return meta, cat, day, alarm, ch


def missing_days(days, first, last):
    """Дни календаря, которых нет ни в одном файле, от first до last включительно."""
    d = datetime.date.fromisoformat(first)
    end = datetime.date.fromisoformat(last)
    gaps = []
    while d <= end:
        if d.isoformat() not in days:
            gaps.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return gaps


def runs(dates):
    """Подряд идущие даты сворачиваем в отрезки, чтобы дыры читались."""
    out = []
    for s in dates:
        d = datetime.date.fromisoformat(s)
        if out and d - out[-1][1] == datetime.timedelta(days=1):
            out[-1][1] = d
        else:
            out.append([d, d])
    return [(a.isoformat(), b.isoformat(), (b - a).days + 1) for a, b in out]


def selfcheck():
    # Дыра внутри, дыра на конце, одиночный день.
    have = {'2020-01-01', '2020-01-02', '2020-01-05', '2020-01-09'}
    g = missing_days(have, '2020-01-01', '2020-01-10')
    assert g == ['2020-01-03', '2020-01-04', '2020-01-06', '2020-01-07',
                 '2020-01-08', '2020-01-10'], g
    assert runs(g) == [('2020-01-03', '2020-01-04', 2),
                       ('2020-01-06', '2020-01-08', 3),
                       ('2020-01-10', '2020-01-10', 1)], runs(g)
    # Пустой список дыр — не падать и не выдумывать отрезок.
    assert missing_days({'2020-01-01'}, '2020-01-01', '2020-01-01') == []
    assert runs([]) == []
    # Разбор csv: кавычки, запятая внутри поля, перевод строки внутри поля.
    t = 'a,b\n"1","две, штуки"\n"2","строка\nвторая"\n'
    r = list(csv.reader(io.StringIO(t)))
    assert len(r) == 3 and r[1][1] == 'две, штуки' and '\n' in r[2][1], r
    assert DATE.match('2025-06-30') and not DATE.match('дата')
    assert NUM.match('196780') and not NUM.match('ид_канала_данных')
    print('самопроверка пройдена')


def main():
    selfcheck()
    if len(sys.argv) < 2:
        print('нужен путь к dataset/'); return
    dataset = sys.argv[1]
    workdir = sys.argv[2] if len(sys.argv) > 2 else 'inventory_scan'
    os.makedirs(workdir, exist_ok=True)

    print('\n== ТРИ ИСХОДНЫХ ФАЙЛА ==')
    small = {}
    for name in ['справочник_объектов_диспетчер.csv',
                 'справочник_каналов_датчиков.csv',
                 'журнал_событий_пример.csv']:
        s = read_small(os.path.join(dataset, name))
        small[name] = s
        print(f'{name}: {s["bytes"]} байт, записей {s["records"]}, '
              f'физических строк {s["physical_lines"]}, CRLF {s["crlf"]}, '
              f'одиночных LF {s["lf_only"]}, BOM {s["bom"]}')
        print('   колонки:', ', '.join(s['header']))

    print('\n== ВОСЕМЬ ГОДОВЫХ ФАЙЛОВ ==')
    all_days, all_ch, all_cat, total, total_alarm = {}, {}, collections.Counter(), 0, 0
    junk_rows = {}
    print(f'{"файл":<24}{"строк":>12} {"период по содержимому":<26}'
          f'{"дней":>6}{"каналов":>9}{"тревожных":>11}')
    for y in YEARS:
        meta, cat, day, alarm, ch = scan_year(dataset, workdir, y)
        rows = int(meta['rows']); total += rows
        a = alarm.get('t', 0) + alarm.get('true', 0); total_alarm += a
        print(f'ext-journal-{y}.csv{"":<5}{rows:>12} '
              f'{meta["mindate"]} … {meta["maxdate"]:<13}'
              f'{meta["days_present"]:>6}{meta["channels"]:>9}{a:>11}')
        junk_d = {d: n for d, n in day.items() if not DATE.match(d)}
        junk_c = {c: v[0] for c, v in ch.items() if not NUM.match(c)}
        if junk_d or junk_c:
            print(f'   ПОВТОРНЫЙ ЗАГОЛОВОК: дней-подделок {junk_d}, каналов-подделок {junk_c}')
            junk_rows[y] = sum(junk_d.values())
        for d, n in day.items():
            if not DATE.match(d):
                continue
            all_days[d] = all_days.get(d, 0) + n
        for c, (n, lo, hi) in ch.items():
            if not NUM.match(c):
                continue
            if c in all_ch:
                pn, plo, phi = all_ch[c]
                all_ch[c] = (pn + n, min(plo, lo), max(phi, hi))
            else:
                all_ch[c] = (n, lo, hi)
        all_cat.update({k: v for k, v in cat.items() if k != 'значение_датчика'})
        if int(meta['badfields']):
            print(f'   ПОЛЕЙ НЕ ШЕСТЬ: {meta["badfields"]}, пример {meta["badexample"]}')
        for k in ('empty_value', 'empty_id', 'empty_date', 'empty_time'):
            if int(meta[k]):
                print(f'   пустых {k}: {meta[k]}')

    junk = sum(junk_rows.values())
    print(f'{"ИТОГО строк в файлах":<24}{total:>12}')
    print(f'{"  из них заголовков":<24}{junk:>12}  {junk_rows}')
    print(f'{"ИТОГО данных":<24}{total - junk:>12} каналов разных {len(all_ch)}, тревожных {total_alarm}')

    first, last = min(all_days), max(all_days)
    gaps = missing_days(all_days, first, last)
    print(f'\n== ДЫРЫ В КАЛЕНДАРЕ, {first} … {last} ==')
    span = (datetime.date.fromisoformat(last) - datetime.date.fromisoformat(first)).days + 1
    print(f'дней в периоде {span}, с записями {len(all_days)}, пустых {len(gaps)}')
    for a, b, n in runs(gaps):
        print(f'   нет данных {a} … {b} — {n} дн.')
    thin = sorted((n, d) for d, n in all_days.items())[:10]
    print('десять самых пустых дней:', ', '.join(f'{d} — {n}' for n, d in thin))

    print('\n== ЗНАЧЕНИЯ, КОТОРЫЕ НЕ ЧИСЛА ==')
    print(f'разных текстовых значений: {len(all_cat)}')
    for v, n in all_cat.most_common(40):
        print(f'   {v:<32}{n:>12}')
    bad = all_cat.get('Неисправен', 0)
    print(f'\nНеисправен: {bad} записей')


if __name__ == '__main__':
    main()
