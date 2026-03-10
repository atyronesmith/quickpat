"""Generates Validated Pattern files from quickstart analysis."""

import shutil
from pathlib import Path

import yaml

from .analyzer import QuickstartAnalysis, ChartInfo
from .config import get as cfg
from .operators import OPERATORS


class PatternGenerator:
    """Generates a complete Validated Pattern directory structure."""

    def __init__(self, analysis: QuickstartAnalysis, config: dict):
        self.analysis = analysis
        self.config = config
        self.output_dir = Path(config['output_dir']).resolve()

    def generate(self):
        """Generate all pattern files."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._generate_values_global()
        self._generate_values_hub()
        self._generate_values_secret_template()
        self._generate_makefile()
        self._generate_makefile_common()
        self._generate_pattern_sh()
        self._generate_pattern_metadata()
        self._generate_ansible_cfg()
        self._generate_ansible_lint()
        self._generate_gitignore()
        self._generate_overrides()
        self._generate_report()

        if self.config.get('chart_strategy') == 'local':
            self._copy_chart_locally()

    # ── values-global.yaml ──────────────────────────────────────────

    def _generate_values_global(self):
        # main: is a root-level key, NOT nested under global:
        data = {
            'global': {
                'pattern': self.config['pattern_name'],
                'options': {
                    'useCSV': False,
                    'syncPolicy': 'Automatic',
                    'installPlanApproval': 'Automatic',
                },
            },
            'main': {
                'clusterGroupName': 'hub',
                'multiSourceConfig': {
                    'enabled': True,
                    'clusterGroupChartVersion': self.config.get(
                        'clustergroup_version',
                        cfg("pattern.clustergroup_version", "0.9.*"),
                    ),
                },
            },
        }
        self._write_yaml(
            self.output_dir / 'values-global.yaml', data, doc_start=True
        )

    # ── values-hub.yaml ─────────────────────────────────────────────

    def _generate_values_hub(self):
        operators = self.config.get('operators', [])
        app_namespace = self.config.get('app_namespace', self.analysis.name)
        app_name = self.config.get('app_name', self.analysis.name)
        use_vault = self.config.get('use_vault', False)

        namespaces = self._build_namespaces(operators, app_namespace, use_vault)
        subscriptions = self._build_subscriptions(operators)
        applications = self._build_applications(
            app_name, app_namespace, use_vault
        )

        data = {
            'clusterGroup': {
                'name': 'hub',
                'isHubCluster': True,
                'namespaces': namespaces,
                'subscriptions': subscriptions,
                'projects': ['hub'],
                'sharedValueFiles': [
                    '/overrides/values-{{ $.Values.global.clusterPlatform }}.yaml',
                ],
                'applications': applications,
            }
        }
        self._write_yaml(self.output_dir / 'values-hub.yaml', data)

    def _get_app_charts(self):
        """Return list of (app_name, namespace, ChartInfo) for all charts."""
        if len(self.analysis.charts) > 1:
            return [(ci.name, ci.group or ci.name, ci) for ci in self.analysis.charts]
        ci = self.analysis.charts[0]
        app_name = self.config.get('app_name', self.analysis.name)
        app_ns = self.config.get('app_namespace', self.analysis.name)
        return [(app_name, app_ns, ci)]

    def _build_namespaces(self, operators, app_namespace, use_vault):
        namespaces = []
        seen = set()

        # Infrastructure namespaces
        if use_vault:
            for ns in ('vault', 'golang-external-secrets'):
                namespaces.append(ns)
                seen.add(ns)

        # Operator namespaces
        for op_key in operators:
            op = OPERATORS[op_key]
            ns = op['namespace']
            if ns in seen or ns == 'openshift-operators':
                continue
            seen.add(ns)

            ns_config = op.get('namespace_config')
            if ns_config:
                namespaces.append({ns: ns_config})
            else:
                namespaces.append(ns)

        # Application namespaces — only add OAI labels where needed
        # Pre-compute: if ANY chart in a namespace needs labels, the namespace gets them
        app_charts = self._get_app_charts()
        ns_needs_labels = set()
        for _, ns, ci in app_charts:
            if ci.needs_oai_labels:
                ns_needs_labels.add(ns)

        for _, ns, ci in app_charts:
            if ns in seen:
                continue
            seen.add(ns)
            if ns in ns_needs_labels:
                namespaces.append({ns: {
                    'operatorGroup': True,
                    'targetNamespaces': [ns],
                    'labels': {
                        'opendatahub.io/dashboard': 'true',
                        'modelmesh-enabled': 'false',
                    },
                }})
            else:
                namespaces.append(ns)

        return namespaces

    def _build_subscriptions(self, operators):
        subscriptions = {}
        for op_key in operators:
            op = OPERATORS[op_key]
            sub = {
                'name': op['subscription_name'],
                'namespace': op['namespace'],
            }
            if op.get('source') and op['source'] != 'redhat-operators':
                sub['source'] = op['source']
            subscriptions[op_key] = sub
        return subscriptions

    def _build_applications(self, app_name, app_namespace, use_vault):
        applications = {}

        # Vault and external secrets (standard infrastructure)
        if use_vault:
            applications['vault'] = {
                'name': 'vault',
                'namespace': 'vault',
                'project': 'hub',
                'chart': 'hashicorp-vault',
                'chartVersion': cfg(
                    "infrastructure.vault_chart_version", "0.1.*"
                ),
            }
            applications['golang-external-secrets'] = {
                'name': 'golang-external-secrets',
                'namespace': 'golang-external-secrets',
                'project': 'hub',
                'chart': 'golang-external-secrets',
                'chartVersion': cfg(
                    "infrastructure.external_secrets_chart_version", "0.2.*"
                ),
            }

        # Application chart(s)
        for name, ns, _ in self._get_app_charts():
            if self.config.get('chart_strategy') == 'local':
                applications[name] = {
                    'name': name,
                    'namespace': ns,
                    'project': 'hub',
                    'path': f'charts/all/{name}',
                }
            else:
                applications[name] = {
                    'name': name,
                    'namespace': ns,
                    'project': 'hub',
                    'repoURL': self.config.get('chart_repo_url', ''),
                    'chart': name,
                    'targetRevision': self.config.get(
                        'chart_version', self.analysis.version
                    ),
                }

        return applications

    # ── values-secret.yaml.template ─────────────────────────────────

    def _generate_values_secret_template(self):
        if not self.config.get('use_vault'):
            return

        fields = []
        seen_names = {}  # name -> count
        for secret in self.analysis.detected_secrets:
            name = secret.name
            if name in seen_names:
                # Disambiguate using the path: rag.pgvector.secret.password -> pgvector_password
                parts = [p for p in secret.path.replace('[', '.').replace(']', '').split('.')
                         if p and p != name]
                # Use the last meaningful path segment + name
                if parts:
                    name = f"{parts[-1]}_{name}"
                # If still a dupe, add a counter
                if name in seen_names:
                    seen_names[name] += 1
                    name = f"{name}_{seen_names[name]}"
            seen_names[name] = 1
            fields.append({
                'name': name,
                'onMissingValue': 'prompt',
            })

        if not fields:
            # Generate a placeholder secret
            fields.append({
                'name': 'secret',
                'onMissingValue': 'generate',
                'vaultPolicy': 'validatedPatternDefaultPolicy',
            })

        data = {
            'version': '2.0',
            'secrets': [{
                'name': f"{self.config['pattern_name']}-secrets",
                'vaultPrefixes': ['global'],
                'fields': fields,
            }],
        }
        self._write_yaml(
            self.output_dir / 'values-secret.yaml.template', data
        )

    # ── Makefile ────────────────────────────────────────────────────

    def _generate_makefile(self):
        content = (
            "include Makefile-common\n"
        )
        (self.output_dir / 'Makefile').write_text(content)

    def _generate_makefile_common(self):
        # Matches the standard Makefile-common from validated patterns
        content = (
            'MAKEFLAGS += --no-print-directory\n'
            'ANSIBLE_STDOUT_CALLBACK ?= null\n'
            'ANSIBLE_RUN := ANSIBLE_STDOUT_CALLBACK='
            '$(ANSIBLE_STDOUT_CALLBACK) ansible-playbook '
            '$(EXTRA_PLAYBOOK_OPTS)\n'
            '\n'
            '.PHONY: help\n'
            'help: ## Print this help message\n'
            '\t@awk \'BEGIN {FS = ":.*##"; printf "\\nUsage:\\n'
            '  make \\033[36m<target>\\033[0m\\n"} '
            '/^(\\s|[a-zA-Z_0-9-])+:.*?##/ '
            '{ printf "  \\033[36m%-35s\\033[0m %s\\n", $$1, $$2 } '
            '/^##@/ { printf "\\n\\033[1m%s\\033[0m\\n", '
            'substr($$0, 5) } \' $(MAKEFILE_LIST)\n'
            '\n'
            '##@ Pattern Install Tasks\n'
            '.PHONY: show\n'
            'show: ## Shows the template that would be applied\n'
            '\t@$(ANSIBLE_RUN) rhvp.cluster_utils.show\n'
            '\n'
            '.PHONY: operator-deploy\n'
            'operator-deploy operator-upgrade: '
            '## Installs/updates the pattern (no secrets)\n'
            '\t@$(ANSIBLE_RUN) rhvp.cluster_utils.operator_deploy\n'
            '\n'
            '.PHONY: install\n'
            'install: pattern-install '
            '## Installs the pattern onto a cluster\n'
            '\n'
            '.PHONY: uninstall\n'
            'uninstall: ## Uninstall notice\n'
            '\t@echo "Uninstall is not yet implemented."\n'
            '\n'
            '.PHONY: pattern-install\n'
            'pattern-install:\n'
            '\t@$(ANSIBLE_RUN) rhvp.cluster_utils.install\n'
            '\n'
            '.PHONY: load-secrets\n'
            'load-secrets: ## Loads secrets onto the cluster\n'
            '\t@$(ANSIBLE_RUN) rhvp.cluster_utils.load_secrets\n'
            '\n'
            '##@ Validation Tasks\n'
            '.PHONY: validate-prereq\n'
            'validate-prereq: ## Verify pre-requisites\n'
            '\t@$(ANSIBLE_RUN) rhvp.cluster_utils.validate_prereq\n'
            '\n'
            '.PHONY: validate-origin\n'
            'validate-origin: ## Verify the git origin is available\n'
            '\t@$(ANSIBLE_RUN) rhvp.cluster_utils.validate_origin\n'
            '\n'
            '.PHONY: validate-cluster\n'
            'validate-cluster: ## Do cluster validations before install\n'
            '\t@$(ANSIBLE_RUN) rhvp.cluster_utils.validate_cluster\n'
            '\n'
            '.PHONY: validate-schema\n'
            'validate-schema: ## Validates values files against schema\n'
            '\t@$(ANSIBLE_RUN) rhvp.cluster_utils.validate_schema\n'
            '\n'
            '.PHONY: argo-healthcheck\n'
            'argo-healthcheck: ## Checks if all argo apps are synced\n'
            '\t@$(ANSIBLE_RUN) rhvp.cluster_utils.argo_healthcheck\n'
        )
        (self.output_dir / 'Makefile-common').write_text(content)

    # ── pattern.sh ──────────────────────────────────────────────────

    def _generate_pattern_sh(self):
        # Standard utility container runner, identical across all patterns
        content = r'''#!/bin/bash
set -euo pipefail

function is_available {
  command -v "$1" >/dev/null 2>&1 || { echo >&2 "$1 is required but it's not installed. Aborting."; exit 1; }
}

function version {
    echo "$1" | awk -F. '{ printf("%d%03d%03d%03d\n", $1,$2,$3,$4); }'
}

if [ -z "${PATTERN_UTILITY_CONTAINER:-}" ]; then
	PATTERN_UTILITY_CONTAINER="quay.io/validatedpatterns/utility-container"
fi
if [ -n "${PATTERN_DISCONNECTED_HOME:-}" ]; then
    PATTERN_UTILITY_CONTAINER="${PATTERN_DISCONNECTED_HOME}/utility-container"
    PATTERN_INSTALL_CHART="oci://${PATTERN_DISCONNECTED_HOME}/pattern-install"
    echo "PATTERN_DISCONNECTED_HOME is set to ${PATTERN_DISCONNECTED_HOME}"
    echo "Setting the following variables:"
    echo "  PATTERN_UTILITY_CONTAINER: ${PATTERN_UTILITY_CONTAINER}"
    echo "  PATTERN_INSTALL_CHART: ${PATTERN_INSTALL_CHART}"
fi

readonly commands=(podman)
for cmd in "${commands[@]}"; do is_available "$cmd"; done

UNSUPPORTED_PODMAN_VERSIONS="1.6 1.5"
PODMAN_VERSION_STR=$(podman --version) || { echo "Failed to get podman version"; exit 1; }
for i in ${UNSUPPORTED_PODMAN_VERSIONS}; do
	if echo "${PODMAN_VERSION_STR}" | grep -q -E "\b${i}"; then
		echo "Unsupported podman version. We recommend > 4.3.0"
		podman --version
		exit 1
	fi
done

PODMAN_VERSION=$(echo "${PODMAN_VERSION_STR}" | awk '{ print $NF }')

PODMAN_ARGS=()
if [ "$(version "${PODMAN_VERSION}")" -lt "$(version "4.3.0")" ]; then
    PODMAN_ARGS=(-v "${HOME}:/root")
else
    MYNAME=$(id -n -u)
    MYUID=$(id -u)
    MYGID=$(id -g)
    PODMAN_ARGS=(--passwd-entry "${MYNAME}:x:${MYUID}:${MYGID}::/pattern-home:/bin/bash" --user "${MYUID}:${MYGID}" --userns "keep-id:uid=${MYUID},gid=${MYGID}")
fi

if [ -n "${KUBECONFIG:-}" ]; then
    if [[ ! "${KUBECONFIG}" =~ ^"${HOME}" ]]; then
        echo "${KUBECONFIG} is pointing outside of the HOME folder, this will make it unavailable from the container."
        echo "Please move it somewhere inside your $HOME folder, as that is what gets bind-mounted inside the container"
        exit 1
    fi
fi

REMOTE_PODMAN=$(podman system connection list | tail -n +2 | wc -l) || REMOTE_PODMAN=0
PKI_HOST_MOUNT_ARGS=()
if [ "${REMOTE_PODMAN}" -eq 0 ]; then
    if [ -d /etc/pki/tls ]; then
        PKI_HOST_MOUNT_ARGS=(-v /etc/pki:/etc/pki:ro)
    elif [ -d /etc/ssl ]; then
        PKI_HOST_MOUNT_ARGS=(-v /etc/ssl:/etc/ssl:ro)
    else
        PKI_HOST_MOUNT_ARGS=(-v /usr/share/ca-certificates:/usr/share/ca-certificates:ro)
    fi
fi

EXTRA_ARGS_ARRAY=()
if [ -n "${EXTRA_ARGS:-}" ]; then
    # shellcheck disable=SC2206
    EXTRA_ARGS_ARRAY=(${EXTRA_ARGS})
fi

podman run -it --rm --pull=newer \
    --security-opt label=disable \
    -e ANSIBLE_STDOUT_CALLBACK \
    -e DISABLE_VALIDATE_ORIGIN \
    -e EXTRA_HELM_OPTS \
    -e EXTRA_PLAYBOOK_OPTS \
    -e K8S_AUTH_HOST \
    -e K8S_AUTH_PASSWORD \
    -e K8S_AUTH_SSL_CA_CERT \
    -e K8S_AUTH_TOKEN \
    -e K8S_AUTH_USERNAME \
    -e K8S_AUTH_VERIFY_SSL \
    -e KUBECONFIG \
    -e PATTERN_DIR \
    -e PATTERN_DISCONNECTED_HOME \
    -e PATTERN_INSTALL_CHART \
    -e PATTERN_NAME \
    -e TARGET_BRANCH \
    -e TARGET_CLUSTERGROUP \
    -e TARGET_ORIGIN \
    -e TOKEN_NAMESPACE \
    -e TOKEN_SECRET \
    -e UUID_FILE \
    -e VALUES_SECRET \
    "${PKI_HOST_MOUNT_ARGS[@]}" \
    -v "$(pwd -P)":"$(pwd -P)" \
    -v "${HOME}":"${HOME}" \
    -v "${HOME}":/pattern-home \
    "${PODMAN_ARGS[@]}" \
    "${EXTRA_ARGS_ARRAY[@]}" \
    -w "$(pwd -P)" \
    "$PATTERN_UTILITY_CONTAINER" \
    "$@"
'''
        script_path = self.output_dir / 'pattern.sh'
        script_path.write_text(content)
        script_path.chmod(0o755)

    # ── pattern-metadata.yaml ───────────────────────────────────────

    def _generate_pattern_metadata(self):
        pattern_name = self.config['pattern_name']
        display_name = pattern_name.replace('-', ' ').title()

        data = {
            'metadata_version': '1.0',
            'name': pattern_name,
            'pattern_version': '1.0',
            'display_name': display_name,
            'tier': 'sandbox',
        }
        self._write_yaml(self.output_dir / 'pattern-metadata.yaml', data)

    # ── ansible.cfg ─────────────────────────────────────────────────

    def _generate_ansible_cfg(self):
        content = (
            '[defaults]\n'
            'localhost_warning=False\n'
            'retry_files_enabled=False\n'
            'interpreter_python=auto_silent\n'
            'timeout=30\n'
            'library=~/.ansible/plugins/modules'
            ':./ansible/plugins/modules'
            ':/usr/share/ansible/plugins/modules\n'
            'roles_path=~/.ansible/roles'
            ':./ansible/roles'
            ':/usr/share/ansible/roles'
            ':/etc/ansible/roles\n'
            'filter_plugins=~/.ansible/plugins/filter'
            ':./ansible/plugins/filter'
            ':/usr/share/ansible/plugins/filter\n'
            'collections_path=/usr/share/ansible/collections\n'
        )
        (self.output_dir / 'ansible.cfg').write_text(content)

    def _generate_ansible_lint(self):
        (self.output_dir / '.ansible-lint').write_text('')

    # ── .gitignore ──────────────────────────────────────────────────

    def _generate_gitignore(self):
        content = (
            '*~\n'
            '*.swp\n'
            '*.swo\n'
            'values-secret*\n'
            '.*.expected.yaml\n'
            'pattern-vault.init\n'
            'vault.init\n'
            'super-linter.log\n'
        )
        (self.output_dir / '.gitignore').write_text(content)

    # ── overrides/ ──────────────────────────────────────────────────

    def _generate_overrides(self):
        overrides_dir = self.output_dir / 'overrides'
        overrides_dir.mkdir(exist_ok=True)

        # Platform override placeholders referenced by sharedValueFiles
        for platform in cfg("platforms", ["AWS", "Azure", "GCP", "IBMCloud", "None"]):
            path = overrides_dir / f'values-{platform}.yaml'
            if not path.exists():
                path.write_text(
                    f'# Platform-specific overrides for {platform}\n'
                )

    # ── charts ──────────────────────────────────────────────────────

    def _copy_chart_locally(self):
        for ci in self.analysis.charts:
            dest = self.output_dir / 'charts' / 'all' / ci.name
            src = Path(ci.chart_path)

            if dest.exists():
                shutil.rmtree(dest)

            shutil.copytree(src, dest)

    # ── docs report ─────────────────────────────────────────────────

    def _generate_report(self):
        docs_dir = self.output_dir / 'docs'
        docs_dir.mkdir(exist_ok=True)
        report = build_report(self.analysis, self.config)
        (docs_dir / 'quickstart-analysis.md').write_text(report)

    # ── helpers ──────────────────────────────────────────────────────

    def _write_yaml(self, path, data, doc_start=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w') as f:
            if doc_start:
                f.write('---\n')
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def build_report(analysis, config=None):
    """Build a markdown report from an analysis and optional config."""
    lines = []
    name = analysis.name
    lines.append(f'# {name}')
    lines.append('')
    if analysis.description:
        lines.append(analysis.description)
        lines.append('')

    lines.append(f'- **Version:** {analysis.version}')
    lines.append(f'- **Source:** `{analysis.chart_path}`')
    lines.append('')

    # Architecture
    lines.append('## Architecture')
    lines.append('')
    components = []
    if analysis.has_llm_service:
        components.append(
            '- **LLM Serving** - Model inference endpoint '
            '(e.g. vLLM, llama-stack)'
        )
    if analysis.has_vector_db:
        components.append(
            '- **Vector Database** - Stores document embeddings '
            'for similarity search'
        )
    if analysis.has_object_storage:
        components.append(
            '- **Object Storage** - S3-compatible storage '
            'for raw documents and artifacts'
        )
    if analysis.has_pipeline:
        components.append(
            '- **Data Pipeline** - Automated document '
            'ingestion, chunking, and embedding'
        )
    if components:
        lines.append('This quickstart provides the following capabilities:')
        lines.append('')
        lines.extend(components)
        lines.append('')
    if analysis.has_gpu_requirement:
        lines.append('> **Note:** This quickstart requires GPU resources.')
        lines.append('')

    # Dependencies
    if analysis.dependencies:
        lines.append('## Helm Dependencies')
        lines.append('')
        lines.append('| Chart | Version | Repository |')
        lines.append('|-------|---------|------------|')
        for dep in analysis.dependencies:
            repo = dep.repository or 'local'
            lines.append(f'| {dep.name} | {dep.version} | {repo} |')
        lines.append('')

    # Operators
    if analysis.detected_operators:
        lines.append('## Required OpenShift Operators')
        lines.append('')
        lines.append(
            'The following operators are automatically installed '
            'by the Validated Pattern:'
        )
        lines.append('')
        lines.append('| Operator | Subscription | Channel | Source |')
        lines.append('|----------|-------------|---------|--------|')
        for op_key in analysis.detected_operators:
            op = OPERATORS[op_key]
            lines.append(
                f"| {op['display_name']} | "
                f"{op['subscription_name']} | "
                f"{op['channel']} | "
                f"{op.get('source', 'redhat-operators')} |"
            )
        lines.append('')

    # Secrets
    if analysis.detected_secrets:
        lines.append('## Secrets Configuration')
        lines.append('')
        lines.append(
            'The following secrets were detected and should be '
            'configured before deployment:'
        )
        lines.append('')
        lines.append('| Secret | Values Path | Action |')
        lines.append('|--------|-------------|--------|')
        for s in analysis.detected_secrets:
            lines.append(
                f'| `{s.name}` | `{s.path}` | Set via Vault or values |'
            )
        lines.append('')

    # Framework architecture (always included)
    lines.append('## Framework Architecture')
    lines.append('')
    lines.append(
        'This pattern uses the **multisource configuration** approach. '
        'Infrastructure Helm charts (clustergroup, vault, external-secrets) '
        'are pulled dynamically from the upstream Validated Patterns registry '
        'rather than stored locally. This means:'
    )
    lines.append('')
    lines.append(
        '- No fork of multicloud-gitops required'
    )
    lines.append(
        '- Upstream bug fixes are received by bumping `clusterGroupChartVersion`'
    )
    lines.append(
        '- No `common/` git subtree needed '
        '(modern patterns use Ansible collections in the utility container)'
    )
    lines.append('')
    lines.append(
        'The `pattern.sh` script runs all make targets inside a '
        'podman-based utility container (`quay.io/validatedpatterns/utility-container`) '
        'which includes the `rhvp.cluster_utils` Ansible collection '
        'and all required tooling.'
    )
    lines.append('')
    lines.append(
        '> **Note:** The multisource feature is not yet documented on '
        'validatedpatterns.io but is used by all current production patterns '
        '(multicloud-gitops, rag-llm-gitops) and documented in the '
        '[common repo README]'
        '(https://github.com/validatedpatterns/common).'
    )
    lines.append('')

    # Pattern config (only when generated via create)
    if config:
        lines.append('## Pattern Configuration')
        lines.append('')
        lines.append(f"- **Pattern name:** {config['pattern_name']}")
        lines.append(f"- **Application name:** {config.get('app_name', name)}")
        lines.append(
            f"- **Namespace:** {config.get('app_namespace', name)}"
        )
        lines.append(
            f"- **Chart strategy:** {config.get('chart_strategy', 'local')}"
        )
        lines.append(
            f"- **Vault enabled:** {config.get('use_vault', False)}"
        )
        lines.append('')
        lines.append('## Deployment')
        lines.append('')
        lines.append('```bash')
        lines.append('git init && git add -A && git commit -m "Initial pattern"')
        lines.append('oc login <cluster>')
        lines.append('./pattern.sh make install')
        lines.append('```')
        lines.append('')

    return '\n'.join(lines)
