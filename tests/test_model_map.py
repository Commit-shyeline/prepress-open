"""The 3D model ↔ template page maths (prepress/static/model-map.js) is JavaScript; its assertions live in
tests/model_map.test.js and run under Node. This wrapper runs them from pytest, and skips where Node is
not installed rather than failing a Python-only machine.

Run: python3.13 -m pytest tests/test_model_map.py -q
"""
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))


def test_the_mapping_maths_under_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is not installed; the JavaScript tests need it")
    result = subprocess.run([node, os.path.join(HERE, "model_map.test.js")],
                            capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr or result.stdout
    assert "model_map: ok" in result.stdout
