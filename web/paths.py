"""Делает ядро prognoz импортируемым без установки пакета.

Добавляет `prognoz/src` в sys.path ДО любого `import prognoz`.
Импортируется первым в app.py. На проде можно `pip install -e ./prognoz` и убрать.
"""
from __future__ import annotations

import sys
from pathlib import Path

# web/paths.py → web/ → prognoz/ → prognoz/src
_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
