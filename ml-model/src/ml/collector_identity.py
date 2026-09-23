"""Direct channel -> section -> collector identity from the customer object tree.

The frozen feature code calls its group key ``pfx``. For collector releases this
internal column stores the real collector ID as text; no tag-prefix voting occurs.
"""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import threading

import pandas as pd
from . import config as C

CHANNELS = Path('data/01_raw/dataset_update_20260916/справочник_каналов_датчиков.csv')
OBJECTS = Path('data/01_raw/dataset_20260915/справочник_объектов_диспетчер.csv')
_LOCK = threading.RLock()


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_registry(destination: Path) -> dict:
    channels, objects = pd.read_csv(CHANNELS), pd.read_csv(OBJECTS)
    if channels['ид_канала_данных'].duplicated().any() or objects['ид_объект'].duplicated().any():
        raise ValueError('Channel/object registry contains duplicate IDs')
    joined = channels.merge(objects[['ид_объект', 'родитель']], on='ид_объект', how='left', validate='many_to_one')
    if joined['родитель'].isna().any():
        raise ValueError('Channel section is missing from customer object tree')
    parents = set(joined['родитель'].astype(int))
    if not parents <= set(objects['ид_объект']):
        raise ValueError('Collector parent is missing from customer object tree')
    registry = pd.DataFrame(dict(ch=joined['ид_канала_данных'].astype(int),
                                pfx=joined['родитель'].astype(int).astype(str),
                                stype=joined['тип_датчика'], sys=joined['тип_инж_системы'],
                                section_id=joined['ид_объект'].astype(int)))
    registry.sort_values('ch').to_parquet(destination, index=False)
    return dict(channels=len(registry), collectors=registry.pfx.nunique(),
                channels_source_sha256=sha256(CHANNELS), objects_source_sha256=sha256(OBJECTS),
                registry_sha256=sha256(destination), identity='channel.section_id -> object.parent_id')


@contextmanager
def registry_context(path: Path | None):
    # Frozen builders read C.CHAN. Serialize scoped bindings within this process
    # and restore even on failure; files and frozen research source are untouched.
    with _LOCK:
        original = C.CHAN
        try:
            if path is not None:
                C.CHAN = str(Path(path).resolve())
            yield
        finally:
            C.CHAN = original
