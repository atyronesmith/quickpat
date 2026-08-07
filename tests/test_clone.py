"""Tests for quickpat.clone."""

from pathlib import Path
from unittest.mock import patch

import pytest

from quickpat.clone import cloned_repo, resolve_path_ctx


class TestClonedRepo:
    def test_removes_temp_directory_after_use(self, tmp_path, monkeypatch):
        created = []

        def fake_mkdtemp(**kwargs):
            d = tmp_path / f"clone-{len(created)}"
            d.mkdir()
            created.append(d)
            return str(d)

        monkeypatch.setattr('quickpat.clone.tempfile.mkdtemp', fake_mkdtemp)
        monkeypatch.setattr(
            'quickpat.clone.subprocess.run',
            lambda *args, **kwargs: None,
        )

        clone_dir = created[0] if created else None
        with cloned_repo('https://github.com/example/repo.git') as path:
            clone_dir = Path(path)
            assert clone_dir.is_dir()

        assert not clone_dir.exists()

    def test_removes_temp_directory_on_clone_failure(self, tmp_path, monkeypatch):
        import subprocess

        def fake_mkdtemp(**kwargs):
            d = tmp_path / 'clone-fail'
            d.mkdir()
            return str(d)

        monkeypatch.setattr('quickpat.clone.tempfile.mkdtemp', fake_mkdtemp)

        def fail_clone(*args, **kwargs):
            raise subprocess.CalledProcessError(1, 'git')

        monkeypatch.setattr('quickpat.clone.subprocess.run', fail_clone)

        clone_dir = tmp_path / 'clone-fail'
        with pytest.raises(subprocess.CalledProcessError):
            with cloned_repo('https://github.com/example/repo.git'):
                pass

        assert not clone_dir.exists()


class TestResolvePathCtx:
    def test_yields_local_path_without_cleanup(self, tmp_path):
        qs = tmp_path / 'my-quickstart'
        qs.mkdir()

        with resolve_path_ctx(str(qs)) as path:
            assert path == str(qs)

        assert qs.is_dir()

    def test_clones_registry_name_and_cleans_up(self, tmp_path, monkeypatch):
        created = []

        def fake_mkdtemp(**kwargs):
            d = tmp_path / 'registry-clone'
            d.mkdir()
            created.append(d)
            return str(d)

        monkeypatch.setattr('quickpat.clone.tempfile.mkdtemp', fake_mkdtemp)
        monkeypatch.setattr(
            'quickpat.clone.resolve_name',
            lambda name: 'https://github.com/example/RAG.git',
        )
        monkeypatch.setattr(
            'quickpat.clone.subprocess.run',
            lambda *args, **kwargs: None,
        )

        clone_dir = tmp_path / 'registry-clone'
        with resolve_path_ctx('RAG') as path:
            assert path == str(clone_dir)

        assert not clone_dir.exists()
