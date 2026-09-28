# Веса модели v3, работающей на стенде

Эти 25 бустеров LightGBM (`booster_42.txt`…`booster_66.txt`) и `model_meta.json` лежат
в образах стенда `moskollektor/ml:lgbm-v3-bag-2026.09.21` и
`moskollektor/ml-score:lgbm-v3-bag-2026.09.21b`, каталог `/app/models/current`.
В обоих образах файлы побайтно одинаковые. 28.09.2026 их выгрузили в git по MOS-145,
контрольные суммы лежат в `SHA256SUMS`. Проверка: `shasum -a 256 -c SHA256SUMS`.

Модель обучена на горизонт `horizon_h = 720` (30 суток), порог предупреждения 0,63.
Это дефект MOS-219: нужен горизонт 24 часа. Поле `train_code_sha256` в `model_meta.json`
совпадает с SHA-256 трёх файлов `ml-model/autoresearch_v3/` (`train.py`, `prepare.py`,
`prepare_data.py`), значит код обучения этих весов лежит в репозитории без правок.

Прежний `README.md` из образа говорил «веса в git не коммитятся». Он относился к каталогу
`models/current` при сборке образа и здесь заменён этим файлом.
