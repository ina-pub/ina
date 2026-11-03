"""Build the snudown C extension. Upstream's setup.py needs distutils and gperf; this needs neither.

src/html_entities.h is generated from src/html_entities.gperf and committed (see README.md).
"""

from __future__ import annotations

from pathlib import Path

from setuptools import Extension, setup

HERE = Path(__file__).parent
SOURCES = ['snudown.c'] + sorted(
    str(path.relative_to(HERE))
    for folder in ('src', 'html')
    for path in (HERE / folder).glob('*.c')
)

setup(ext_modules=[Extension('snudown', sources=SOURCES, include_dirs=['src', 'html'])])
