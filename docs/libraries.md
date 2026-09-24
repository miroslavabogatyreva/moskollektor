# Перечень библиотек и лицензий

Приёмка НФ-82 (`docs/acceptance-test.md`): вместе с решением сдаётся перечень всех
использованных библиотек, у каждой названы версия и лицензия, под GPL, LGPL и AGPL
в поставке нет ничего (`docs/HLD.md` разд. 7.3). Четыре набора: `api`, `worker`,
`nginx`, фронт. Пятый контейнер, `ml`, собирает ML-команда — перечень по нему
присылает Николай, здесь не считается.

**Замер 17.09.2026.** Перечень получен командами, не переписан руками:

- `api`, `worker` — `.venv` разработчика (`backend/requirements.txt` ставится
  туда же, откуда его берёт `backend/Dockerfile`), метаданные читает
  `importlib.metadata`: **23 пакета** (16 было 17.09.2026, семь добавила
  MOS-39 24.09.2026 — `python-ldap` и `argon2-cffi` со своими транзитивными).
  Один образ на оба контейнера
  (`docs/HLD.md` разд. 7.1) — набор один и тот же. **Это не сама сборка, а
  приближение к ней** (находка проверяющей 58, 17.09.2026): `.venv` может
  разойтись с образом версией пакета или лишним инструментом разработки.
  `code/check_licenses.py --image <образ>` читает метаданные ВНУТРИ готового
  образа и доказывает это по-настоящему — но образ, с которым сдаём, живёт
  на стенде (`135.106.216.101`), не на машине разработчика: `moskollektor-api`
  и `moskollektor-worker` там пересобраны 17.09.2026 в 13:51, 58 сверила три
  хеша. Прогонять `--image` нужно там, где этот образ доступен докеру
  (по ssh на стенд или после `docker pull`/копии образа) — локальная сборка
  того же `Dockerfile` на чужой машине (например, залежавшийся
  `moskollektor-migrate`) ничего не доказывает про поставку.
- `nginx` — образ `nginx:1.31-alpine` (`docs/HLD.md` разд. 7.1), лицензия сверена
  на [nginx.org/LICENSE](https://nginx.org/LICENSE) 17.09.2026: **1 запись**.
  Пакеты самого Alpine (busybox, musl) не разбираем — за рамками НФ-82.
- фронт — `frontend/node_modules` (то же дерево, что даёт `npm ls --all`),
  `dependencies` и `devDependencies` вместе: **100 пакетов**.

Итого **124 пакета**. Проверка — `python3 code/check_licenses.py`, строка
в `delivery/check-all.sh` под НФ-82.

## Три места, не одно

`docs/HLD.md` разд. 7.3 предупреждает: у `scikit-survival` поле лицензии на PyPI —
`GPL-3.0-or-later`, а список классификаторов пуст, автоматическая проверка
по одним классификаторам такую библиотеку пропустит. Смотрим оба поля метаданных
и файл лицензии:

- **Поле лицензии.** У пакетов Python сейчас идёт переход на PEP 639
  (Metadata-Version 2.4): часть уже пишет SPDX-поле `License-Expression`
  (`asyncpg`, `fastapi`, `pydantic` — проверено 17.09.2026), часть ещё держит
  старое поле `License` (`h11`, `APScheduler`). `code/check_licenses.py` читает
  оба — первый прогон скрипта на реальном venv красил все 16 пакетов из-за
  этого расхождения формата, а не из-за лицензии: находка описана в докстринге
  скрипта и в его самопроверке.
- **Классификаторы** — резервный источник, когда оба поля пустые (старый способ
  до PEP 639). У всех 16 пакетов `api`/`worker` поле и классификаторы совпали,
  расхождений вроде `scikit-survival` не нашлось.
- **Файл `LICENSE` в дистрибутиве** — проверен разовым прогоном
  `pip-licenses --with-license-file` 17.09.2026 (инструмент временный,
  в `.venv` не остался — не входит в `requirements.txt` и не едет в образ):
  пустой либо отсутствующий файл лицензии не нашёлся ни у одного из 16.

Ловушку `scikit-survival` (GPL в поле, пустые классификаторы) сам пакет
не ставим — 274 МБ по замеру `docs/HLD.md` разд. 7.2, он и так не наш
(`docs/toir-libs-verdict.md`). `code/check_licenses.py --selfcheck` подсовывает
такую же запись без установки пакета и доказывает, что она красная.

## `api`, `worker`

| Пакет | Версия | Лицензия |
|---|---|---|
| APScheduler | 3.11.3 | MIT |
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| argon2-cffi | 25.1.0 | MIT |
| argon2-cffi-bindings | 26.1.0 | MIT |
| asyncpg | 0.31.0 | Apache-2.0 |
| cffi | 2.1.1 | MIT-0 |
| click | 8.5.0 | BSD-3-Clause |
| fastapi | 0.141.1 | MIT |
| h11 | 0.16.0 | MIT |
| idna | 3.20 | BSD-3-Clause |
| pyasn1 | 0.6.4 | BSD-2-Clause |
| pyasn1_modules | 0.4.2 | BSD |
| pycparser | 3.0 | BSD-3-Clause |
| pydantic | 2.13.5 | MIT |
| pydantic_core | 2.46.5 | MIT |
| python-ldap | 3.4.8 | python-ldap |
| starlette | 1.6.0 | BSD-3-Clause |
| typing-inspection | 0.4.4 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| tzlocal | 5.4.4 | MIT |
| uvicorn | 0.52.4 | BSD-3-Clause |

**`python-ldap` — «python-ldap», не SPDX-имя.** `code/check_licenses.py` сверяет
строку поля `License` дословно, а PyPI-метаданные пакета несут в этом поле
буквально имя пакета, не название лицензии. По содержанию это лицензия в стиле
Python (классификатор PyPI — `License :: OSI Approved :: Python Software
Foundation License`, `docs/HLD.md` разд. 3.5) — не GPL/LGPL/AGPL, `нарушает()`
не красит её ни по одному из двух источников. Так же у `pyasn1_modules` поле
несёт `BSD`, а не `BSD-2-Clause`, как у самого `pyasn1` — два разных пакета,
два разных значения одного поля, оба не GPL. Найдено 24.09.2026, MOS-39:
первый прогон скрипта после установки семи пакетов дал СБОЙ по обеим строкам —
доказывает, что сверка по факту, а не по тому, что «должно быть».

`ruff` (0.16.8, MIT) в этот список не входит — инструмент разработки, форматирует
код по хуку `.claude/hooks/format.sh`, в образ `api`/`worker` не попадает
(`docs/HLD.md` разд. 7.1.2).

## `nginx`

| Пакет | Версия | Лицензия |
|---|---|---|
| nginx | 1.31 | BSD-2-Clause |

## Фронт

**Проверка этого раздела в `delivery/check-all.sh` помечена «НФ-82 наполовину»
навсегда, не до следующего прогона** (в отличие от `api`/`worker` — там
`--image` со временем закроет строку целиком). Причина: `dist/` — минифицированный
JS без метаданных пакета, заглянуть внутрь готового бандла и проверить лицензию
оттуда нечем, поэтому здесь предмет проверки — дерево `npm ci` (сама стадия
сборки, `docs/HLD.md` разд. 7.4), а не то, что получает заказчик.

`dependencies` и `devDependencies` вместе, 103 пакета. В шипуемый бандл
(`frontend/dist`, копируется в образ `nginx` — `docs/HLD.md` разд. 7.4) из них
попадают только **`preact`** и **`preact-router`**: остальное — инструменты сборки
(Babel, Rolldown, esbuild-цепочка Vite, TypeScript, Tailwind, Prettier),
транспилируют и бандлят код, в собранные файлы сами не попадают; `@playwright/test`
с `playwright` и `playwright-core` (Apache-2.0, с 22.09.2026) гоняют E2E-тесты
из `frontend/e2e/` и в сборку не входят вовсе — та же логика,
что у `ruff`/`prettier` в `docs/HLD.md` разд. 7.1.2.

Одна лицензия семейства копилефт — `lightningcss` и его нативный биндинг,
**MPL-2.0** (двигатель CSS у `@tailwindcss/vite`). Список HLD 7.3 запрещает
GPL, LGPL и AGPL — MPL-2.0 в него не входит, и правило не нарушено, но пакет
и так инструмент сборки, в `dist/` не идёт.

| Пакет | Версия | Лицензия |
|---|---|---|
| @babel/code-frame | 7.29.7 | MIT |
| @babel/compat-data | 7.29.7 | MIT |
| @babel/core | 7.29.7 | MIT |
| @babel/generator | 7.29.8 | MIT |
| @babel/helper-annotate-as-pure | 7.29.7 | MIT |
| @babel/helper-compilation-targets | 7.29.7 | MIT |
| @babel/helper-globals | 7.29.7 | MIT |
| @babel/helper-module-imports | 7.29.7 | MIT |
| @babel/helper-module-transforms | 7.29.7 | MIT |
| @babel/helper-plugin-utils | 7.29.7 | MIT |
| @babel/helper-string-parser | 7.29.7 | MIT |
| @babel/helper-validator-identifier | 7.29.7 | MIT |
| @babel/helper-validator-option | 7.29.7 | MIT |
| @babel/helpers | 7.29.7 | MIT |
| @babel/parser | 7.29.8 | MIT |
| @babel/plugin-syntax-jsx | 7.29.7 | MIT |
| @babel/plugin-transform-react-jsx | 7.29.7 | MIT |
| @babel/plugin-transform-react-jsx-development | 7.29.7 | MIT |
| @babel/template | 7.29.7 | MIT |
| @babel/traverse | 7.29.8 | MIT |
| @babel/types | 7.29.8 | MIT |
| @jridgewell/gen-mapping | 0.3.13 | MIT |
| @jridgewell/remapping | 2.3.5 | MIT |
| @jridgewell/resolve-uri | 3.1.2 | MIT |
| @jridgewell/sourcemap-codec | 1.6.0 | MIT |
| @jridgewell/trace-mapping | 0.3.31 | MIT |
| @oxc-project/types | 0.149.0 | MIT |
| @playwright/test | 1.63.0 | Apache-2.0 |
| @preact/preset-vite | 2.10.6 | MIT |
| @prefresh/babel-plugin | 0.5.4 | MIT |
| @prefresh/core | 1.5.11 | MIT |
| @prefresh/utils | 1.2.1 | MIT |
| @prefresh/vite | 2.4.12 | MIT |
| @rolldown/binding-darwin-arm64 | 1.2.8 | MIT |
| @rolldown/pluginutils | 1.0.1 | MIT |
| @rollup/pluginutils | 5.4.0 | MIT |
| @tailwindcss/node | 4.3.3 | MIT |
| @tailwindcss/oxide | 4.3.3 | MIT |
| @tailwindcss/oxide-darwin-arm64 | 4.3.3 | MIT |
| @tailwindcss/vite | 4.3.3 | MIT |
| @types/estree | 1.0.9 | MIT |
| @typescript/typescript-darwin-arm64 | 7.0.2 | Apache-2.0 |
| babel-plugin-transform-hook-names | 1.0.2 | MIT |
| baseline-browser-mapping | 2.11.24 | Apache-2.0 |
| boolbase | 1.0.0 | ISC |
| browserslist | 4.29.0 | MIT |
| caniuse-lite | 1.0.30001810 | CC-BY-4.0 |
| convert-source-map | 2.0.0 | MIT |
| css-select | 5.2.2 | BSD-2-Clause |
| css-what | 6.2.2 | BSD-2-Clause |
| debug | 4.4.3 | MIT |
| detect-libc | 2.1.2 | Apache-2.0 |
| dom-serializer | 2.0.0 | MIT |
| domelementtype | 2.3.0 | BSD-2-Clause |
| domhandler | 5.0.3 | BSD-2-Clause |
| domutils | 3.2.2 | BSD-2-Clause |
| electron-to-chromium | 1.5.430 | ISC |
| enhanced-resolve | 5.25.1 | MIT |
| entities | 4.5.0 | BSD-2-Clause |
| escalade | 3.2.0 | MIT |
| estree-walker | 2.0.2 | MIT |
| fdir | 6.5.0 | MIT |
| fsevents | 2.3.3 | MIT |
| gensync | 1.0.0-beta.2 | MIT |
| graceful-fs | 4.2.11 | ISC |
| he | 1.2.0 | MIT |
| jiti | 2.7.0 | MIT |
| js-tokens | 4.0.0 | MIT |
| jsesc | 3.1.0 | MIT |
| json5 | 2.2.3 | MIT |
| kolorist | 1.8.0 | MIT |
| lightningcss | 1.32.0 | MPL-2.0 |
| lightningcss-darwin-arm64 | 1.32.0 | MPL-2.0 |
| lru-cache | 5.1.1 | ISC |
| magic-string | 0.30.21 | MIT |
| ms | 2.1.3 | MIT |
| nanoid | 3.3.19 | MIT |
| node-html-parser | 6.1.13 | MIT |
| node-releases | 2.0.55 | MIT |
| nth-check | 2.1.1 | BSD-2-Clause |
| picocolors | 1.1.1 | ISC |
| picomatch | 4.0.7 | MIT |
| playwright | 1.63.0 | Apache-2.0 |
| playwright-core | 1.63.0 | Apache-2.0 |
| postcss | 8.5.28 | MIT |
| preact | 10.29.8 | MIT |
| preact-router | 4.1.2 | MIT |
| prettier | 3.9.7 | MIT |
| rolldown | 1.2.8 | MIT |
| semver | 6.3.1 | ISC |
| simple-code-frame | 1.3.0 | MIT |
| source-map | 0.7.6 | BSD-3-Clause |
| source-map-js | 1.2.1 | BSD-3-Clause |
| stack-trace | 1.0.0 | MIT |
| tailwindcss | 4.3.3 | MIT |
| tapable | 2.3.3 | MIT |
| tinyglobby | 0.2.17 | MIT |
| typescript | 7.0.2 | Apache-2.0 |
| update-browserslist-db | 1.3.3 | MIT |
| vite | 8.3.0 | MIT |
| vite-prerender-plugin | 0.5.13 | MIT |
| yallist | 3.1.1 | ISC |
| zimmerframe | 1.1.5 | MIT |

**Оговорка про платформу.** Список снят на маке (darwin-arm64): нативные биндинги
(`@rolldown/binding-*`, `@tailwindcss/oxide-*`, `lightningcss-*`) на сервере сборки
(`node:24-alpine`, linux) будут другими файлами того же пакета и той же лицензии —
`npm` ставит нативный бинарник под платформу сборки, JS-код и лицензия пакета
от этого не меняются. Ни один из них в `dist/` не попадает (замечание выше).

## Образ `ml`

Собирает ML-команда, репозиторий мы не видим. Перечень по нему присылает Николай —
здесь не выдумываем и не считаем.
