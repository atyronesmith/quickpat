"""Tests for skills.skill_validate."""

import sys
from pathlib import Path

import yaml
import pytest

# skill_validate imports from quickpat, so make sure both are on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills"))

from skill_validate import validate, validate_and_fix


def _make_valid_pattern(path):
    """Create a minimal valid pattern directory."""
    path.mkdir(parents=True, exist_ok=True)

    # values-global.yaml
    with open(path / "values-global.yaml", "w") as f:
        f.write("---\n")
        yaml.dump({
            "global": {"pattern": "test"},
            "main": {
                "clusterGroupName": "hub",
                "multiSourceConfig": {
                    "enabled": True,
                    "clusterGroupChartVersion": "0.9.*",
                },
            },
        }, f, sort_keys=False)

    # values-hub.yaml
    with open(path / "values-hub.yaml", "w") as f:
        yaml.dump({
            "clusterGroup": {
                "name": "hub",
                "isHubCluster": True,
                "namespaces": ["vault"],
                "subscriptions": {},
                "projects": ["hub"],
                "sharedValueFiles": [
                    "/overrides/values-{{ $.Values.global.clusterPlatform }}.yaml"
                ],
                "applications": {
                    "vault": {
                        "name": "vault",
                        "namespace": "vault",
                        "project": "hub",
                        "chart": "hashicorp-vault",
                        "chartVersion": "0.1.*",
                    },
                    "myapp": {
                        "name": "myapp",
                        "namespace": "myapp",
                        "project": "hub",
                        "path": "charts/all/myapp",
                    },
                },
            }
        }, f, sort_keys=False)

    # values-secret.yaml.template
    with open(path / "values-secret.yaml.template", "w") as f:
        yaml.dump({
            "version": "2.0",
            "secrets": [{
                "name": "test-secrets",
                "vaultPrefixes": ["global"],
                "fields": [{"name": "password", "onMissingValue": "prompt"}],
            }],
        }, f, sort_keys=False)

    # Makefile
    (path / "Makefile").write_text("include Makefile-common\n")
    (path / "Makefile-common").write_text("# rhvp.cluster_utils\n")

    # pattern.sh
    ps = path / "pattern.sh"
    ps.write_text("#!/bin/bash\nutility-container\n")
    ps.chmod(0o755)

    # ansible.cfg
    (path / "ansible.cfg").write_text("[defaults]\n")

    # overrides
    overrides = path / "overrides"
    overrides.mkdir()
    for platform in ("AWS", "Azure", "GCP", "IBMCloud", "None"):
        (overrides / f"values-{platform}.yaml").write_text(f"# {platform}\n")


class TestValidateClean:
    def test_valid_pattern_passes(self, tmp_path):
        pat = tmp_path / "pattern"
        _make_valid_pattern(pat)
        result = validate(str(pat))
        assert result.valid is True
        assert len(result.issues) == 0

    def test_missing_dir_fails(self, tmp_path):
        result = validate(str(tmp_path / "nonexistent"))
        assert result.valid is False


class TestValidateDetectsErrors:
    def test_missing_required_file(self, tmp_path):
        pat = tmp_path / "pattern"
        _make_valid_pattern(pat)
        (pat / "Makefile").unlink()
        result = validate(str(pat))
        assert not result.valid
        assert any("Makefile" in i.file for i in result.issues)

    def test_main_nested_under_global(self, tmp_path):
        pat = tmp_path / "pattern"
        _make_valid_pattern(pat)
        with open(pat / "values-global.yaml", "w") as f:
            f.write("---\n")
            yaml.dump({
                "global": {
                    "pattern": "test",
                    "main": {"clusterGroupName": "hub", "multiSourceConfig": {"enabled": True}},
                },
            }, f, sort_keys=False)
        result = validate(str(pat))
        assert any("nested under global" in i.message for i in result.issues)

    def test_multisource_disabled(self, tmp_path):
        pat = tmp_path / "pattern"
        _make_valid_pattern(pat)
        with open(pat / "values-global.yaml", "w") as f:
            f.write("---\n")
            yaml.dump({
                "global": {"pattern": "test"},
                "main": {"clusterGroupName": "hub",
                         "multiSourceConfig": {"enabled": False}},
            }, f, sort_keys=False)
        result = validate(str(pat))
        assert any("multiSourceConfig.enabled" in i.message for i in result.issues)

    def test_legacy_makefile_include(self, tmp_path):
        pat = tmp_path / "pattern"
        _make_valid_pattern(pat)
        (pat / "Makefile").write_text("include common/Makefile\n")
        result = validate(str(pat))
        assert any("common/Makefile" in i.message for i in result.issues)

    def test_secret_wrong_version(self, tmp_path):
        pat = tmp_path / "pattern"
        _make_valid_pattern(pat)
        with open(pat / "values-secret.yaml.template", "w") as f:
            yaml.dump({
                "version": "1.0",
                "secrets": [{"name": "test", "vaultPrefixes": ["global"], "fields": []}],
            }, f, sort_keys=False)
        result = validate(str(pat))
        assert any("version" in i.message.lower() for i in result.issues)

    def test_secret_vault_prefix_override(self, tmp_path):
        pat = tmp_path / "pattern"
        _make_valid_pattern(pat)
        with open(pat / "values-secret.yaml.template", "w") as f:
            yaml.dump({
                "version": "2.0",
                "secrets": [{"name": "test", "vaultPrefixOverride": "global", "fields": []}],
            }, f, sort_keys=False)
        result = validate(str(pat))
        assert any("vaultPrefixOverride" in i.message for i in result.issues)

    def test_pattern_sh_not_executable(self, tmp_path):
        pat = tmp_path / "pattern"
        _make_valid_pattern(pat)
        (pat / "pattern.sh").chmod(0o644)
        result = validate(str(pat))
        assert any("not executable" in i.message for i in result.issues)

    def test_missing_overrides(self, tmp_path):
        pat = tmp_path / "pattern"
        _make_valid_pattern(pat)
        import shutil
        shutil.rmtree(pat / "overrides")
        result = validate(str(pat))
        assert any("overrides" in i.message.lower() for i in result.issues)


class TestValidateAndFix:
    def test_fixes_main_nesting(self, tmp_path):
        pat = tmp_path / "pattern"
        _make_valid_pattern(pat)
        with open(pat / "values-global.yaml", "w") as f:
            f.write("---\n")
            yaml.dump({
                "global": {
                    "pattern": "test",
                    "main": {"clusterGroupName": "hub",
                             "multiSourceConfig": {"enabled": True,
                                                   "clusterGroupChartVersion": "0.9.*"}},
                },
            }, f, sort_keys=False)
        result = validate_and_fix(str(pat))
        assert result.valid is True
        assert result.fixes_applied > 0

    def test_fixes_multisource_disabled(self, tmp_path):
        pat = tmp_path / "pattern"
        _make_valid_pattern(pat)
        with open(pat / "values-global.yaml", "w") as f:
            f.write("---\n")
            yaml.dump({
                "global": {"pattern": "test"},
                "main": {"clusterGroupName": "hub",
                         "multiSourceConfig": {"enabled": False}},
            }, f, sort_keys=False)
        result = validate_and_fix(str(pat))
        assert result.valid is True
        # Verify the file was actually fixed
        with open(pat / "values-global.yaml") as f:
            data = yaml.safe_load(f)
        assert data["main"]["multiSourceConfig"]["enabled"] is True

    def test_fixes_legacy_makefile(self, tmp_path):
        pat = tmp_path / "pattern"
        _make_valid_pattern(pat)
        (pat / "Makefile").write_text("include common/Makefile\n")
        result = validate_and_fix(str(pat))
        assert result.valid is True
        assert "include Makefile-common" in (pat / "Makefile").read_text()

    def test_fixes_secret_version(self, tmp_path):
        pat = tmp_path / "pattern"
        _make_valid_pattern(pat)
        with open(pat / "values-secret.yaml.template", "w") as f:
            yaml.dump({
                "secrets": [{"name": "test", "vaultPrefixes": ["global"], "fields": []}],
            }, f, sort_keys=False)
        result = validate_and_fix(str(pat))
        assert result.valid is True
        with open(pat / "values-secret.yaml.template") as f:
            data = yaml.safe_load(f)
        assert data["version"] == "2.0"

    def test_fixes_vault_prefix_override(self, tmp_path):
        pat = tmp_path / "pattern"
        _make_valid_pattern(pat)
        with open(pat / "values-secret.yaml.template", "w") as f:
            yaml.dump({
                "version": "2.0",
                "secrets": [{"name": "test", "vaultPrefixOverride": "global", "fields": []}],
            }, f, sort_keys=False)
        result = validate_and_fix(str(pat))
        assert result.valid is True
        with open(pat / "values-secret.yaml.template") as f:
            data = yaml.safe_load(f)
        assert "vaultPrefixes" in data["secrets"][0]
        assert isinstance(data["secrets"][0]["vaultPrefixes"], list)

    def test_fixes_pattern_sh_permissions(self, tmp_path):
        pat = tmp_path / "pattern"
        _make_valid_pattern(pat)
        (pat / "pattern.sh").chmod(0o644)
        result = validate_and_fix(str(pat))
        assert result.valid is True
        assert (pat / "pattern.sh").stat().st_mode & 0o111

    def test_fixes_missing_overrides(self, tmp_path):
        pat = tmp_path / "pattern"
        _make_valid_pattern(pat)
        import shutil
        shutil.rmtree(pat / "overrides")
        result = validate_and_fix(str(pat))
        assert result.valid is True
        assert (pat / "overrides").is_dir()
        assert (pat / "overrides" / "values-AWS.yaml").exists()

    def test_multiple_fixes_in_loop(self, tmp_path):
        """Corrupt multiple things and verify the loop fixes them all."""
        pat = tmp_path / "pattern"
        _make_valid_pattern(pat)
        # Break 4 things
        with open(pat / "values-global.yaml", "w") as f:
            f.write("---\n")
            yaml.dump({
                "global": {
                    "pattern": "test",
                    "main": {"clusterGroupName": "hub",
                             "multiSourceConfig": {"enabled": False}},
                },
            }, f, sort_keys=False)
        (pat / "Makefile").write_text("include common/Makefile\n")
        (pat / "pattern.sh").chmod(0o644)
        import shutil
        shutil.rmtree(pat / "overrides")

        result = validate_and_fix(str(pat))
        assert result.valid is True
        assert result.fixes_applied >= 4
        assert result.iterations > 1
