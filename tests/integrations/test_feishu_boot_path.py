"""The Feishu facade must not drag the lark SDK into any import path.

``bootstrap.adapters`` registers the Feishu notification adapters, so the
package facade is imported on the boot path. ``lark-oapi`` is a core
dependency, but it is a heavy vendor SDK that no boot-path consumer needs —
importing the facade must stay free of it, or every host that registers the
adapters pays for it at startup.

``integrations.catalog`` is on the same boot-path forbidden list, so the
credential leaf must keep its catalog import function-local.
"""

from __future__ import annotations

import subprocess
import sys


def test_importing_the_feishu_facade_does_not_load_lark() -> None:
    probe = (
        "import sys; "
        "import integrations.feishu; "
        "assert 'lark_oapi' not in sys.modules, 'lark_oapi'; "
        "print('OK: facade clean')"
    )

    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert "OK: facade clean" in completed.stdout


def test_importing_the_credentials_leaf_does_not_load_the_catalog() -> None:
    """`integrations.catalog` is on the boot-path forbidden list; the leaf defers it."""
    probe = (
        "import sys; "
        "from integrations.feishu import credentials; "
        "assert 'integrations.catalog' not in sys.modules, 'integrations.catalog'; "
        "assert 'lark_oapi' not in sys.modules, 'lark_oapi'; "
        "print('OK: leaf clean')"
    )

    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert "OK: leaf clean" in completed.stdout
