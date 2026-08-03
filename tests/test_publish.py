import subprocess
from pathlib import Path

import pytest

from quickpat.publish import publish_vp, PublishError


def _git(args, cwd, check=True):
    return subprocess.run(
        ['git', *args], cwd=str(cwd), capture_output=True, text=True, check=check,
    )


def _write_vp_out(repo: Path):
    """Populate repo/vp-out with a realistic tree, plus a repo-root-only file
    to prove the published tree doesn't leak content from outside vp-out/."""
    vp_out = repo / 'vp-out'
    (vp_out / 'charts' / 'app').mkdir(parents=True)
    (vp_out / 'values-global.yaml').write_text('global:\n  pattern: test\n')
    (vp_out / 'values-prod.yaml').write_text('clusterGroup:\n  name: prod\n')
    (vp_out / 'values-secret.yaml.template').write_text('version: "2.0"\n')
    (vp_out / 'Makefile').write_text('include Makefile-common\n')
    (vp_out / 'pattern.sh').write_text('#!/bin/bash\necho hi\n')
    (vp_out / 'pattern-metadata.yaml').write_text('name: test-pattern\ntier: sandbox\n')
    (vp_out / 'charts' / 'app' / 'Chart.yaml').write_text('name: app\nversion: 0.1.0\n')
    (vp_out / '.gitignore').write_text('values-secret.yaml\n')
    (repo / 'repo-root-only.txt').write_text('should never appear in a published tag\n')
    (repo / 'spec.yaml').write_text('metadata:\n  name: test\n')


@pytest.fixture
def vp_repo(tmp_path):
    """A bare 'remote' repo plus a working clone with a committed vp-out/."""
    remote = tmp_path / 'remote.git'
    work = tmp_path / 'work'
    _git(['init', '--bare', '--initial-branch=main', str(remote)], cwd=tmp_path)
    _git(['clone', str(remote), str(work)], cwd=tmp_path)
    _git(['config', 'user.email', 'test@example.com'], cwd=work)
    _git(['config', 'user.name', 'Test'], cwd=work)

    _write_vp_out(work)
    _git(['add', '-A'], cwd=work)
    _git(['commit', '-m', 'initial'], cwd=work)
    _git(['push', 'origin', 'HEAD:main'], cwd=work)

    return {'remote': remote, 'work': work}


def _remote_tag_names(remote: Path, pattern: str = 'vp-v*'):
    result = _git(['tag', '-l', pattern], cwd=remote)
    return sorted(t for t in result.stdout.splitlines() if t)


def _remote_tree_names(remote: Path, tag: str):
    result = _git(['ls-tree', '--name-only', tag], cwd=remote)
    return set(result.stdout.split())


class TestFirstPublish:
    def test_creates_v1(self, vp_repo):
        result = publish_vp(str(vp_repo['work'] / 'vp-out'))
        assert result.success
        assert not result.no_op
        assert result.version == 1
        assert result.tag == 'vp-v1'
        assert result.pushed
        assert _remote_tag_names(vp_repo['remote']) == ['vp-v1']

    def test_tree_is_root_level(self, vp_repo):
        publish_vp(str(vp_repo['work'] / 'vp-out'))
        names = _remote_tree_names(vp_repo['remote'], 'vp-v1')
        assert 'values-prod.yaml' in names
        assert 'charts' in names
        assert 'Makefile' in names
        assert 'vp-out' not in names
        assert 'repo-root-only.txt' not in names

    def test_gitignored_excluded(self, vp_repo):
        publish_vp(str(vp_repo['work'] / 'vp-out'))
        names = _remote_tree_names(vp_repo['remote'], 'vp-v1')
        assert 'values-secret.yaml' not in names
        assert 'values-secret.yaml.template' in names

    def test_oc_patch_hint_uses_metadata_name(self, vp_repo):
        result = publish_vp(str(vp_repo['work'] / 'vp-out'))
        assert 'test-pattern' in result.oc_patch_cmd
        assert 'vp-v1' in result.oc_patch_cmd


class TestSecondPublish:
    def test_chains_to_previous(self, vp_repo):
        publish_vp(str(vp_repo['work'] / 'vp-out'))
        (vp_repo['work'] / 'vp-out' / 'values-prod.yaml').write_text(
            'clusterGroup:\n  name: prod\n  changed: true\n'
        )
        _git(['add', '-A'], cwd=vp_repo['work'])
        _git(['commit', '-m', 'change'], cwd=vp_repo['work'])
        _git(['push', 'origin', 'HEAD:main'], cwd=vp_repo['work'])

        result = publish_vp(str(vp_repo['work'] / 'vp-out'))
        assert result.version == 2
        assert result.tag == 'vp-v2'
        assert result.parent_tag == 'vp-v1'
        assert _remote_tag_names(vp_repo['remote']) == ['vp-v1', 'vp-v2']

        parent_of_v2 = _git(['rev-parse', 'vp-v2^{commit}^'], cwd=vp_repo['work']).stdout.strip()
        v1_commit = _git(['rev-parse', 'vp-v1^{commit}'], cwd=vp_repo['work']).stdout.strip()
        assert parent_of_v2 == v1_commit

        diff = _git(['diff', 'vp-v1', 'vp-v2'], cwd=vp_repo['work'])
        assert 'changed: true' in diff.stdout

    def test_no_change_is_noop(self, vp_repo):
        publish_vp(str(vp_repo['work'] / 'vp-out'))
        result = publish_vp(str(vp_repo['work'] / 'vp-out'))
        assert result.success
        assert result.no_op
        assert result.tag == 'vp-v1'
        assert _remote_tag_names(vp_repo['remote']) == ['vp-v1']


class TestVersionDerivation:
    def test_derives_from_remote_not_local_count(self, vp_repo):
        # Simulate a pre-existing published version nobody's local repo knows about.
        (vp_repo['work'] / 'vp-out' / 'extra.txt').write_text('x\n')
        _git(['add', '-A'], cwd=vp_repo['work'])
        _git(['commit', '-m', 'extra'], cwd=vp_repo['work'])
        tree = _git(['rev-parse', 'HEAD:vp-out'], cwd=vp_repo['work']).stdout.strip()
        commit = _git(['commit-tree', tree, '-m', 'seed'], cwd=vp_repo['work']).stdout.strip()
        _git(['tag', '-a', 'vp-v5', commit, '-m', 'seed'], cwd=vp_repo['work'])
        _git(['push', 'origin', 'refs/tags/vp-v5'], cwd=vp_repo['work'])
        _git(['tag', '-d', 'vp-v5'], cwd=vp_repo['work'])  # remove local copy

        (vp_repo['work'] / 'vp-out' / 'values-prod.yaml').write_text('changed: yes\n')
        _git(['add', '-A'], cwd=vp_repo['work'])
        _git(['commit', '-m', 'change again'], cwd=vp_repo['work'])
        _git(['push', 'origin', 'HEAD:main'], cwd=vp_repo['work'])

        result = publish_vp(str(vp_repo['work'] / 'vp-out'))
        assert result.version == 6
        assert result.tag == 'vp-v6'


class TestDirtyState:
    def test_dirty_refused_by_default(self, vp_repo):
        (vp_repo['work'] / 'vp-out' / 'values-prod.yaml').write_text('uncommitted: true\n')
        with pytest.raises(PublishError, match='uncommitted'):
            publish_vp(str(vp_repo['work'] / 'vp-out'))

    def test_allow_dirty_publishes_disk_content(self, vp_repo):
        (vp_repo['work'] / 'vp-out' / 'values-prod.yaml').write_text('uncommitted: true\n')
        result = publish_vp(str(vp_repo['work'] / 'vp-out'), allow_dirty=True)
        assert result.success
        diff = _git(['show', f'{result.tag}:values-prod.yaml'], cwd=vp_repo['work'])
        assert 'uncommitted: true' in diff.stdout


class TestErrors:
    def test_missing_vp_out(self, tmp_path):
        with pytest.raises(PublishError, match='not found'):
            publish_vp(str(tmp_path / 'nope'))

    def test_no_remote(self, tmp_path):
        repo = tmp_path / 'lonely'
        repo.mkdir()
        _git(['init', '--initial-branch=main'], cwd=repo)
        _git(['config', 'user.email', 'test@example.com'], cwd=repo)
        _git(['config', 'user.name', 'Test'], cwd=repo)
        _write_vp_out(repo)
        _git(['add', '-A'], cwd=repo)
        _git(['commit', '-m', 'initial'], cwd=repo)

        with pytest.raises(PublishError, match='remote'):
            publish_vp(str(repo / 'vp-out'))

    def test_not_committed(self, vp_repo):
        (vp_repo['work'] / 'vp-out' / 'new-file.txt').write_text('new\n')
        with pytest.raises(PublishError, match='uncommitted'):
            publish_vp(str(vp_repo['work'] / 'vp-out'))


class TestDryRun:
    def test_dry_run_writes_nothing(self, vp_repo):
        result = publish_vp(str(vp_repo['work'] / 'vp-out'), dry_run=True)
        assert result.dry_run
        assert result.tag == 'vp-v1'
        assert _remote_tag_names(vp_repo['remote']) == []
        local_tags = _git(['tag', '-l'], cwd=vp_repo['work']).stdout.split()
        assert local_tags == []
