"""Корень backend в sys.path: тесты импортируют `app.*` из любого каталога запуска,
без PYTHONPATH — так же, как их запускает образ, где рабочий каталог и есть backend."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
