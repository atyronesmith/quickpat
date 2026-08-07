"""Tests for quickpat.providers.factory — ImportError handling & basics."""

from __future__ import annotations

import builtins
from unittest import mock

import pytest

from quickpat.providers.factory import make_provider


def test_make_provider_none_returns_none():
    assert make_provider({"provider": "none"}) is None


def test_make_provider_empty_returns_none():
    assert make_provider({}) is None


def test_make_provider_missing_provider_returns_none():
    assert make_provider({"provider": ""}) is None


_real_import = builtins.__import__


def _block_import(*packages: str):
    """Return a side_effect for builtins.__import__ that raises ImportError
    for the listed top-level package names and delegates everything else."""

    def _guarded(name, *args, **kwargs):
        top = name.split(".")[0]
        if top in packages:
            raise ImportError(f"No module named '{name}'")
        return _real_import(name, *args, **kwargs)

    return _guarded


@pytest.mark.parametrize(
    "provider_name,blocked_pkg",
    [
        ("openai", "openai"),
        ("anthropic", "anthropic"),
        ("deepinfra", "openai"),
        ("vllm", "openai"),
    ],
)
def test_missing_package_gives_friendly_error(provider_name, blocked_pkg):
    with mock.patch("builtins.__import__", side_effect=_block_import(blocked_pkg)):
        with pytest.raises(ImportError, match=r"pip install 'quickpat\["):
            make_provider({"provider": provider_name})


@pytest.mark.parametrize("provider_name", ["openai", "anthropic", "deepinfra", "vllm"])
def test_friendly_error_mentions_provider(provider_name):
    with mock.patch("builtins.__import__", side_effect=_block_import("openai", "anthropic")):
        with pytest.raises(ImportError, match=provider_name):
            make_provider({"provider": provider_name})


def test_unknown_provider_raises_value_error():
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        make_provider({"provider": "nonexistent"})
