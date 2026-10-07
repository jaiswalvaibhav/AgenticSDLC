"""Skipped unless the system `dot` binary is installed (dev/demo-only, see diagrams.py)."""
import shutil

import pytest

from sdlc import diagrams

pytestmark = pytest.mark.skipif(shutil.which("dot") is None, reason="graphviz `dot` binary not installed")


def test_each_renderer_returns_png_bytes():
    for render in diagrams.RENDERERS.values():
        data = render()
        assert data.startswith(b"\x89PNG")
