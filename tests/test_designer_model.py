"""The design editor's geometry (prepress/static/designer-model.js) is JavaScript; its assertions live in
tests/designer_model.test.js and run under Node. This wrapper runs them from pytest, and skips where Node
is not installed rather than failing a Python-only machine.

Run: python3.13 -m pytest tests/test_designer_model.py -q
"""
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))


def test_the_geometry_under_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is not installed; the JavaScript tests need it")
    result = subprocess.run([node, os.path.join(HERE, "designer_model.test.js")],
                            capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr or result.stdout
    assert "designer_model: ok" in result.stdout
