"""Loaded by ``tia record`` via ``-p tia._xdist`` so xdist workers record.

A separate module (not ``tia.plugin`` itself) because the controller has
already imported ``tia.plugin``; naming it in ``-p`` would make pytest warn
that it can't assertion-rewrite an already-imported module.
"""

from tia.plugin import worker_configure as pytest_configure  # noqa: F401
