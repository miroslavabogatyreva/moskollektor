#!/usr/bin/env python3
"""Produce a portable LOCAL candidate bundle, without changing deployed models."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile

ROOT=Path(__file__).resolve().parents[1]

def main(model,data,out):
    out.mkdir(parents=True,exist_ok=False)
    context=out/'build';context.mkdir()
    for rel in ['src/ml/__init__.py','src/ml/local24_data.py','src/ml/local24_engineering.py',
                'scripts/ml_local24_score.py','requirements/ml-serving.txt','requirements/local24-runtime.txt','Dockerfile.local24']:
        dest=context/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/rel,dest)
    shutil.copytree(ROOT/'src/ml/serving',context/'src/ml/serving',ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(model,context/'model')
    runtime=out/'runtime-data';runtime.mkdir();(runtime/'sources').mkdir()
    meta=json.loads((data/'metadata.json').read_text())
    shutil.copy2(data/'mapping.parquet',runtime/'mapping.parquet')
    for key in ('daily','episodes'):
        source=Path(meta['sources'][key]);source=source if source.is_absolute() else data/source
        rel=f'sources/{key}.parquet';shutil.copy2(source,runtime/rel);meta['sources'][key]=rel
    meta['sources']={k:meta['sources'][k] for k in ('daily','episodes')}
    (runtime/'metadata.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n')
    (out/'README.md').write_text('''# Local24: исследовательский кандидат, не принятая модель

Модель обучена на новый D5-эпизод на пикетном участке в ближайшие 24 часа.
На известном Q1+Q2 2026 она нашла 64 успешных участка из 1770 рекомендаций,
столько же, сколько простое правило недавности. Улучшение качества не доказано.
Расчёт ежедневный в 00:00 Europe/Moscow, только на явно заданном архивном срезе.
Новые live-данные и продуктивное внедрение не входят в доказательства пакета.

Сборка и запуск (из каталога пакета):

```sh
docker build -f build/Dockerfile.local24 -t moskollektor/ml-local24:20260923 build
docker run --rm -p 127.0.0.1:18105:8100 moskollektor/ml-local24:20260923
```

Архивный расчёт; предварительно создайте доступный для записи каталог output:

```sh
mkdir -p output
docker run --rm -v "$PWD/runtime-data:/runtime-data:ro" -v "$PWD/output:/out" --entrypoint python moskollektor/ml-local24:20260923 scripts/ml_local24_score.py --model /app/models/current --data /runtime-data --as-of 2026-06-15 --out /out/score.json
```

GET /model сообщает object_level=section, horizon_h=24. POST /predict требует
точный порядок feature_names; горизонт720 отклоняется. Вклады объясняют средний
сырой логит, не откалиброванную вероятность. История обучения и метрики доступны
в Jira MOS-217/MOS-219. Никакие данные или веса автоматически не публикуются.
''')
    sums=[]
    for f in sorted(out.rglob('*')):
        if f.is_file():sums.append(hashlib.sha256(f.read_bytes()).hexdigest()+'  '+str(f.relative_to(out)))
    (out/'SHA256SUMS').write_text('\n'.join(sums)+'\n')
    archive=out.with_suffix('.tar.gz')
    with tarfile.open(archive,'w:gz') as t:t.add(out,arcname=out.name)
    print(json.dumps({'archive':str(archive),'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'files':len(sums)+1}))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--model',type=Path,required=True);p.add_argument('--data',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();main(a.model,a.data,a.out)
