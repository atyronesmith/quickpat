"""Clone remote quickstart repos into temporary directories."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .registry import resolve_name


def _is_clone_url(path_or_url: str) -> bool:
    return path_or_url.startswith(('https://github.com/', 'git@'))


@contextmanager
def cloned_repo(url: str):
    """Clone a git URL into a temp directory and remove it when done."""
    tmpdir = tempfile.mkdtemp(prefix='quickpat-')
    print(f"Cloning {url}...")
    try:
        subprocess.run(
            ['git', 'clone', '--depth', '1', url, tmpdir],
            check=True, capture_output=True,
        )
        yield tmpdir
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@contextmanager
def resolve_path_ctx(path_or_url: str):
    """Resolve a local path, git URL, or registry name; clean up temp clones."""
    if _is_clone_url(path_or_url):
        with cloned_repo(path_or_url) as path:
            yield path
        return

    if Path(path_or_url).exists():
        yield path_or_url
        return

    try:
        url = resolve_name(path_or_url)
    except (ValueError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    with cloned_repo(url) as path:
        yield path
