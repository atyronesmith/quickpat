"""Tests for QS (Quickstart Helm chart) generation from ApplicationSpec."""

import yaml
import pytest
from pathlib import Path

from quickpat.pipeline import compose_qs_from_spec

FIXTURES = Path(__file__).parent / 'fixtures'
LEMONADE_SPEC = str(FIXTURES / 'lemonade-stand-compose.yaml')
LEMONADE_REPO = Path(__file__).parent.parent.parent / 'lemonade-stand'


def _qs(tmp_path, spec_yaml: str) -> Path:
    spec_file = tmp_path / 'spec.yaml'
    spec_file.write_text(spec_yaml)
    result = compose_qs_from_spec(str(spec_file), output_dir=str(tmp_path / 'qs-out'))
    assert result.success, f"QS compose failed: {result.warnings}"
    return tmp_path / 'qs-out'


MINIMAL_SPEC = """\
apiVersion: supplychain/v1alpha1
kind: ApplicationSpec
metadata:
  name: qs-test
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
wiring: []
custom: {}
"""


# ── Chart structure ──────────────────────────────────────────────────────────


class TestChartStructure:
    def test_chart_yaml_created(self, tmp_path):
        out = _qs(tmp_path, MINIMAL_SPEC)
        assert (out / 'chart' / 'Chart.yaml').exists()

    def test_chart_yaml_has_correct_name(self, tmp_path):
        out = _qs(tmp_path, MINIMAL_SPEC)
        chart = yaml.safe_load((out / 'chart' / 'Chart.yaml').read_text())
        assert chart['name'] == 'qs-test'
        assert chart['apiVersion'] == 'v2'

    def test_values_yaml_created(self, tmp_path):
        out = _qs(tmp_path, MINIMAL_SPEC)
        assert (out / 'chart' / 'values.yaml').exists()

    def test_notes_txt_created(self, tmp_path):
        out = _qs(tmp_path, MINIMAL_SPEC)
        assert (out / 'chart' / 'templates' / 'NOTES.txt').exists()

    def test_readme_created(self, tmp_path):
        out = _qs(tmp_path, MINIMAL_SPEC)
        assert (out / 'README.md').exists()

    def test_create_secrets_sh_created(self, tmp_path):
        out = _qs(tmp_path, MINIMAL_SPEC)
        assert (out / 'scripts' / 'create-secrets.sh').exists()

    def test_create_secrets_sh_is_executable(self, tmp_path):
        out = _qs(tmp_path, MINIMAL_SPEC)
        sh = out / 'scripts' / 'create-secrets.sh'
        assert sh.stat().st_mode & 0o111  # executable bit set


# ── Infrastructure blocks → prereqs only ─────────────────────────────────────


class TestInfraBlocks:
    SPEC = """\
apiVersion: supplychain/v1alpha1
kind: ApplicationSpec
metadata:
  name: infra-test
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
  gpu:
    type: gpu-compute
    config:
      mig_strategy: single
wiring: []
custom: {}
"""

    def test_no_chart_templates_for_infra_blocks(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        assert not (out / 'chart' / 'templates' / 'platform').exists()
        assert not (out / 'chart' / 'templates' / 'gpu').exists()

    def test_notes_txt_contains_openshift_ai_prereq(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        notes = (out / 'chart' / 'templates' / 'NOTES.txt').read_text()
        assert 'OpenShift AI' in notes

    def test_notes_txt_contains_gpu_prereq(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        notes = (out / 'chart' / 'templates' / 'NOTES.txt').read_text()
        assert 'NVIDIA GPU Operator' in notes

    def test_notes_txt_contains_mig_strategy(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        notes = (out / 'chart' / 'templates' / 'NOTES.txt').read_text()
        assert 'mig_strategy: single' in notes


# ── Model serving block → inline templates ────────────────────────────────────


class TestModelServingBlock:
    SPEC = """\
apiVersion: supplychain/v1alpha1
kind: ApplicationSpec
metadata:
  name: ms-test
  tier: sandbox
  upstream:
    repo: https://github.com/example/qs.git
blocks:
  platform:
    type: ai-platform-foundation
  llm:
    type: model-serving
    config:
      model: meta-llama/Llama-3.2-3B-Instruct
      runtime: vllm
      image: quay.io/modh/vllm:rhoai-2.19-cuda
      gpu: true
      replicas:
        min: 0
        max: 1
      resources:
        requests: {cpu: 1, memory: 8Gi}
        limits: {cpu: 4, memory: 20Gi}
      storage:
        type: oci
        uri: oci://quay.io/redhat-ai-services/modelcar-catalog:llama-3.2-3b-instruct
wiring: []
custom: {}
"""

    def test_serving_runtime_created(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        assert (out / 'chart' / 'templates' / 'llm' / 'servingruntime.yaml').exists()

    def test_inference_service_created(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        assert (out / 'chart' / 'templates' / 'llm' / 'inferenceservice.yaml').exists()

    def test_serving_runtime_uses_helm_values(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        sr = (out / 'chart' / 'templates' / 'llm' / 'servingruntime.yaml').read_text()
        assert '{{ .Values.llm.image }}' in sr

    def test_inference_service_uses_helm_values(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        is_text = (out / 'chart' / 'templates' / 'llm' / 'inferenceservice.yaml').read_text()
        assert '{{ .Values.llm.storageUri }}' in is_text

    def test_inference_service_has_gpu_toleration(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        is_text = (out / 'chart' / 'templates' / 'llm' / 'inferenceservice.yaml').read_text()
        assert 'nvidia.com/gpu' in is_text

    def test_values_yaml_has_llm_section(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        values = (out / 'chart' / 'values.yaml').read_text()
        assert 'llm:' in values
        assert 'meta-llama/Llama-3.2-3B-Instruct' in values


# ── Object storage block ──────────────────────────────────────────────────────


class TestObjectStorageBlock:
    SPEC = """\
apiVersion: supplychain/v1alpha1
kind: ApplicationSpec
metadata:
  name: os-test
  tier: sandbox
  upstream:
    repo: https://github.com/example/qs.git
blocks:
  platform:
    type: ai-platform-foundation
  store:
    type: object-storage
    config:
      provider: minio
      storage: 20Gi
      init_models:
        - my-model/v1
wiring: []
custom: {}
"""

    def test_pvc_created(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        assert (out / 'chart' / 'templates' / 'store' / 'pvc.yaml').exists()

    def test_deployment_created(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        assert (out / 'chart' / 'templates' / 'store' / 'deployment.yaml').exists()

    def test_service_created(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        assert (out / 'chart' / 'templates' / 'store' / 'service.yaml').exists()

    def test_credentials_secret_created(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        assert (out / 'chart' / 'templates' / 'store' / 'credentials.yaml').exists()

    def test_data_connection_secret_created(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        assert (out / 'chart' / 'templates' / 'store' / 'data-connection.yaml').exists()

    def test_pvc_uses_helm_values(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        pvc = (out / 'chart' / 'templates' / 'store' / 'pvc.yaml').read_text()
        assert '{{ .Values.store.storage }}' in pvc

    def test_credentials_uses_secret_values(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        creds = (out / 'chart' / 'templates' / 'store' / 'credentials.yaml').read_text()
        assert '.Values.secrets.minioAccessKey' in creds


# ── Default output → qs-out ───────────────────────────────────────────────────


class TestDefaultOutput:
    def test_default_output_is_qs_out(self, tmp_path):
        spec_file = tmp_path / 'spec.yaml'
        spec_file.write_text(MINIMAL_SPEC.replace('qs-test', 'default-qs'))
        result = compose_qs_from_spec(str(spec_file))
        assert result.success
        assert result.pattern_dir == str(tmp_path / 'qs-out')
        assert (tmp_path / 'qs-out' / 'chart' / 'Chart.yaml').exists()


# ── Lemonade-stand end-to-end ─────────────────────────────────────────────────


class TestLemonadeStandQS:
    def test_compose_qs_succeeds(self, tmp_path):
        result = compose_qs_from_spec(LEMONADE_SPEC, output_dir=str(tmp_path / 'qs'))
        assert result.success

    def test_all_model_serving_blocks_have_templates(self, tmp_path):
        compose_qs_from_spec(LEMONADE_SPEC, output_dir=str(tmp_path / 'qs'))
        out = tmp_path / 'qs'
        for block in ('llm', 'hap-detector', 'prompt-injection-detector'):
            assert (out / 'chart' / 'templates' / block / 'servingruntime.yaml').exists()
            assert (out / 'chart' / 'templates' / block / 'inferenceservice.yaml').exists()

    def test_object_storage_templates_present(self, tmp_path):
        compose_qs_from_spec(LEMONADE_SPEC, output_dir=str(tmp_path / 'qs'))
        out = tmp_path / 'qs'
        for f in ('pvc.yaml', 'deployment.yaml', 'service.yaml',
                  'credentials.yaml', 'data-connection.yaml'):
            assert (out / 'chart' / 'templates' / 'model-storage' / f).exists()

    def test_guardrails_templates_present(self, tmp_path):
        compose_qs_from_spec(LEMONADE_SPEC, output_dir=str(tmp_path / 'qs'))
        out = tmp_path / 'qs'
        assert (out / 'chart' / 'templates' / 'guardrails' / 'orchestrator.yaml').exists()
        assert (out / 'chart' / 'templates' / 'guardrails' / 'configmap.yaml').exists()

    def test_no_infra_block_templates(self, tmp_path):
        compose_qs_from_spec(LEMONADE_SPEC, output_dir=str(tmp_path / 'qs'))
        out = tmp_path / 'qs'
        assert not (out / 'chart' / 'templates' / 'platform').exists()
        assert not (out / 'chart' / 'templates' / 'gpu').exists()

    def test_readme_has_helm_install_instructions(self, tmp_path):
        compose_qs_from_spec(LEMONADE_SPEC, output_dir=str(tmp_path / 'qs'))
        readme = (tmp_path / 'qs' / 'README.md').read_text()
        assert 'helm install' in readme

    @pytest.mark.skipif(
        not LEMONADE_REPO.exists(),
        reason='lemonade-stand repo not present',
    )
    def test_custom_charts_copied_from_repo(self, tmp_path):
        compose_qs_from_spec(
            str(LEMONADE_REPO / 'spec.yaml'),
            output_dir=str(tmp_path / 'qs'),
        )
        out = tmp_path / 'qs'
        # Real templates from the repo — not stubs
        assert (out / 'chart' / 'templates' / 'lemonade-stand-app' / 'deployment.yaml').exists()
        assert not (out / 'chart' / 'templates' / 'lemonade-stand-app' / '.gitkeep').exists()
