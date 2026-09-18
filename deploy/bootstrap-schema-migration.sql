-- Разовый бутстрап public.schema_migration для стенда, где часть файлов
-- накатана руками ДО того, как появился backend/app/migrate.py (MOS-85,
-- 15.09.2026), либо в обход журнала уже после его появления (013 — так же,
-- см. разбор в docs/plan.md, раздел «Миграции базы»: 012 накатывается через
-- IF NOT EXISTS намеренно — сталкивается с smvu.ensure_partitions() по
-- расписанию, — а не потому что это общее правило для всех файлов).
-- На чистой базе этот файл не нужен: migrate.py сам накатит всё по порядку
-- с нуля. Нужен только здесь и один раз — без него настоящий прогон
-- попробует выполнить CREATE TABLE по уже существующим объектам и упадёт.
--
-- sha256 посчитаны тем же способом, что в migrate.py (sha256 текста файла
-- в кодировке utf-8), 15.09.2026 по содержимому файлов как они лежат
-- в этом коммите. Строка 020_app_setting.sql добавлена 18.09.2026 (MOS-110,
-- Q4.12) по тому же поводу: файл накатан на стенде руками через psql раньше,
-- чем появился в git, — 58 проверила уловом (повтор в транзакции с откатом
-- дал DuplicateTableError), без этой строки следующий migrate попробует
-- накатить его второй раз и уронит api и worker целиком.
CREATE TABLE IF NOT EXISTS public.schema_migration (
    filename   text PRIMARY KEY,
    sha256     text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO public.schema_migration (filename, sha256) VALUES
    ('001_assets.sql', '631022f7ca9d8228ef209bd55250de28dd598166b8c3a7a458f579e2fb8dfcf9'),
    ('002_geo.sql', '9933e86748deab3d8a27fa0a7f2b5e0f13e07078c31c56af74abd0af73b9f7cc'),
    ('003_permits.sql', 'c0109a2178164e1fbb1b46b5f78533e2cbc5d246b8ddec33a4f5a6068872a2b8'),
    ('004_events.sql', 'e0152dd91649d250544acd9fd4308f47c4f044763b417715f0ed446255321ab9'),
    ('005_xref.sql', '55bbf4ba833666157eee1211ad0ac4d3c6ae8abd8b8ebead60455d88acff4b85'),
    ('006_explain_templates.sql', '5bb6e20191b41442c6d4be6e841dc8520adf18ebb741c40c2dce0c0cfaa8c8a9'),
    ('013_setpoints.sql', '7510005622f792daa914cf2a037653ed1198e42e04a1a718d7b3bc07dc181d1c'),
    ('020_app_setting.sql', '23e8a3254110e2a2f138cf41a4752fd9009f2f04990fbe1ed729ecf150d7be66')
ON CONFLICT (filename) DO NOTHING;
