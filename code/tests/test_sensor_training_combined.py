"""The UI synthetic toggle has component levels, not a union-score threshold."""
import importlib.util
from pathlib import Path

import numpy as np


def test_combined_level_uses_max_component_level():
    path = Path(__file__).resolve().parents[2] / 'docs/proof/2026-09-28-sensor-model/combined_demo.py'
    spec = importlib.util.spec_from_file_location('sensor_combined_replay', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    artifact = {'modes': {name: {'thresholds': {'high': 0.3, 'watch': 0.1}}
                          for name in ('real', 'sim')}}
    real = np.array([0.4, 0.05, 0.2, 0.05])
    sim = np.array([0.05, 0.2, 0.2, 0.05])
    masks = module.max_level_masks(real, sim, artifact)
    assert masks['high'].tolist() == [True, False, False, False]
    assert masks['watch'].tolist() == [True, True, True, False]
    # p_union=0.36 on row2 does not upgrade two watch components to high.
    assert 1 - (1 - real[2]) * (1 - sim[2]) > 0.3
