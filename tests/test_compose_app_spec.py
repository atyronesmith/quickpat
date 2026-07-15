"""Tests for ApplicationSpec compose pipeline — DSC/GPU config flow and custom component stubs."""

import yaml
import pytest
from pathlib import Path

from quickpat.compose.parser import load_application_spec
from quickpat.compose.compiler import compile_spec
from quickpat.pipeline import compose_from_spec


FIXTURES = Path(__file__).parent / 'fixtures'
LEMONADE_SPEC = str(FIXTURES / 'lemonade-stand-compose.yaml')
LEMONADE_REPO = Path(__file__).parent.parent.parent / 'lemonade-stand'


def _compose(tmp_path, spec_yaml: str) -> Path:
    """Write a spec file, run compose, return the output directory."""
    spec_file = tmp_path / 'spec.yaml'
    spec_file.write_text(spec_yaml)
    result = compose_from_spec(str(spec_file), output_dir=str(tmp_path / 'out'))
    assert result.success, f"compose failed: {result.warnings}"
    return tmp_path / 'out'


def _read_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


# ── DSC config flow ──────────────────────────────────────────────────────────


class TestDSCConfig:
    SPEC = """\
apiVersion: supplychain/v1alpha1
kind: ApplicationSpec
metadata:
  name: dsc-test
  tier: sandbox
  upstream:
    repo: https://github.com/example/qs.git
blocks:
  platform:
    type: ai-platform-foundation
    config:
      dsc:
        kserve: Managed
        trustyai: Managed
        dashboard: Managed
        datasciencepipelines: Removed
        workbenches: Removed
wiring: []
custom: {}
"""

    def test_dsc_chart_created(self, tmp_path):
        out = _compose(tmp_path, self.SPEC)
        assert (out / 'charts' / 'dsc' / 'Chart.yaml').exists()
        assert (out / 'charts' / 'dsc' / 'templates' / 'datasciencecluster.yaml').exists()

    def test_trustyai_managed_from_config(self, tmp_path):
        out = _compose(tmp_path, self.SPEC)
        dsc = _read_yaml(out / 'charts' / 'dsc' / 'templates' / 'datasciencecluster.yaml')
        trustyai_state = dsc['spec']['components']['trustyai']['managementState']
        assert trustyai_state == 'Managed'

    def test_datasciencepipelines_removed_from_config(self, tmp_path):
        out = _compose(tmp_path, self.SPEC)
        dsc = _read_yaml(out / 'charts' / 'dsc' / 'templates' / 'datasciencecluster.yaml')
        dsp_state = dsc['spec']['components']['datasciencepipelines']['managementState']
        assert dsp_state == 'Removed'

    def test_kserve_managed_from_config(self, tmp_path):
        out = _compose(tmp_path, self.SPEC)
        dsc = _read_yaml(out / 'charts' / 'dsc' / 'templates' / 'datasciencecluster.yaml')
        assert dsc['spec']['components']['kserve']['managementState'] == 'Managed'

    def test_dsc_argocd_app_in_values_hub(self, tmp_path):
        out = _compose(tmp_path, self.SPEC)
        hub = _read_yaml(out / 'values-prod.yaml')
        apps = hub['clusterGroup']['applications']
        assert 'dsc' in apps
        assert apps['dsc']['path'] == 'charts/dsc'

    def test_no_dsc_config_uses_defaults(self, tmp_path):
        spec = """\
apiVersion: supplychain/v1alpha1
kind: ApplicationSpec
metadata:
  name: no-dsc-config
  tier: sandbox
  upstream:
    repo: https://github.com/example/qs.git
blocks:
  platform:
    type: ai-platform-foundation
wiring: []
custom: {}
"""
        out = _compose(tmp_path, spec)
        dsc = _read_yaml(out / 'charts' / 'dsc' / 'templates' / 'datasciencecluster.yaml')
        # Should still produce a valid DSC with hardcoded defaults
        assert 'components' in dsc['spec']
        assert 'kserve' in dsc['spec']['components']


# ── GPU config flow ──────────────────────────────────────────────────────────


class TestGPUConfig:
    SPEC = """\
apiVersion: supplychain/v1alpha1
kind: ApplicationSpec
metadata:
  name: gpu-test
  tier: sandbox
  upstream:
    repo: https://github.com/example/qs.git
blocks:
  platform:
    type: ai-platform-foundation
  gpu:
    type: gpu-compute
    config:
      mig_strategy: single
      dcgm: true
      vgpu_manager: false
      driver:
        upgrade_policy:
          auto_upgrade: true
wiring: []
custom: {}
"""

    def test_clusterpolicy_chart_created(self, tmp_path):
        out = _compose(tmp_path, self.SPEC)
        assert (out / 'charts' / 'nvidia-config' / 'Chart.yaml').exists()
        assert (out / 'charts' / 'nvidia-config' / 'templates' / 'clusterpolicy.yaml').exists()

    def test_mig_strategy_from_config(self, tmp_path):
        out = _compose(tmp_path, self.SPEC)
        cp = _read_yaml(out / 'charts' / 'nvidia-config' / 'templates' / 'clusterpolicy.yaml')
        assert cp['spec']['mig']['strategy'] == 'single'

    def test_dcgm_enabled_from_config(self, tmp_path):
        out = _compose(tmp_path, self.SPEC)
        cp = _read_yaml(out / 'charts' / 'nvidia-config' / 'templates' / 'clusterpolicy.yaml')
        assert cp['spec']['dcgmExporter']['enabled'] is True

    def test_auto_upgrade_from_config(self, tmp_path):
        out = _compose(tmp_path, self.SPEC)
        cp = _read_yaml(out / 'charts' / 'nvidia-config' / 'templates' / 'clusterpolicy.yaml')
        assert cp['spec']['driver']['upgradePolicy']['autoUpgrade'] is True

    def test_nvidia_argocd_app_in_values_hub(self, tmp_path):
        out = _compose(tmp_path, self.SPEC)
        hub = _read_yaml(out / 'values-prod.yaml')
        apps = hub['clusterGroup']['applications']
        assert 'nvidia-config' in apps
        assert apps['nvidia-config']['path'] == 'charts/nvidia-config'

    def test_mig_none_variant(self, tmp_path):
        spec = self.SPEC.replace('mig_strategy: single', 'mig_strategy: none')
        out = _compose(tmp_path, spec)
        cp = _read_yaml(out / 'charts' / 'nvidia-config' / 'templates' / 'clusterpolicy.yaml')
        assert cp['spec']['mig']['strategy'] == 'none'


# ── Custom component stubs ───────────────────────────────────────────────────


class TestCustomComponentStubs:
    SPEC = """\
apiVersion: supplychain/v1alpha1
kind: ApplicationSpec
metadata:
  name: stub-test
  tier: sandbox
  upstream:
    repo: https://github.com/example/qs.git
blocks:
  platform:
    type: ai-platform-foundation
wiring: []
custom:
  my-app:
    description: Test app
    source:
      image: quay.io/example/my-app:1.0
    replicas: 2
    ports:
      - name: http
        port: 8080
        route: true
    env:
      DATABASE_URL: http://db:5432
    resources:
      requests:
        cpu: 500m
        memory: 512Mi
  my-worker:
    description: Background worker
    source:
      image: quay.io/example/worker:latest
    replicas: 1
    ports: []
    env: {}
    resources: {}
"""

    def test_stub_charts_created(self, tmp_path):
        out = _compose(tmp_path, self.SPEC)
        assert (out / 'charts' / 'my-app' / 'Chart.yaml').exists()
        assert (out / 'charts' / 'my-worker' / 'Chart.yaml').exists()

    def test_stub_templates_dir_created(self, tmp_path):
        out = _compose(tmp_path, self.SPEC)
        assert (out / 'charts' / 'my-app' / 'templates').is_dir()
        assert (out / 'charts' / 'my-worker' / 'templates').is_dir()

    def test_stub_values_contains_image_comment(self, tmp_path):
        out = _compose(tmp_path, self.SPEC)
        values = (out / 'charts' / 'my-app' / 'values.yaml').read_text()
        assert 'quay.io/example/my-app:1.0' in values

    def test_stub_values_contains_env_comment(self, tmp_path):
        out = _compose(tmp_path, self.SPEC)
        values = (out / 'charts' / 'my-app' / 'values.yaml').read_text()
        assert 'DATABASE_URL' in values

    def test_custom_apps_in_values_hub(self, tmp_path):
        out = _compose(tmp_path, self.SPEC)
        hub = _read_yaml(out / 'values-prod.yaml')
        apps = hub['clusterGroup']['applications']
        assert 'my-app' in apps
        assert apps['my-app']['path'] == 'charts/my-app'
        assert 'my-worker' in apps
        assert apps['my-worker']['path'] == 'charts/my-worker'

    def test_custom_apps_use_app_namespace(self, tmp_path):
        out = _compose(tmp_path, self.SPEC)
        hub = _read_yaml(out / 'values-prod.yaml')
        apps = hub['clusterGroup']['applications']
        assert apps['my-app']['namespace'] == 'stub-test'


# ── Lemonade-stand end-to-end ────────────────────────────────────────────────


class TestLemonadeStandCompose:
    def test_spec_loads(self):
        spec = load_application_spec(LEMONADE_SPEC)
        assert spec.name == 'lemonade-stand'
        assert 'platform' in spec.blocks
        assert 'gpu' in spec.blocks
        assert spec.blocks['platform'].block_type == 'ai-platform-foundation'
        assert spec.blocks['gpu'].block_type == 'gpu-compute'

    def test_compile_extracts_dsc_config(self):
        spec = load_application_spec(LEMONADE_SPEC)
        _, config = compile_spec(spec, '/tmp/unused')
        assert config['dsc_config'].get('trustyai') == 'Managed'
        assert config['dsc_config'].get('datasciencepipelines') == 'Removed'

    def test_compile_extracts_gpu_config(self):
        spec = load_application_spec(LEMONADE_SPEC)
        _, config = compile_spec(spec, '/tmp/unused')
        assert config['gpu_config'].get('mig_strategy') == 'single'
        assert config['gpu_config'].get('dcgm') is True

    def test_compile_captures_custom_components(self):
        spec = load_application_spec(LEMONADE_SPEC)
        _, config = compile_spec(spec, '/tmp/unused')
        assert 'lemonade-stand-app' in config['custom_components']
        assert 'chunker-service' in config['custom_components']
        assert 'lingua-detector' in config['custom_components']
        assert 'shiny-dashboard' in config['custom_components']

    def test_full_compose_succeeds(self, tmp_path):
        result = compose_from_spec(LEMONADE_SPEC, output_dir=str(tmp_path / 'out'))
        assert result.success, f"compose failed: {result.warnings}"

    def test_dsc_trustyai_managed(self, tmp_path):
        compose_from_spec(LEMONADE_SPEC, output_dir=str(tmp_path / 'out'))
        dsc = _read_yaml(tmp_path / 'out' / 'charts' / 'dsc' / 'templates' / 'datasciencecluster.yaml')
        assert dsc['spec']['components']['trustyai']['managementState'] == 'Managed'

    def test_dsc_datasciencepipelines_removed(self, tmp_path):
        compose_from_spec(LEMONADE_SPEC, output_dir=str(tmp_path / 'out'))
        dsc = _read_yaml(tmp_path / 'out' / 'charts' / 'dsc' / 'templates' / 'datasciencecluster.yaml')
        assert dsc['spec']['components']['datasciencepipelines']['managementState'] == 'Removed'

    def test_clusterpolicy_mig_single(self, tmp_path):
        compose_from_spec(LEMONADE_SPEC, output_dir=str(tmp_path / 'out'))
        cp = _read_yaml(tmp_path / 'out' / 'charts' / 'nvidia-config' / 'templates' / 'clusterpolicy.yaml')
        assert cp['spec']['mig']['strategy'] == 'single'

    def test_custom_component_stubs_present(self, tmp_path):
        compose_from_spec(LEMONADE_SPEC, output_dir=str(tmp_path / 'out'))
        out = tmp_path / 'out'
        for comp in ('lemonade-stand-app', 'chunker-service', 'lingua-detector', 'shiny-dashboard'):
            assert (out / 'charts' / comp / 'Chart.yaml').exists(), f"missing stub for {comp}"

    def test_all_apps_in_values_hub(self, tmp_path):
        compose_from_spec(LEMONADE_SPEC, output_dir=str(tmp_path / 'out'))
        hub = _read_yaml(tmp_path / 'out' / 'values-prod.yaml')
        apps = hub['clusterGroup']['applications']
        # Infra charts
        assert 'dsc' in apps
        assert 'nvidia-config' in apps
        # Custom component stubs
        assert 'lemonade-stand-app' in apps
        assert 'chunker-service' in apps
        assert 'shiny-dashboard' in apps


# ── Auto-detect and copy existing charts ────────────────────────────────────


class TestExistingChartDetection:
    """When charts/ exist next to spec.yaml, compose copies them instead of stubbing."""

    def _make_app_repo(self, tmp_path: Path, components: list[str]) -> Path:
        """Create a minimal application repo with real charts for given components."""
        spec_dir = tmp_path / 'myapp'
        spec_dir.mkdir()

        spec = {
            'apiVersion': 'supplychain/v1alpha1',
            'kind': 'ApplicationSpec',
            'metadata': {
                'name': 'myapp',
                'tier': 'sandbox',
                'upstream': {'repo': 'https://github.com/example/qs.git'},
            },
            'blocks': {
                'platform': {'type': 'ai-platform-foundation'},
            },
            'wiring': [],
            'custom': {
                comp: {
                    'description': f'{comp} component',
                    'source': {'image': f'quay.io/example/{comp}:latest'},
                    'replicas': 1,
                    'ports': [],
                    'env': {},
                    'resources': {},
                }
                for comp in components
            },
        }
        (spec_dir / 'spec.yaml').write_text(yaml.dump(spec))

        for comp in components:
            chart_dir = spec_dir / 'charts' / comp / 'templates'
            chart_dir.mkdir(parents=True)
            (spec_dir / 'charts' / comp / 'Chart.yaml').write_text(
                yaml.dump({'apiVersion': 'v2', 'name': comp, 'version': '1.0.0', 'type': 'application'})
            )
            (spec_dir / 'charts' / comp / 'values.yaml').write_text('# hand-written\n')
            (chart_dir / 'deployment.yaml').write_text(
                f'# hand-written deployment for {comp}\nkind: Deployment\n'
            )

        return spec_dir

    def test_existing_chart_copied_not_stubbed(self, tmp_path):
        spec_dir = self._make_app_repo(tmp_path, ['my-api'])
        out = tmp_path / 'out'
        result = compose_from_spec(str(spec_dir / 'spec.yaml'), output_dir=str(out))
        assert result.success

        # Real chart was copied — deployment.yaml is present (not a stub .gitkeep)
        assert (out / 'charts' / 'my-api' / 'templates' / 'deployment.yaml').exists()
        assert not (out / 'charts' / 'my-api' / 'templates' / '.gitkeep').exists()

    def test_copied_chart_preserves_hand_written_content(self, tmp_path):
        spec_dir = self._make_app_repo(tmp_path, ['my-api'])
        out = tmp_path / 'out'
        compose_from_spec(str(spec_dir / 'spec.yaml'), output_dir=str(out))

        content = (out / 'charts' / 'my-api' / 'templates' / 'deployment.yaml').read_text()
        assert 'hand-written deployment for my-api' in content

    def test_missing_chart_still_gets_stub(self, tmp_path):
        spec_dir = self._make_app_repo(tmp_path, [])  # no charts/
        # Add a custom component with no chart in the repo
        spec = yaml.safe_load((spec_dir / 'spec.yaml').read_text())
        spec['custom']['orphan'] = {
            'description': 'orphan', 'source': {'image': 'quay.io/x/y:1'},
            'replicas': 1, 'ports': [], 'env': {}, 'resources': {},
        }
        (spec_dir / 'spec.yaml').write_text(yaml.dump(spec))

        out = tmp_path / 'out'
        compose_from_spec(str(spec_dir / 'spec.yaml'), output_dir=str(out))
        assert (out / 'charts' / 'orphan' / 'templates' / '.gitkeep').exists()

    def test_rerun_overwrites_copied_chart(self, tmp_path):
        spec_dir = self._make_app_repo(tmp_path, ['my-api'])
        out = tmp_path / 'out'
        compose_from_spec(str(spec_dir / 'spec.yaml'), output_dir=str(out))

        # Update source chart
        deploy = spec_dir / 'charts' / 'my-api' / 'templates' / 'deployment.yaml'
        deploy.write_text('# updated deployment\nkind: Deployment\n')

        compose_from_spec(str(spec_dir / 'spec.yaml'), output_dir=str(out))
        content = (out / 'charts' / 'my-api' / 'templates' / 'deployment.yaml').read_text()
        assert 'updated deployment' in content

    def test_argocd_app_entry_present_for_copied_chart(self, tmp_path):
        spec_dir = self._make_app_repo(tmp_path, ['my-api'])
        out = tmp_path / 'out'
        compose_from_spec(str(spec_dir / 'spec.yaml'), output_dir=str(out))

        hub = _read_yaml(out / 'values-prod.yaml')
        apps = hub['clusterGroup']['applications']
        assert 'my-api' in apps
        assert apps['my-api']['path'] == 'charts/my-api'


class TestDefaultOutputDir:
    """When --output is omitted, compose writes to vp-out/ next to spec.yaml."""

    def test_default_output_is_vp_out(self, tmp_path):
        spec_file = tmp_path / 'spec.yaml'
        spec_file.write_text(yaml.dump({
            'apiVersion': 'supplychain/v1alpha1',
            'kind': 'ApplicationSpec',
            'metadata': {
                'name': 'default-out-test',
                'tier': 'sandbox',
                'upstream': {'repo': 'https://github.com/example/qs.git'},
            },
            'blocks': {'platform': {'type': 'ai-platform-foundation'}},
            'wiring': [],
            'custom': {},
        }))

        result = compose_from_spec(str(spec_file))  # no output_dir
        assert result.success
        assert result.pattern_dir == str(tmp_path / 'vp-out')
        assert (tmp_path / 'vp-out' / 'values-prod.yaml').exists()

    def test_explicit_output_overrides_default(self, tmp_path):
        spec_file = tmp_path / 'spec.yaml'
        spec_file.write_text(yaml.dump({
            'apiVersion': 'supplychain/v1alpha1',
            'kind': 'ApplicationSpec',
            'metadata': {
                'name': 'explicit-out-test',
                'tier': 'sandbox',
                'upstream': {'repo': 'https://github.com/example/qs.git'},
            },
            'blocks': {'platform': {'type': 'ai-platform-foundation'}},
            'wiring': [],
            'custom': {},
        }))

        out = tmp_path / 'custom-out'
        result = compose_from_spec(str(spec_file), output_dir=str(out))
        assert result.success
        assert result.pattern_dir == str(out)
        assert (out / 'values-prod.yaml').exists()
        assert not (tmp_path / 'vp-out').exists()


class TestGeneratedHeaders:
    """Generated YAML files should have the do-not-edit header comment."""

    def test_values_hub_has_header(self, tmp_path):
        spec_file = tmp_path / 'spec.yaml'
        spec_file.write_text(yaml.dump({
            'apiVersion': 'supplychain/v1alpha1',
            'kind': 'ApplicationSpec',
            'metadata': {
                'name': 'header-test',
                'tier': 'sandbox',
                'upstream': {'repo': 'https://github.com/example/qs.git'},
            },
            'blocks': {'platform': {'type': 'ai-platform-foundation'}},
            'wiring': [],
            'custom': {},
        }))

        compose_from_spec(str(spec_file))
        hub = (tmp_path / 'vp-out' / 'values-prod.yaml').read_text()
        assert 'Generated by quickpat compose' in hub

    def test_dsc_chart_has_header(self, tmp_path):
        spec_file = tmp_path / 'spec.yaml'
        spec_file.write_text(yaml.dump({
            'apiVersion': 'supplychain/v1alpha1',
            'kind': 'ApplicationSpec',
            'metadata': {
                'name': 'header-test',
                'tier': 'sandbox',
                'upstream': {'repo': 'https://github.com/example/qs.git'},
            },
            'blocks': {'platform': {'type': 'ai-platform-foundation'}},
            'wiring': [],
            'custom': {},
        }))

        compose_from_spec(str(spec_file))
        dsc_cr = (tmp_path / 'vp-out' / 'charts' / 'dsc' / 'templates' / 'datasciencecluster.yaml').read_text()
        assert 'Generated by quickpat compose' in dsc_cr


class TestLemonadeStandRepo:
    """End-to-end test using the actual lemonade-stand application repo."""

    @pytest.mark.skipif(
        not LEMONADE_REPO.exists(),
        reason='lemonade-stand repo not present at expected path'
    )
    def test_compose_from_repo_spec(self, tmp_path):
        result = compose_from_spec(
            str(LEMONADE_REPO / 'spec.yaml'),
            output_dir=str(tmp_path / 'vp-out'),
        )
        assert result.success

    @pytest.mark.skipif(
        not LEMONADE_REPO.exists(),
        reason='lemonade-stand repo not present at expected path'
    )
    def test_hand_written_charts_copied_not_stubbed(self, tmp_path):
        compose_from_spec(
            str(LEMONADE_REPO / 'spec.yaml'),
            output_dir=str(tmp_path / 'vp-out'),
        )
        out = tmp_path / 'vp-out'

        # All 5 custom components should have real content, not stubs
        for comp in ('lemonade-stand-app', 'chunker-service', 'lingua-detector',
                     'shiny-dashboard', 'guardrails-config'):
            chart_dir = out / 'charts' / comp
            assert chart_dir.exists(), f'missing chart dir for {comp}'
            assert not (chart_dir / 'templates' / '.gitkeep').exists(), \
                f'{comp} has stub .gitkeep — should have been copied from repo'

    @pytest.mark.skipif(
        not LEMONADE_REPO.exists(),
        reason='lemonade-stand repo not present at expected path'
    )
    def test_lemonade_app_deployment_template_present(self, tmp_path):
        compose_from_spec(
            str(LEMONADE_REPO / 'spec.yaml'),
            output_dir=str(tmp_path / 'vp-out'),
        )
        assert (tmp_path / 'vp-out' / 'charts' / 'lemonade-stand-app' / 'templates' / 'deployment.yaml').exists()
