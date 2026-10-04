"""Shared pytest fixtures.

The HuggingFace / Diffusers provider tests mock the heavy model objects, so
they must not depend on whether `transformers` / `diffusers` happen to be
installed on the machine running the suite. This fixture marks those optional
libraries as "available" for every test; the one test that checks the
not-installed error path patches the flag back to False itself.
"""

import pytest


@pytest.fixture(autouse=True)
def _optional_ml_libs_marked_available(monkeypatch):
    import src.providers.diffusers_provider as dp
    import src.providers.huggingface_provider as hp

    monkeypatch.setattr(hp, "_TRANSFORMERS_AVAILABLE", True)
    monkeypatch.setattr(dp, "_DIFFUSERS_AVAILABLE", True)
