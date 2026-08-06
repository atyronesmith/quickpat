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
        # Key name is now <camelBlockName>AccessKey, not generic minioAccessKey
        assert '.Values.secrets.storeAccessKey' in creds


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


# ── Provider-conditional object storage ──────────────────────────────────────


class TestObjectStorageProviders:
    """object_storage_templates() generates provider-conditional Helm templates."""

    def _spec(self, provider: str, block_name: str = 'store') -> str:
        return f"""\
apiVersion: supplychain/v1alpha1
kind: ApplicationSpec
metadata:
  name: provider-test
  tier: sandbox
  upstream:
    repo: https://github.com/example/qs.git
blocks:
  platform:
    type: ai-platform-foundation
  {block_name}:
    type: object-storage
    config:
      provider: {provider}
      bucket: testbucket
      storage: 5Gi
wiring: []
custom: {{}}
"""

    # ── minio ─────────────────────────────────────────────────────────────────

    def test_minio_generates_pvc(self, tmp_path):
        out = _qs(tmp_path, self._spec('minio'))
        pvc = (out / 'chart' / 'templates' / 'store' / 'pvc.yaml').read_text()
        assert 'PersistentVolumeClaim' in pvc
        assert 'eq .Values.store.provider "minio"' in pvc

    def test_minio_generates_deployment(self, tmp_path):
        out = _qs(tmp_path, self._spec('minio'))
        dep = (out / 'chart' / 'templates' / 'store' / 'deployment.yaml').read_text()
        assert 'kind: Deployment' in dep
        assert 'eq .Values.store.provider "minio"' in dep

    def test_minio_deployment_has_bucket_init_container(self, tmp_path):
        out = _qs(tmp_path, self._spec('minio'))
        dep = (out / 'chart' / 'templates' / 'store' / 'deployment.yaml').read_text()
        assert 'bucket-init' in dep
        assert 'mc mb' in dep

    def test_minio_generates_service(self, tmp_path):
        out = _qs(tmp_path, self._spec('minio'))
        assert (out / 'chart' / 'templates' / 'store' / 'service.yaml').exists()

    def test_minio_no_obc(self, tmp_path):
        out = _qs(tmp_path, self._spec('minio'))
        obc = (out / 'chart' / 'templates' / 'store' / 'obc.yaml').read_text()
        assert 'ObjectBucketClaim' not in obc or 'eq .Values.store.provider "odf"' in obc

    def test_minio_data_connection_uses_service_endpoint(self, tmp_path):
        out = _qs(tmp_path, self._spec('minio'))
        dc = (out / 'chart' / 'templates' / 'store' / 'data-connection.yaml').read_text()
        assert 'store:9000' in dc

    def test_minio_values_yaml_has_provider(self, tmp_path):
        out = _qs(tmp_path, self._spec('minio'))
        values = (out / 'chart' / 'values.yaml').read_text()
        assert 'provider: minio' in values

    # ── odf ──────────────────────────────────────────────────────────────────

    def test_odf_generates_obc(self, tmp_path):
        out = _qs(tmp_path, self._spec('odf'))
        obc = (out / 'chart' / 'templates' / 'store' / 'obc.yaml').read_text()
        assert 'ObjectBucketClaim' in obc
        assert 'eq .Values.store.provider "odf"' in obc

    def test_odf_generates_setup_job(self, tmp_path):
        out = _qs(tmp_path, self._spec('odf'))
        setup = (out / 'chart' / 'templates' / 'store' / 'obc-setup.yaml').read_text()
        assert 'kind: Job' in setup
        assert 'helm.sh/hook' in setup

    def test_odf_setup_job_creates_data_connection(self, tmp_path):
        out = _qs(tmp_path, self._spec('odf'))
        setup = (out / 'chart' / 'templates' / 'store' / 'obc-setup.yaml').read_text()
        assert 'store-data-connection' in setup

    def test_odf_creates_rbac_by_default(self, tmp_path):
        out = _qs(tmp_path, self._spec('odf'))
        setup = (out / 'chart' / 'templates' / 'store' / 'obc-setup.yaml').read_text()
        assert 'kind: ServiceAccount' in setup
        assert 'kind: Role' in setup
        assert 'kind: RoleBinding' in setup

    def test_odf_no_rbac_when_flag_false(self, tmp_path):
        spec_file = tmp_path / 'spec.yaml'
        spec_file.write_text(self._spec('odf'))
        from quickpat.pipeline import compose_qs_from_spec
        result = compose_qs_from_spec(
            str(spec_file),
            output_dir=str(tmp_path / 'qs'),
            create_service_account=False,
        )
        assert result.success
        setup = (tmp_path / 'qs' / 'chart' / 'templates' / 'store' / 'obc-setup.yaml').read_text()
        assert 'kind: ServiceAccount' not in setup
        assert 'kind: Role' not in setup
        # Comment explaining manual creation should be present
        assert 'oc create sa' in setup

    def test_odf_pvc_guarded_out(self, tmp_path):
        out = _qs(tmp_path, self._spec('odf'))
        pvc = (out / 'chart' / 'templates' / 'store' / 'pvc.yaml').read_text()
        # PVC only renders for minio — should be empty or guarded out for odf
        assert 'eq .Values.store.provider "minio"' in pvc

    # ── s3 ───────────────────────────────────────────────────────────────────

    def test_s3_no_pvc(self, tmp_path):
        out = _qs(tmp_path, self._spec('s3'))
        pvc = (out / 'chart' / 'templates' / 'store' / 'pvc.yaml').read_text()
        assert 'eq .Values.store.provider "minio"' in pvc  # guarded to minio only

    def test_s3_no_deployment(self, tmp_path):
        out = _qs(tmp_path, self._spec('s3'))
        dep = (out / 'chart' / 'templates' / 'store' / 'deployment.yaml').read_text()
        assert 'eq .Values.store.provider "minio"' in dep  # no MinIO for s3

    def test_s3_data_connection_uses_endpoint_value(self, tmp_path):
        out = _qs(tmp_path, self._spec('s3'))
        dc = (out / 'chart' / 'templates' / 'store' / 'data-connection.yaml').read_text()
        assert '.Values.store.endpoint' in dc

    def test_s3_values_yaml_has_endpoint_field(self, tmp_path):
        out = _qs(tmp_path, self._spec('s3'))
        values = (out / 'chart' / 'values.yaml').read_text()
        assert 'endpoint:' in values
        assert 'region:' in values

    # ── Interface contract ────────────────────────────────────────────────────

    def test_data_connection_always_present_minio(self, tmp_path):
        out = _qs(tmp_path, self._spec('minio'))
        assert (out / 'chart' / 'templates' / 'store' / 'data-connection.yaml').exists()

    def test_data_connection_always_present_odf(self, tmp_path):
        out = _qs(tmp_path, self._spec('odf'))
        assert (out / 'chart' / 'templates' / 'store' / 'data-connection.yaml').exists()

    def test_data_connection_always_present_s3(self, tmp_path):
        out = _qs(tmp_path, self._spec('s3'))
        assert (out / 'chart' / 'templates' / 'store' / 'data-connection.yaml').exists()

    # ── create-secrets.sh ─────────────────────────────────────────────────────

    def test_minio_script_auto_generates_creds(self, tmp_path):
        out = _qs(tmp_path, self._spec('minio'))
        sh = (out / 'scripts' / 'create-secrets.sh').read_text()
        assert 'openssl rand' in sh
        assert 'store-credentials' in sh

    def test_s3_script_prompts_for_creds(self, tmp_path):
        out = _qs(tmp_path, self._spec('s3'))
        sh = (out / 'scripts' / 'create-secrets.sh').read_text()
        assert 'read -rsp' in sh

    def test_odf_script_skips_credentials(self, tmp_path):
        out = _qs(tmp_path, self._spec('odf'))
        sh = (out / 'scripts' / 'create-secrets.sh').read_text()
        assert 'ODF' in sh or 'odf' in sh.lower()
        # ODF should NOT generate credential creation commands
        assert 'store-credentials' not in sh


# ── Data pipeline block ───────────────────────────────────────────────────────


class TestDataPipelineBlock:
    """data_pipeline_templates() generates Tekton Pipeline + Task + RBAC."""

    SPEC = """\
apiVersion: supplychain/v1alpha1
kind: ApplicationSpec
metadata:
  name: dp-test
  tier: sandbox
  upstream:
    repo: https://github.com/example/qs.git
blocks:
  platform:
    type: ai-platform-foundation
    config:
      dsc:
        datasciencepipelines: Managed
  db:
    type: vector-store
    config:
      database: testdb
      port: 5432
  store:
    type: object-storage
    config:
      provider: minio
      bucket: testbucket
  ingest:
    type: data-pipeline
    config:
      sources:
        - name: docs
          type: s3
          config:
            bucket: testbucket
      schedule: manual
      chunk_size: 256
    inputs:
      vector_store: db
      object_storage: store
wiring: []
custom: {}
"""

    SCHEDULED_SPEC = """\
apiVersion: supplychain/v1alpha1
kind: ApplicationSpec
metadata:
  name: dp-scheduled
  tier: sandbox
  upstream:
    repo: https://github.com/example/qs.git
blocks:
  platform:
    type: ai-platform-foundation
  db:
    type: vector-store
    config:
      database: embeddings
      port: 5432
  store:
    type: object-storage
    config:
      provider: minio
      bucket: docs
  ingest:
    type: data-pipeline
    config:
      sources:
        - name: wiki
          type: s3
      schedule: daily
      chunk_size: 512
    inputs:
      vector_store: db
      object_storage: store
wiring: []
custom: {}
"""

    def test_pipeline_yaml_generated(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        assert (out / 'chart' / 'templates' / 'ingest' / 'pipeline.yaml').exists()

    def test_ingest_task_generated(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        assert (out / 'chart' / 'templates' / 'ingest' / 'ingest-task.yaml').exists()

    def test_pipeline_run_template_generated(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        assert (out / 'chart' / 'templates' / 'ingest' / 'pipeline-run.yaml').exists()

    def test_rbac_generated(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        rbac = (out / 'chart' / 'templates' / 'ingest' / 'rbac.yaml').read_text()
        assert 'kind: ServiceAccount' in rbac
        assert 'kind: Role' in rbac
        assert 'kind: RoleBinding' in rbac

    def test_pipeline_has_vector_store_param(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        pl = (out / 'chart' / 'templates' / 'ingest' / 'pipeline.yaml').read_text()
        assert 'vector-store-endpoint' in pl
        assert 'vector-store-db' in pl

    def test_pipeline_has_object_storage_param(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        pl = (out / 'chart' / 'templates' / 'ingest' / 'pipeline.yaml').read_text()
        assert 'object-storage-endpoint' in pl
        assert 'object-storage-bucket' in pl

    def test_input_resolution_vector_store(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        pl = (out / 'chart' / 'templates' / 'ingest' / 'pipeline.yaml').read_text()
        # 'db' vector-store block: port 5432, database testdb
        assert 'db:5432' in pl
        assert 'testdb' in pl

    def test_input_resolution_object_storage(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        pl = (out / 'chart' / 'templates' / 'ingest' / 'pipeline.yaml').read_text()
        # 'store' object-storage block: minio at store:9000, bucket testbucket
        assert 'store:9000' in pl
        assert 'testbucket' in pl

    def test_task_references_vector_store_secret(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        task = (out / 'chart' / 'templates' / 'ingest' / 'ingest-task.yaml').read_text()
        assert 'db-secret' in task

    def test_task_references_object_storage_credentials(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        task = (out / 'chart' / 'templates' / 'ingest' / 'ingest-task.yaml').read_text()
        assert 'store-credentials' in task

    def test_task_uses_helm_image_value(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        task = (out / 'chart' / 'templates' / 'ingest' / 'ingest-task.yaml').read_text()
        assert '{{ .Values.ingest.image }}' in task

    def test_manual_schedule_no_cronjob(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        trigger = (out / 'chart' / 'templates' / 'ingest' / 'trigger.yaml').read_text()
        # Guarded by {{- if ne .Values.ingest.schedule "manual" }}
        assert 'ne .Values.ingest.schedule "manual"' in trigger

    def test_scheduled_pipeline_has_cronjob(self, tmp_path):
        out = _qs(tmp_path, self.SCHEDULED_SPEC)
        trigger = (out / 'chart' / 'templates' / 'ingest' / 'trigger.yaml').read_text()
        assert 'kind: CronJob' in trigger

    def test_values_yaml_has_pipeline_section(self, tmp_path):
        out = _qs(tmp_path, self.SPEC)
        values = (out / 'chart' / 'values.yaml').read_text()
        assert 'ingest:' in values
        assert 'ingestion-pipeline' in values
        assert 'schedule:' in values

    def test_inputs_parsed_from_spec(self, tmp_path):
        """Verify parser correctly captures inputs: block references."""
        spec_file = tmp_path / 'spec.yaml'
        spec_file.write_text(self.SPEC)
        from quickpat.compose.parser import load_application_spec
        spec = load_application_spec(str(spec_file))
        ingest = spec.blocks['ingest']
        assert ingest.inputs.get('vector_store') == 'db'
        assert ingest.inputs.get('object_storage') == 'store'


# ── Top-level secrets (Vault-free QS path) ──────────────────────────────────


TOP_SECRETS_SPEC = """\
apiVersion: supplychain/v1alpha1
kind: ApplicationSpec
metadata:
  name: sec-test
  tier: sandbox
  upstream: {}
blocks:
  platform:
    type: ai-platform-foundation
secrets:
  - name: gemini
    vault_path: sec-test/gemini
    onMissingValue: skip
    fields:
      - name: api_key
  - name: ssh
    vault_path: sec-test/ssh
    fields:
      - name: private_key
      - name: public_key
wiring: []
custom: {}
"""


class TestTopLevelSecrets:
    """Top-level secrets: render as plain Secrets, not Vault ExternalSecrets."""

    def test_secret_template_created_per_secret(self, tmp_path):
        out = _qs(tmp_path, TOP_SECRETS_SPEC)
        assert (out / 'chart' / 'templates' / 'secrets' / 'gemini.yaml').exists()
        assert (out / 'chart' / 'templates' / 'secrets' / 'ssh.yaml').exists()

    def test_secret_template_is_plain_secret_not_external(self, tmp_path):
        out = _qs(tmp_path, TOP_SECRETS_SPEC)
        gemini = (out / 'chart' / 'templates' / 'secrets' / 'gemini.yaml').read_text()
        assert 'kind: Secret' in gemini
        assert 'ExternalSecret' not in gemini
        assert 'name: gemini' in gemini
        assert 'api_key:' in gemini

    def test_values_yaml_has_camelcase_secret_keys(self, tmp_path):
        out = _qs(tmp_path, TOP_SECRETS_SPEC)
        values = (out / 'chart' / 'values.yaml').read_text()
        assert 'geminiApiKey' in values
        assert 'sshPrivateKey' in values
        assert 'sshPublicKey' in values

    def test_template_and_values_keys_agree(self, tmp_path):
        out = _qs(tmp_path, TOP_SECRETS_SPEC)
        gemini = (out / 'chart' / 'templates' / 'secrets' / 'gemini.yaml').read_text()
        # Template must reference the exact key present in values.yaml
        assert '.Values.secrets.geminiApiKey' in gemini

    def test_create_secrets_creates_each_secret(self, tmp_path):
        out = _qs(tmp_path, TOP_SECRETS_SPEC)
        sh = (out / 'scripts' / 'create-secrets.sh').read_text()
        assert 'oc create secret generic gemini' in sh
        assert 'oc create secret generic ssh' in sh
        assert '--from-literal=private_key=' in sh
        assert '--from-literal=public_key=' in sh

    def test_optional_secret_is_guarded(self, tmp_path):
        out = _qs(tmp_path, TOP_SECRETS_SPEC)
        sh = (out / 'scripts' / 'create-secrets.sh').read_text()
        # gemini is onMissingValue: skip -> only created if a value is supplied
        assert 'Skipping gemini' in sh

    def test_required_secret_is_not_guarded(self, tmp_path):
        out = _qs(tmp_path, TOP_SECRETS_SPEC)
        sh = (out / 'scripts' / 'create-secrets.sh').read_text()
        assert 'Skipping ssh' not in sh

    def test_create_secrets_is_valid_bash(self, tmp_path):
        import subprocess
        out = _qs(tmp_path, TOP_SECRETS_SPEC)
        sh = out / 'scripts' / 'create-secrets.sh'
        r = subprocess.run(['bash', '-n', str(sh)], capture_output=True, text=True)
        assert r.returncode == 0, f"bash syntax error: {r.stderr}"

    def test_no_vault_references_anywhere(self, tmp_path):
        out = _qs(tmp_path, TOP_SECRETS_SPEC)
        for path in (out / 'chart').rglob('*.yaml'):
            text = path.read_text()
            assert 'ExternalSecret' not in text, f"ExternalSecret leaked into {path}"
            assert 'vaultPrefix' not in text, f"vaultPrefix leaked into {path}"


class TestExternalSecretStripping:
    """Custom-chart ExternalSecrets are dropped from the QS output."""

    def test_is_external_secret_detects_kind(self, tmp_path):
        from quickpat.compose.qs_generator import QSGenerator
        es = tmp_path / 'es.yaml'
        es.write_text('apiVersion: external-secrets.io/v1\nkind: ExternalSecret\n')
        plain = tmp_path / 'plain.yaml'
        plain.write_text('apiVersion: v1\nkind: Secret\n')
        assert QSGenerator._is_external_secret(es) is True
        assert QSGenerator._is_external_secret(plain) is False

    def test_copy_skips_external_secrets(self, tmp_path):
        from quickpat.compose.qs_generator import QSGenerator
        src = tmp_path / 'src'
        (src).mkdir()
        (src / 'es.yaml').write_text('kind: ExternalSecret\n')
        (src / 'keep.yaml').write_text('kind: ConfigMap\n')
        dst = tmp_path / 'dst'
        gen = QSGenerator.__new__(QSGenerator)  # no __init__ needed for static/instance copy
        gen._copy_templates_no_externalsecrets(src, dst)
        assert (dst / 'keep.yaml').exists()
        assert not (dst / 'es.yaml').exists()

    def test_is_external_secret_multidoc_mixed_is_not_pure(self, tmp_path):
        """A file mixing a Secret and an ExternalSecret is not a pure-ES file."""
        from quickpat.compose.qs_generator import QSGenerator
        mixed = tmp_path / 'mixed.yaml'
        mixed.write_text(
            'apiVersion: v1\nkind: Secret\nmetadata:\n  name: keep\n'
            '---\n'
            'apiVersion: external-secrets.io/v1\nkind: ExternalSecret\n'
            'metadata:\n  name: drop\n'
        )
        assert QSGenerator._is_external_secret(mixed) is False

    def test_copy_strips_only_external_secret_doc_from_multidoc(self, tmp_path):
        """Mixed multi-doc file: Secret is kept, ExternalSecret doc removed."""
        from quickpat.compose.qs_generator import QSGenerator
        src = tmp_path / 'src'
        src.mkdir()
        (src / 'mixed.yaml').write_text(
            'apiVersion: v1\nkind: Secret\nmetadata:\n  name: keep\n'
            '---\n'
            'apiVersion: external-secrets.io/v1\nkind: ExternalSecret\n'
            'metadata:\n  name: drop\n'
        )
        dst = tmp_path / 'dst'
        gen = QSGenerator.__new__(QSGenerator)
        gen._copy_templates_no_externalsecrets(src, dst)
        out = (dst / 'mixed.yaml').read_text()
        assert 'kind: Secret' in out
        assert 'ExternalSecret' not in out
        assert 'name: keep' in out
        assert 'name: drop' not in out

    def test_copy_drops_file_of_only_external_secrets(self, tmp_path):
        """A multi-doc file that is all ExternalSecrets is dropped entirely."""
        from quickpat.compose.qs_generator import QSGenerator
        src = tmp_path / 'src'
        src.mkdir()
        (src / 'all_es.yaml').write_text(
            'kind: ExternalSecret\nmetadata:\n  name: a\n'
            '---\n'
            'kind: ExternalSecret\nmetadata:\n  name: b\n'
        )
        dst = tmp_path / 'dst'
        gen = QSGenerator.__new__(QSGenerator)
        gen._copy_templates_no_externalsecrets(src, dst)
        assert not (dst / 'all_es.yaml').exists()


class TestSpecValidationOnQSPath:
    """compose_qs_from_spec must run the same spec-level validation gate as
    compose_from_spec (VP path) — regression test for the QS path silently
    skipping validate_spec and compiling invalid specs."""

    INVALID_WIRING_SPEC = """\
apiVersion: supplychain/v1alpha1
kind: ApplicationSpec
metadata:
  name: qs-invalid-wiring
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
wiring:
  - from: platform
    to: does-not-exist
custom: {}
"""

    def test_invalid_wiring_reference_aborts_qs_compose(self, tmp_path):
        spec_file = tmp_path / 'spec.yaml'
        spec_file.write_text(self.INVALID_WIRING_SPEC)
        out_dir = tmp_path / 'qs-out'

        result = compose_qs_from_spec(str(spec_file), output_dir=str(out_dir))

        assert not result.success
        assert any('spec:error' in w for w in result.warnings)
        assert not out_dir.exists()


def test_secret_value_key_normalises_snake_and_kebab():
    from quickpat.compose.qs_generator import _secret_value_key
    assert _secret_value_key('gemini', 'api_key') == 'geminiApiKey'
    assert _secret_value_key('brave-search', 'api_key') == 'braveSearchApiKey'
    assert _secret_value_key('vertex', 'sa_json') == 'vertexSaJson'
    assert _secret_value_key('ssh', 'private_key') == 'sshPrivateKey'
