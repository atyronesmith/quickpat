"""Eval test harness configuration and fixtures.

Provides quickstart caching, provider probing, and dynamic parametrization
for the quickstart x provider evaluation matrix.
"""

import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

# Ensure quickpat is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from quickpat.config import get as cfg
from quickpat.registry import fetch_registry

CACHE_DIR = Path.home() / ".cache" / "quickpat" / "quickstarts"
DEFAULT_RESULTS_DIR = str(Path(__file__).resolve().parent / "results")


def pytest_addoption(parser):
    parser.addoption(
        "--quickstart", action="store", default=None,
        help="Filter to a single quickstart name (substring match)",
    )
    parser.addoption(
        "--provider", action="store", default=None,
        help="Filter to a single provider name",
    )
    parser.addoption(
        "--eval-results-dir", action="store", default=DEFAULT_RESULTS_DIR,
        help="Directory for JSONL result files",
    )
    parser.addoption(
        "--no-cache", action="store_true", default=False,
        help="Force re-clone of quickstart repos",
    )


# ── Provider probing ─────────────────────────────────────────────


def _probe_local_server(base_url):
    """Quick HTTP probe for a local server."""
    try:
        # Try /v1/models for vLLM or /api/tags for Ollama
        for path in ["/v1/models", "/api/tags", "/"]:
            try:
                urllib.request.urlopen(base_url + path, timeout=2)
                return True
            except Exception:
                continue
        return False
    except Exception:
        return False


def probe_providers():
    """Discover available LLM providers. 'none' is always available."""
    from quickpat.providers import make_provider

    providers = [("none", "deterministic", None)]

    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            model = cfg("llm.anthropic.model", "claude-sonnet-4-20250514")
            providers.append(("anthropic", model, make_provider({"provider": "anthropic"})))
        except Exception:
            pass

    if os.environ.get("OPENAI_API_KEY"):
        try:
            model = cfg("llm.openai.model", "gpt-4o-mini")
            providers.append(("openai", model, make_provider({"provider": "openai"})))
        except Exception:
            pass

    if os.environ.get("DEEPINFRA_API_KEY"):
        try:
            model = cfg("llm.deepinfra.model", "Qwen/Qwen2.5-72B-Instruct")
            providers.append(("deepinfra", model, make_provider({"provider": "deepinfra"})))
        except Exception:
            pass

    # Local servers
    ollama_url = cfg("llm.ollama.base_url", "http://localhost:11434")
    if _probe_local_server(ollama_url):
        try:
            model = cfg("llm.ollama.model", "llama3.1")
            providers.append(("ollama", model, make_provider({"provider": "ollama"})))
        except Exception:
            pass

    vllm_url = cfg("llm.vllm.base_url", "http://localhost:8000")
    if _probe_local_server(vllm_url):
        try:
            model = cfg("llm.vllm.model", "default")
            providers.append(("vllm", model, make_provider({"provider": "vllm"})))
        except Exception:
            pass

    return providers


# ── Quickstart caching ───────────────────────────────────────────


def ensure_quickstart(name, url, no_cache=False):
    """Clone a quickstart repo to the cache directory. Returns local path."""
    dest = CACHE_DIR / name
    if dest.exists() and not no_cache:
        return dest
    if dest.exists():
        import shutil
        shutil.rmtree(dest)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--depth", "1", url, str(dest)],
        check=True, capture_output=True,
    )
    return dest


def fetch_quickstarts(name_filter=None, no_cache=False):
    """Fetch registry and clone/cache all quickstarts. Returns list of (name, path)."""
    registry = fetch_registry()
    quickstarts = []
    for entry in registry:
        name = entry["name"]
        url = entry.get("url")
        if not url:
            continue
        if name_filter and name_filter.lower() not in name.lower():
            continue
        try:
            path = ensure_quickstart(name, url, no_cache=no_cache)
            quickstarts.append((name, str(path)))
        except Exception as e:
            # Skip quickstarts that fail to clone
            print(f"Warning: failed to clone {name}: {e}", file=sys.stderr)
    return quickstarts


# ── Dynamic parametrization ──────────────────────────────────────


def pytest_generate_tests(metafunc):
    """Create the quickstart x provider test matrix."""
    if "quickstart_name" not in metafunc.fixturenames:
        return

    name_filter = metafunc.config.getoption("--quickstart", default=None)
    provider_filter = metafunc.config.getoption("--provider", default=None)
    no_cache = metafunc.config.getoption("--no-cache", default=False)

    # Fetch quickstarts
    quickstarts = fetch_quickstarts(name_filter=name_filter, no_cache=no_cache)
    if not quickstarts:
        pytest.skip("No quickstarts available")

    # Probe providers
    providers = probe_providers()
    if provider_filter:
        providers = [p for p in providers if p[0] == provider_filter]
        if not providers:
            pytest.skip(f"Provider '{provider_filter}' not available")

    # Build matrix
    argnames = [
        "quickstart_name", "quickstart_path",
        "provider_name", "model_name", "llm_callable",
    ]
    argvalues = []
    ids = []
    for qs_name, qs_path in quickstarts:
        for prov_name, model, llm_fn in providers:
            argvalues.append((qs_name, qs_path, prov_name, model, llm_fn))
            ids.append(f"{qs_name}-{prov_name}")

    metafunc.parametrize(argnames, argvalues, ids=ids)


@pytest.fixture
def eval_results_dir(request):
    """Directory for writing JSONL result files."""
    d = request.config.getoption("--eval-results-dir", DEFAULT_RESULTS_DIR)
    Path(d).mkdir(parents=True, exist_ok=True)
    return d
