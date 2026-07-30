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


# ── Custom component namespace + extraValueFiles ──────────────────────────────

_CUSTOM_NS_SPEC = """\
apiVersion: supplychain/v1alpha1
kind: ApplicationSpec
metadata:
  name: ns-test
  tier: sandbox
  upstream: {}
blocks: {}
wiring: []
vault:
  enabled: true
custom:
  app-one:
    description: App in its own namespace
    namespace: my-app-namespace
    source:
      chart: charts/app-one
  app-two:
    description: App with extraValueFiles
    namespace: another-ns
    extraValueFiles:
      - /overrides/app-two-overrides.yaml
    source:
      chart: charts/app-two
  app-default:
    description: App with no namespace (uses pattern default)
    source:
      chart: charts/app-default
"""


class TestCustomComponentNamespace:
    def test_explicit_namespace_in_argocd_app(self, tmp_path):
        out = _compose(tmp_path, _CUSTOM_NS_SPEC)
        apps = _read_yaml(out / 'values-prod.yaml')['clusterGroup']['applications']
        assert apps['app-one']['namespace'] == 'my-app-namespace'

    def test_second_explicit_namespace(self, tmp_path):
        out = _compose(tmp_path, _CUSTOM_NS_SPEC)
        apps = _read_yaml(out / 'values-prod.yaml')['clusterGroup']['applications']
        assert apps['app-two']['namespace'] == 'another-ns'

    def test_default_namespace_when_unset(self, tmp_path):
        out = _compose(tmp_path, _CUSTOM_NS_SPEC)
        apps = _read_yaml(out / 'values-prod.yaml')['clusterGroup']['applications']
        # Falls back to pattern name when no namespace is declared
        assert apps['app-default']['namespace'] == 'ns-test'

    def test_extra_value_files_in_argocd_app(self, tmp_path):
        out = _compose(tmp_path, _CUSTOM_NS_SPEC)
        apps = _read_yaml(out / 'values-prod.yaml')['clusterGroup']['applications']
        assert apps['app-two']['extraValueFiles'] == ['/overrides/app-two-overrides.yaml']

    def test_no_extra_value_files_when_unset(self, tmp_path):
        out = _compose(tmp_path, _CUSTOM_NS_SPEC)
        apps = _read_yaml(out / 'values-prod.yaml')['clusterGroup']['applications']
        assert 'extraValueFiles' not in apps['app-one']


# ── Subscription channels ─────────────────────────────────────────────────────

_VIRT_SPEC = """\
apiVersion: supplychain/v1alpha1
kind: ApplicationSpec
metadata:
  name: virt-test
  tier: sandbox
  upstream: {}
blocks:
  virt:
    type: openshift-virtualization
  identity:
    type: keycloak-oidc
wiring: []
custom: {}
"""


class TestSubscriptionChannels:
    def test_openshift_virtualization_channel(self, tmp_path):
        out = _compose(tmp_path, _VIRT_SPEC)
        subs = _read_yaml(out / 'values-prod.yaml')['clusterGroup']['subscriptions']
        assert subs['openshift-virtualization']['channel'] == 'stable'

    def test_rhbk_channel(self, tmp_path):
        out = _compose(tmp_path, _VIRT_SPEC)
        subs = _read_yaml(out / 'values-prod.yaml')['clusterGroup']['subscriptions']
        assert subs['rhbk']['channel'] == 'stable-v26'

    def test_rhbk_namespace(self, tmp_path):
        out = _compose(tmp_path, _VIRT_SPEC)
        subs = _read_yaml(out / 'values-prod.yaml')['clusterGroup']['subscriptions']
        assert subs['rhbk']['namespace'] == 'openshell-agents'

    def test_rhbk_operatorgroup_namespace(self, tmp_path):
        out = _compose(tmp_path, _VIRT_SPEC)
        namespaces = _read_yaml(out / 'values-prod.yaml')['clusterGroup']['namespaces']
        assert namespaces.get('openshell-agents', {}).get('operatorGroup') is True


# ── Empty upstream — no orphan remote app or namespace ────────────────────────

_EMPTY_UPSTREAM_SPEC = """\
apiVersion: supplychain/v1alpha1
kind: ApplicationSpec
metadata:
  name: custom-only
  tier: sandbox
  upstream: {}
blocks: {}
wiring: []
vault:
  enabled: true
custom:
  my-app:
    namespace: my-ns
    source:
      chart: charts/my-app
"""


class TestEmptyUpstream:
    def test_no_remote_argocd_app(self, tmp_path):
        out = _compose(tmp_path, _EMPTY_UPSTREAM_SPEC)
        apps = _read_yaml(out / 'values-prod.yaml')['clusterGroup']['applications']
        # Only my-app, vault, and openshift-external-secrets should be present
        assert 'custom-only' not in apps

    def test_no_orphan_namespace(self, tmp_path):
        out = _compose(tmp_path, _EMPTY_UPSTREAM_SPEC)
        namespaces = _read_yaml(out / 'values-prod.yaml')['clusterGroup']['namespaces']
        assert 'custom-only' not in namespaces

    def test_custom_app_present(self, tmp_path):
        out = _compose(tmp_path, _EMPTY_UPSTREAM_SPEC)
        apps = _read_yaml(out / 'values-prod.yaml')['clusterGroup']['applications']
        assert 'my-app' in apps
        assert apps['my-app']['namespace'] == 'my-ns'


# ── deploy: manual — excluded from ArgoCD apps ───────────────────────────────

_MANUAL_DEPLOY_SPEC = """\
apiVersion: supplychain/v1alpha1
kind: ApplicationSpec
metadata:
  name: manual-test
  tier: sandbox
  upstream: {}
blocks: {}
wiring: []
vault:
  enabled: true
custom:
  runtime-app:
    description: Normal ArgoCD-managed app
    namespace: runtime-ns
    source:
      chart: charts/runtime-app

  build-job:
    description: One-time build step — not ArgoCD managed
    deploy: manual
    namespace: build-ns
    source:
      chart: charts/build-job
"""


class TestManualDeploy:
    def test_manual_component_absent_from_argocd_apps(self, tmp_path):
        out = _compose(tmp_path, _MANUAL_DEPLOY_SPEC)
        apps = _read_yaml(out / 'values-prod.yaml')['clusterGroup']['applications']
        assert 'build-job' not in apps

    def test_argocd_component_present(self, tmp_path):
        out = _compose(tmp_path, _MANUAL_DEPLOY_SPEC)
        apps = _read_yaml(out / 'values-prod.yaml')['clusterGroup']['applications']
        assert 'runtime-app' in apps
        assert apps['runtime-app']['namespace'] == 'runtime-ns'

    def test_invalid_deploy_value_raises(self, tmp_path):
        bad_spec = _MANUAL_DEPLOY_SPEC.replace("deploy: manual", "deploy: auto")
        spec_file = tmp_path / 'spec.yaml'
        spec_file.write_text(bad_spec)
        from quickpat.compose.parser import load_application_spec, AppSpecError
        with pytest.raises(AppSpecError, match="deploy.*must be"):
            load_application_spec(str(spec_file))


# ── vault: enabled: true without block secrets ───────────────────────────────

_VAULT_ENABLED_SPEC = """\
apiVersion: supplychain/v1alpha1
kind: ApplicationSpec
metadata:
  name: vault-flag-test
  tier: sandbox
  upstream: {}
blocks:
  virt:
    type: openshift-virtualization
wiring: []
vault:
  enabled: true
custom:
  my-app:
    namespace: my-ns
    source:
      chart: charts/my-app
"""


class TestVaultEnabled:
    def test_vault_app_in_values_prod(self, tmp_path):
        out = _compose(tmp_path, _VAULT_ENABLED_SPEC)
        apps = _read_yaml(out / 'values-prod.yaml')['clusterGroup']['applications']
        assert 'vault' in apps

    def test_eso_subscription_present(self, tmp_path):
        out = _compose(tmp_path, _VAULT_ENABLED_SPEC)
        subs = _read_yaml(out / 'values-prod.yaml')['clusterGroup']['subscriptions']
        assert 'openshift-external-secrets' in subs

    def test_secret_template_generated(self, tmp_path):
        # When vault is enabled (even with no block secrets), a template is produced
        # if top-level secrets are also present.
        spec_with_secrets = _VAULT_ENABLED_SPEC + """\
secrets:
  - name: my-key
    vault_path: vault-flag-test/my-key
    fields:
      - name: value
"""
        out = _compose(tmp_path, spec_with_secrets)
        assert (out / 'values-secret.yaml.template').exists()


# ── VP v2 values-secret.yaml.template ────────────────────────────────────────

_SECRETS_SPEC = """\
apiVersion: supplychain/v1alpha1
kind: ApplicationSpec
metadata:
  name: secrets-test
  tier: sandbox
  upstream: {}
blocks: {}
wiring: []
vault:
  enabled: true
custom: {}
secrets:
  - name: ssh
    vault_path: secrets-test/ssh
    fields:
      - name: private_key
      - name: public_key

  - name: anthropic
    vault_path: secrets-test/anthropic
    onMissingValue: skip
    fields:
      - name: api_key

  - name: gemini
    vault_path: secrets-test/gemini
    onMissingValue: skip
    fields:
      - name: api_key
"""


class TestVP2SecretTemplate:
    def test_template_file_exists(self, tmp_path):
        out = _compose(tmp_path, _SECRETS_SPEC)
        assert (out / 'values-secret.yaml.template').exists()

    def test_version_2_0(self, tmp_path):
        out = _compose(tmp_path, _SECRETS_SPEC)
        tmpl = _read_yaml(out / 'values-secret.yaml.template')
        assert tmpl['version'] == '2.0'

    def test_backing_store_vault(self, tmp_path):
        out = _compose(tmp_path, _SECRETS_SPEC)
        tmpl = _read_yaml(out / 'values-secret.yaml.template')
        assert tmpl['backingStore'] == 'vault'

    def test_ssh_group_present(self, tmp_path):
        out = _compose(tmp_path, _SECRETS_SPEC)
        tmpl = _read_yaml(out / 'values-secret.yaml.template')
        names = [s['name'] for s in tmpl['secrets']]
        assert 'ssh' in names

    def test_ssh_has_both_fields(self, tmp_path):
        out = _compose(tmp_path, _SECRETS_SPEC)
        tmpl = _read_yaml(out / 'values-secret.yaml.template')
        ssh = next(s for s in tmpl['secrets'] if s['name'] == 'ssh')
        field_names = [f['name'] for f in ssh['fields']]
        assert 'private_key' in field_names
        assert 'public_key' in field_names

    def test_all_groups_present(self, tmp_path):
        out = _compose(tmp_path, _SECRETS_SPEC)
        tmpl = _read_yaml(out / 'values-secret.yaml.template')
        names = {s['name'] for s in tmpl['secrets']}
        assert names == {'ssh', 'anthropic', 'gemini'}

    def test_fields_have_null_value(self, tmp_path):
        out = _compose(tmp_path, _SECRETS_SPEC)
        tmpl = _read_yaml(out / 'values-secret.yaml.template')
        for secret in tmpl['secrets']:
            for f in secret['fields']:
                assert 'value' in f
                assert f['value'] is None

    def test_skip_secrets_still_in_template(self, tmp_path):
        # onMissingValue: skip secrets still appear in the template
        # (user still needs to know to populate them if they want that provider)
        out = _compose(tmp_path, _SECRETS_SPEC)
        tmpl = _read_yaml(out / 'values-secret.yaml.template')
        names = {s['name'] for s in tmpl['secrets']}
        assert 'anthropic' in names
        assert 'gemini' in names

    def test_no_vault_prefixes(self, tmp_path):
        # VP v2 format uses value: null, not vaultPrefixes
        out = _compose(tmp_path, _SECRETS_SPEC)
        tmpl = _read_yaml(out / 'values-secret.yaml.template')
        for secret in tmpl['secrets']:
            assert 'vaultPrefixes' not in secret

    def test_no_on_missing_value_in_template(self, tmp_path):
        # Template fields should only have name + value, not onMissingValue
        out = _compose(tmp_path, _SECRETS_SPEC)
        tmpl = _read_yaml(out / 'values-secret.yaml.template')
        for secret in tmpl['secrets']:
            for f in secret['fields']:
                assert 'onMissingValue' not in f
