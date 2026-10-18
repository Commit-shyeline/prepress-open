"""The WordPress connector's hand-off reader (wordpress/prepress-open-connector/connector.js) is JavaScript;
its assertions live in tests/connector.test.js and run under Node. This wrapper runs them from pytest, and
skips where Node is not installed rather than failing a Python-only machine.

Run: python3.13 -m pytest tests/test_connector.py -q
"""
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))


def test_the_hand_off_reader_under_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is not installed; the JavaScript tests need it")
    result = subprocess.run([node, os.path.join(HERE, "connector.test.js")],
                            capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr or result.stdout
    assert "connector: ok" in result.stdout
