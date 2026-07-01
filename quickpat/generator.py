"""Generates Validated Pattern files from quickstart analysis."""

import re
import shutil
from pathlib import Path

import yaml

from .analyzer import QuickstartAnalysis, ChartInfo
from .config import get as cfg
from .operators import OPERATORS, INFRA_CHARTS


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
        self._generate_infra_charts()
        self._generate_scripts()
        self._generate_readme()
        self._generate_report()
        self._generate_license()

        # Copy charts with local strategy (per-chart or global default)
        default_strategy = self.config.get('chart_strategy', 'remote')
        has_local = any(
            (ci.strategy or default_strategy) == 'local'
            for ci in self.analysis.charts
        )
        if has_local:
            self._copy_chart_locally()

        # Remote strategy: {app-name}-secrets chart + override file
        has_remote = any(
            (ci.strategy or default_strategy) == 'remote'
            for ci in self.analysis.charts
        )
        if has_remote and self.config.get('use_vault'):
            self._generate_pattern_secrets_chart()
            self._generate_app_override_file()

    # ── values-global.yaml ──────────────────────────────────────────

    def _generate_values_global(self):
        data = {
            'global': {
                'pattern': self.config['pattern_name'],
                'singleArgoCD': True,
                'secretLoader': {'disabled': False},
            },
            'main': {
                'clusterGroupName': self.config.get(
                    'cluster_group_name', 'hub'
                ),
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

    # ── values-{clusterGroupName}.yaml ────────────────────────────────

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

        group_name = self.config.get('cluster_group_name', 'prod')
        data = {
            'clusterGroup': {
                'name': group_name,
                'namespaces': namespaces,
                'subscriptions': subscriptions,
                'applications': applications,
            }
        }
        self._write_yaml(
            self.output_dir / f'values-{group_name}.yaml', data
        )

    def _get_app_charts(self):
        """Return list of (app_name, namespace, ChartInfo) for all charts."""
        ns_overrides = self.config.get('namespace_overrides', {})
        if len(self.analysis.charts) > 1:
            result = []
            for ci in self.analysis.charts:
                ns = ns_overrides.get(ci.name, ci.group or ci.name)
                result.append((ci.name, ns, ci))
            return result
        ci = self.analysis.charts[0]
        app_name = self.config.get('app_name', self.analysis.name)
        app_ns = ns_overrides.get(ci.name, self.config.get('app_namespace', self.analysis.name))
        return [(app_name, app_ns, ci)]

    def _build_namespaces(self, operators, app_namespace, use_vault):
        namespaces = []
        seen = set()

        # Infrastructure namespaces
        if use_vault:
            for ns in ('vault',):
                namespaces.append({ns: {}})
                seen.add(ns)
            namespaces.append({'external-secrets-operator': {
                'operatorGroup': True,
                'targetNamespaces': [],
            }})
            seen.add('external-secrets-operator')
            namespaces.append({'external-secrets': {}})
            seen.add('external-secrets')

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
                namespaces.append({ns: {}})

        # Application namespaces — only add OAI labels where needed
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
                namespaces.append({ns: {}})

        return namespaces

    def _build_subscriptions(self, operators):
        subscriptions = {}
        for op_key in operators:
            op = OPERATORS[op_key]
            sub_key = op.get('subscription_key', op_key)
            sub = {
                'name': op['subscription_name'],
                'namespace': op['namespace'],
            }
            if op.get('source') and op['source'] != 'redhat-operators':
                sub['source'] = op['source']
            subscriptions[sub_key] = sub

        if self.config.get('use_vault'):
            subscriptions['openshift-external-secrets'] = {
                'name': 'openshift-external-secrets-operator',
                'namespace': 'external-secrets-operator',
                'channel': 'stable-v1',
            }

        return subscriptions

    def _build_applications(self, app_name, app_namespace, use_vault):
        applications = {}
        group_name = self.config.get('cluster_group_name', 'prod')

        # Vault and external secrets (standard infrastructure)
        if use_vault:
            applications['vault'] = {
                'name': 'vault',
                'namespace': 'vault',
                'project': group_name,
                'chart': 'hashicorp-vault',
                'chartVersion': cfg(
                    "infrastructure.vault_chart_version", "0.1.*"
                ),
            }
            applications['openshift-external-secrets'] = {
                'name': 'openshift-external-secrets',
                'namespace': 'external-secrets',
                'project': group_name,
                'chart': 'openshift-external-secrets',
                'chartVersion': cfg(
                    "infrastructure.external_secrets_chart_version", "0.0.*"
                ),
            }

        # Infrastructure config charts (operator CRs)
        operators = self.config.get('operators', [])
        for op_key in operators:
            if op_key in INFRA_CHARTS:
                ic = INFRA_CHARTS[op_key]
                chart_name = ic['chart_name']
                applications[chart_name] = {
                    'name': chart_name,
                    'namespace': ic['namespace'],
                    'project': group_name,
                    'path': f'charts/{chart_name}',
                }

        # Application chart(s)
        default_strategy = self.config.get('chart_strategy', 'remote')
        has_remote = False
        for name, ns, ci in self._get_app_charts():
            strategy = ci.strategy or default_strategy
            if strategy == 'local':
                applications[name] = {
                    'name': name,
                    'namespace': ns,
                    'project': group_name,
                    'path': f'charts/all/{name}',
                }
            elif strategy == 'remote':
                has_remote = True
                git_url = self.config.get('git_repo_url', '')
                chart_path = self.config.get('chart_path_in_repo', '') or '.'
                app_entry = {
                    'name': name,
                    'namespace': ns,
                    'project': group_name,
                    'repoURL': git_url,
                    'path': chart_path,
                    'chartVersion': self.config.get('chart_branch', 'main'),
                }
                extra_files = self.config.get('extra_value_files')
                if extra_files:
                    app_entry['extraValueFiles'] = extra_files
                ignore_diffs = self.config.get('ignore_differences')
                if ignore_diffs:
                    app_entry['ignoreDifferences'] = ignore_diffs
                applications[name] = app_entry
            else:
                repo_url = ci.repo_url or self.config.get('chart_repo_url', '')
                applications[name] = {
                    'name': name,
                    'namespace': ns,
                    'project': group_name,
                    'repoURL': repo_url,
                    'chart': name,
                    'targetRevision': self.config.get(
                        'chart_version', ci.version or self.analysis.version
                    ),
                }

        # Secrets chart for remote strategy with vault
        if has_remote and use_vault:
            app_namespace = self.config.get('app_namespace', self.analysis.name)
            secrets_chart_name = f"{app_name}-secrets"
            applications[secrets_chart_name] = {
                'name': secrets_chart_name,
                'namespace': app_namespace,
                'project': group_name,
                'path': f'charts/{secrets_chart_name}',
            }

        return applications

    # ── values-secret.yaml.template ─────────────────────────────────

    def _generate_values_secret_template(self):
        if not self.config.get('use_vault'):
            return

        # Remote strategy: grouped secrets by service name
        default_strategy = self.config.get('chart_strategy', 'remote')
        has_remote = any(
            (ci.strategy or default_strategy) == 'remote'
            for ci in self.analysis.charts
        )
        if has_remote and self.config.get('secret_groups'):
            self._generate_grouped_secret_template()
            return

        # Local/external strategy: flat secret list
        self._generate_flat_secret_template()

    def _generate_grouped_secret_template(self):
        """Generate values-secret.yaml.template with per-service grouping."""
        vault_prefix = self.config.get('vault_prefix', 'hub')
        secret_groups = self.config.get('secret_groups', {})

        secrets = []
        for group_name, fields in secret_groups.items():
            group_fields = []
            for f in fields:
                if f.get('computed'):
                    continue
                entry = {'name': f['name']}
                classification = f.get('classification', 'prompt')
                if classification == 'static-config':
                    entry['value'] = f.get('default_value', '')
                elif classification == 'auto-generate':
                    entry['onMissingValue'] = 'generate'
                    entry['vaultPolicy'] = 'validatedPatternDefaultPolicy'
                else:
                    entry['onMissingValue'] = 'prompt'
                group_fields.append(entry)

            if group_fields:
                secrets.append({
                    'name': group_name,
                    'vaultPrefixes': [vault_prefix],
                    'fields': group_fields,
                })

        if not secrets:
            secrets.append({
                'name': f"{self.config['pattern_name']}-secrets",
                'vaultPrefixes': [vault_prefix],
                'fields': [{'name': 'secret', 'onMissingValue': 'generate',
                            'vaultPolicy': 'validatedPatternDefaultPolicy'}],
            })

        self._write_yaml(
            self.output_dir / 'values-secret.yaml.template',
            {'version': '2.0', 'secrets': secrets},
        )

    def _generate_flat_secret_template(self):
        """Generate values-secret.yaml.template with flat field list."""
        secret_config = self.config.get('secret_config', {})
        fields = []
        seen_names = {}  # name -> count
        for secret in self.analysis.detected_secrets:
            action = secret_config.get(secret.name, 'prompt')
            if action == 'skip':
                continue

            name = secret.name
            if name in seen_names:
                parts = [p for p in secret.path.replace('[', '.').replace(']', '').split('.')
                         if p and p != name]
                if parts:
                    name = f"{parts[-1]}_{name}"
                if name in seen_names:
                    seen_names[name] += 1
                    name = f"{name}_{seen_names[name]}"
            seen_names[name] = 1

            field_entry = {'name': name, 'onMissingValue': action}
            if action == 'generate':
                field_entry['vaultPolicy'] = 'validatedPatternDefaultPolicy'
            fields.append(field_entry)

        if not fields:
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
            "# Generated by quickpat\n"
            "# Add custom targets above or below the include line\n"
            "\n"
            "include Makefile-common\n"
        )
        (self.output_dir / 'Makefile').write_text(content)

    def _generate_makefile_common(self):
        content = (
            'MAKEFLAGS += --no-print-directory\n'
            'ANSIBLE_STDOUT_CALLBACK ?= rhvp.cluster_utils.readable\n'
            'ANSIBLE_RUN ?= ANSIBLE_STDOUT_CALLBACK='
            '$(ANSIBLE_STDOUT_CALLBACK) ansible-playbook '
            '$(EXTRA_PLAYBOOK_OPTS)\n'
            '\n'
            '.PHONY: help\n'
            'help: ## Print this help message\n'
            '\t@echo "Documentation: https://validatedpatterns.io/"\n'
            '\t@awk \'BEGIN {FS = ":.*##"; printf "\\nUsage:\\n'
            '  make \\033[36m<target>\\033[0m\\n"} '
            '/^(\\s|[a-zA-Z_0-9-])+:.*?##/ '
            '{ printf "  \\033[36m%-35s\\033[0m %s\\n", $$1, $$2 } '
            '/^##@/ { printf "\\n\\033[1m%s\\033[0m\\n", '
            'substr($$0, 5) } \' $(MAKEFILE_LIST)\n'
            '\n'
            '##@ Pattern Install Tasks\n'
            '.PHONY: show\n'
            'show: ## Shows the template that would be applied by the \'make install\' target\n'
            '\t@$(ANSIBLE_RUN) rhvp.cluster_utils.show\n'
            '\n'
            '.PHONY: operator-deploy\n'
            'operator-deploy operator-upgrade: '
            '## Installs/updates the pattern on a cluster (DOES NOT load secrets)\n'
            '\t@$(ANSIBLE_RUN) rhvp.cluster_utils.operator_deploy\n'
            '\n'
            '.PHONY: install\n'
            'install: pattern-install '
            '## Installs the pattern onto a cluster\n'
            '\n'
            '.PHONY: uninstall\n'
            'uninstall: ## Uninstall the pattern (EXPERIMENTAL)\n'
            '\t@$(ANSIBLE_RUN) rhvp.cluster_utils.uninstall\n'
            '\n'
            '.PHONY: pattern-install\n'
            'pattern-install:\n'
            '\t@$(ANSIBLE_RUN) rhvp.cluster_utils.install\n'
            '\n'
            '.PHONY: load-secrets\n'
            'load-secrets: ## Loads secrets onto the cluster (unless explicitly disabled in values-global.yaml)\n'
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
            'tier': self.config.get('tier', 'sandbox'),
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
            '\n'
            '[inventory]\n'
            'inventory_unparsed_warning=False\n'
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

    # ── Infrastructure charts (operator CRs) ─────────────────────────

    def _generate_infra_charts(self):
        operators = self.config.get('operators', [])
        for op_key in operators:
            if op_key not in INFRA_CHARTS:
                continue
            ic = INFRA_CHARTS[op_key]
            chart_dir = self.output_dir / 'charts' / ic['chart_name']
            tmpl_dir = chart_dir / 'templates'
            tmpl_dir.mkdir(parents=True, exist_ok=True)

            self._write_yaml(chart_dir / 'Chart.yaml', {
                'apiVersion': 'v2',
                'name': ic['chart_name'],
                'description': ic['description'],
                'version': '0.1.0',
                'type': 'application',
            })

            self._write_yaml(chart_dir / 'values.yaml', {
                'global': {'pattern': ''},
            })

            self._write_yaml(
                tmpl_dir / ic['template_name'],
                ic['cr'],
            )

    # ── LICENSE ─────────────────────────────────────────────────────

    def _generate_license(self):
        content = (
            'Apache License\n'
            'Version 2.0, January 2004\n'
            'http://www.apache.org/licenses/\n'
            '\n'
            'TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION\n'
            '\n'
            'See http://www.apache.org/licenses/LICENSE-2.0 for full terms.\n'
        )
        (self.output_dir / 'LICENSE').write_text(content)

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

    # ── remote strategy: secrets chart ─────────────────────────────

    @staticmethod
    def _transform_computed_template(template, source_fields):
        """Convert Helm template syntax to ExternalSecret template syntax."""
        t = template
        t = re.sub(r'\|\s*b64enc', '', t)
        t = re.sub(r'\|\s*quote', '', t)
        t = t.strip()

        printf_m = re.match(
            r'\{\{\s*printf\s+"([^"]+)"\s*(.*?)\s*\}\}', t, re.DOTALL,
        )
        if printf_m:
            fmt_str = printf_m.group(1)
            args_str = printf_m.group(2).strip()
            # Parse args in order: field refs and include calls
            args = re.findall(
                r'\(include\s+"[^"]*"\s+\.\)|\.[\w][\w.-]*', args_str,
            )
            result = fmt_str
            for arg in args:
                if arg.startswith('(include'):
                    # Drop the include and its %s
                    result = result.replace('%s', '', 1)
                else:
                    # Extract the field name from .Values.secret.X or .X
                    field = re.sub(r'^\.(?:Values\.(?:[\w]+\.)*)?', '', arg)
                    result = result.replace('%s', '{{ .%s }}' % field, 1)
            result = re.sub(r'%[sdvfgq]', '', result)
            return result

        # Non-printf: replace .Values refs inline
        t = re.sub(r'\.Values\.(?:[\w]+\.)*(\w[\w-]*)', r'.\1', t)
        t = re.sub(r'\(include\s+"[^"]*"\s+\.\)', '', t)
        return t

    def _generate_pattern_secrets_chart(self):
        """Generate charts/{app-name}-secrets/ with ExternalSecret CRDs."""
        app_name = self.config.get('app_name', self.analysis.name)
        secrets_chart_name = f"{app_name}-secrets"
        chart_dir = self.output_dir / 'charts' / secrets_chart_name
        chart_dir.mkdir(parents=True, exist_ok=True)
        tmpl_dir = chart_dir / 'templates'
        tmpl_dir.mkdir(exist_ok=True)

        pattern_name = self.config['pattern_name']
        self._write_yaml(chart_dir / 'Chart.yaml', {
            'apiVersion': 'v2',
            'name': secrets_chart_name,
            'version': '0.1.0',
            'description': f'ExternalSecret CRDs for {pattern_name}',
            'type': 'application',
        })

        # Empty values.yaml
        (chart_dir / 'values.yaml').write_text('')

        secret_groups = self.config.get('secret_groups', {})
        vault_prefix = self.config.get('vault_prefix', 'hub')
        secret_target_names = self.config.get('secret_target_names', {})

        for group_name, fields in secret_groups.items():
            target_name = secret_target_names.get(group_name, group_name)

            # Build per-key data entries and target template data
            data_entries = []
            template_data = {}
            for f in fields:
                fname = f['name']
                if f.get('computed'):
                    template_data[fname] = self._transform_computed_template(
                        f['template'], f.get('source_fields', []),
                    )
                else:
                    data_entries.append({
                        'secretKey': fname,
                        'remoteRef': {
                            'key': f'secret/data/{vault_prefix}/{group_name}',
                            'property': fname,
                        },
                    })
                    template_data[fname] = '{{ .' + fname + ' }}'

            ext_secret = {
                'apiVersion': 'external-secrets.io/v1',
                'kind': 'ExternalSecret',
                'metadata': {'name': target_name},
                'spec': {
                    'refreshInterval': '15s',
                    'secretStoreRef': {
                        'name': 'vault-backend',
                        'kind': 'ClusterSecretStore',
                    },
                    'target': {
                        'name': target_name,
                        'template': {
                            'type': 'Opaque',
                            'engineVersion': 'v2',
                            'data': template_data,
                        },
                    },
                    'data': data_entries,
                },
            }

            filename = f'{group_name}-secret.yaml'
            self._write_yaml(tmpl_dir / filename, ext_secret)

    def _generate_app_override_file(self):
        """Generate overrides/<app-name>.yaml disabling in-chart secret creation."""
        override_entries = self.config.get('override_entries', [])
        if not override_entries:
            return

        overrides_dir = self.output_dir / 'overrides'
        overrides_dir.mkdir(exist_ok=True)

        # Build nested dict from dotted paths
        override_data = {}
        for entry in override_entries:
            parts = entry['path'].split('.')
            d = override_data
            for part in parts[:-1]:
                d = d.setdefault(part, {})
            d[parts[-1]] = entry['value']

        app_name = self.config.get('app_name', self.analysis.name)
        self._write_yaml(overrides_dir / f'{app_name}.yaml', override_data)

    # ── charts ──────────────────────────────────────────────────────

    def _copy_chart_locally(self):
        default_strategy = self.config.get('chart_strategy', 'remote')
        for ci in self.analysis.charts:
            strategy = ci.strategy or default_strategy
            if strategy != 'local':
                continue
            dest = self.output_dir / 'charts' / 'all' / ci.name
            src = Path(ci.chart_path)

            if dest.exists():
                shutil.rmtree(dest)

            shutil.copytree(src, dest)

    # ── CRC validation scripts ─────────────────────────────────────

    def _generate_scripts(self):
        """Generate pattern-agnostic CRC validation scripts."""
        scripts_dir = self.output_dir / 'scripts'
        scripts_dir.mkdir(exist_ok=True)
        gn = self.config.get('cluster_group_name', 'prod')
        values_file = f"values-{gn}.yaml"

        def _sub(script):
            return script.replace('$VALUES_GROUP_FILE', values_file)

        self._write_script(scripts_dir / 'crc-setup.sh', _SCRIPT_CRC_SETUP)
        self._write_script(scripts_dir / 'deploy.sh', _SCRIPT_DEPLOY)
        self._write_script(
            scripts_dir / 'validate-deployment.sh', _sub(_SCRIPT_VALIDATE),
        )
        self._write_script(scripts_dir / 'undeploy.sh', _sub(_SCRIPT_UNDEPLOY))
        self._write_script(scripts_dir / 'status.sh', _sub(_SCRIPT_STATUS))

        self._write_yaml(scripts_dir / 'dsc.yaml', {
            'apiVersion': 'datasciencecluster.opendatahub.io/v1',
            'kind': 'DataScienceCluster',
            'metadata': {'name': 'default-dsc'},
            'spec': {'components': {
                'codeflare': {'managementState': 'Removed'},
                'dashboard': {'managementState': 'Managed'},
                'datasciencepipelines': {'managementState': 'Managed'},
                'kserve': {'managementState': 'Managed'},
                'modelmeshserving': {'managementState': 'Removed'},
                'ray': {'managementState': 'Removed'},
                'trustyai': {'managementState': 'Removed'},
                'workbenches': {'managementState': 'Managed'},
            }},
        })

        (scripts_dir / 'README.md').write_text(_SCRIPT_README)

    def _write_script(self, path, content):
        path.write_text(content)
        path.chmod(0o755)

    # ── README ──────────────────────────────────────────────────────

    def _generate_readme(self):
        a = self.analysis
        name = self.config.get('pattern_name', a.name)
        repo_url = (self.config.get('git_repo_url', '') or a.git_repo_url).removesuffix('.git')
        lines = [f'# {name}', '']

        # Boilerplate — what this repo is
        source_link = f'[{a.name}]({repo_url})' if repo_url else a.name
        lines.append(
            f'> This is a [Validated Pattern](https://validatedpatterns.io/) generated by '
            f'[QuickPat](https://github.com/atyronesmith/quickpat) from the '
            f'{source_link} AI Quickstart.'
        )
        lines.append('')

        # About the quickstart
        description = a.description if a.description != 'A Helm chart for Kubernetes' else ''
        if description or repo_url:
            lines.append('## About the Quickstart')
            lines.append('')
            if description:
                lines.append(description)
                lines.append('')
            if repo_url:
                lines.append(
                    f'For full details, see the '
                    f'[original quickstart README]({repo_url}#readme).'
                )
                lines.append('')

        # What the pattern provides
        lines.append('## What This Pattern Provides')
        lines.append('')
        lines.append('- GitOps deployment via ArgoCD')
        if a.detected_operators:
            op_names = [
                OPERATORS[op]['display_name']
                for op in a.detected_operators if op in OPERATORS
            ]
            if op_names:
                lines.append(f'- Operator lifecycle management ({", ".join(op_names)})')
        lines.append('- HashiCorp Vault secret management')
        lines.append('- Multi-cloud support (AWS, Azure, GCP, IBM Cloud)')
        lines.append('')

        # Deploy
        lines.append('## Deploy')
        lines.append('')
        lines.append('### On CRC or OpenShift')
        lines.append('')
        lines.append('```bash')
        lines.append('./scripts/deploy.sh')
        lines.append('```')
        lines.append('')
        lines.append('### Via pattern.sh (standard VP flow)')
        lines.append('')
        lines.append('```bash')
        lines.append('git init && git add -A && git commit -m "init"')
        lines.append('oc login <cluster>')
        lines.append('./pattern.sh make install')
        lines.append('```')
        lines.append('')

        # Directory structure
        lines.append('## Directory Structure')
        lines.append('')
        lines.append('```')
        lines.append('values-global.yaml          # Global config, multisource settings')
        gn = self.config.get('cluster_group_name', 'prod')
        pad = ' ' * max(1, 28 - len(f'values-{gn}.yaml'))
        lines.append(f'values-{gn}.yaml{pad}# Cluster group: namespaces, operators, apps')
        lines.append('values-secret.yaml.template # Vault secrets template')
        lines.append('Makefile                    # Build targets')
        lines.append('pattern.sh                  # VP utility container runner')
        lines.append('charts/                     # Helm chart(s)')
        lines.append('overrides/                  # Platform-specific value overrides')
        lines.append('scripts/                    # CRC deploy, undeploy, validate, status')
        lines.append('docs/                       # Quickstart analysis report')
        lines.append('```')
        lines.append('')

        # Footer
        lines.append('## Documentation')
        lines.append('')
        lines.append(
            'See [`docs/quickstart-analysis.md`](docs/quickstart-analysis.md) '
            'for detailed analysis of the original quickstart.'
        )
        lines.append('')

        (self.output_dir / 'README.md').write_text('\n'.join(lines))

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
            f"- **Chart strategy:** {config.get('chart_strategy', 'remote')}"
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


# ── Script templates (pattern-agnostic CRC validation) ──────────

_SCRIPT_CRC_SETUP = r'''#!/bin/bash
set -eo pipefail

CRC_MEMORY=${CRC_MEMORY:-49152}
CRC_CPUS=${CRC_CPUS:-12}
CRC_DISK=${CRC_DISK:-100}

echo "=== CRC Setup for VP Structural Validation ==="
echo "  Memory: ${CRC_MEMORY}MB  CPUs: ${CRC_CPUS}  Disk: ${CRC_DISK}GB"
echo ""

if ! command -v crc &>/dev/null; then
    echo "ERROR: crc not found."
    echo "Download from https://console.redhat.com/openshift/create/local"
    echo "(Homebrew does NOT have a crc formula)"
    exit 1
fi

echo "CRC version: $(crc version | head -1)"

STATUS=$(crc status 2>/dev/null | grep "CRC VM:" | awk '{print $3}' || echo "Stopped")

if [ "$STATUS" = "Running" ]; then
    echo "CRC is already running."
    echo ""
    echo "Current config:"
    crc config view 2>/dev/null | grep -E "memory|cpus|disk" || true
    echo ""
    echo "To reconfigure, run: crc stop && crc delete && re-run this script"
else
    echo "Configuring CRC..."
    crc config set memory "$CRC_MEMORY"
    crc config set cpus "$CRC_CPUS"
    crc config set disk-size "$CRC_DISK"
    crc config set consent-telemetry no

    echo ""
    echo "Starting CRC (this takes 5-10 minutes on first run)..."
    crc start

    echo ""
    echo "CRC started successfully."
fi

echo ""
echo "=== Cluster Login ==="
eval "$(crc oc-env)"

KUBEADMIN_PASS=$(cat ~/.crc/machines/crc/kubeadmin-password 2>/dev/null || true)
if [ -z "$KUBEADMIN_PASS" ]; then
    KUBEADMIN_PASS=$(crc console --credentials 2>&1 | grep kubeadmin | grep -oE "'[^']+'" | tail -1 | tr -d "'")
fi

if [ -n "$KUBEADMIN_PASS" ]; then
    oc login -u kubeadmin -p "$KUBEADMIN_PASS" https://api.crc.testing:6443 --insecure-skip-tls-verify=true
else
    echo "Could not extract kubeadmin password automatically."
    echo "Run: crc console --credentials"
    echo "Then: oc login -u kubeadmin -p <password> https://api.crc.testing:6443"
    exit 1
fi

echo ""
echo "=== Pull Secret Check ==="
HAS_RH_REGISTRY=$(oc get secret pull-secret -n openshift-config -o jsonpath='{.data.\.dockerconfigjson}' 2>/dev/null \
    | base64 -d 2>/dev/null \
    | python3 -c "import sys,json; print('registry.redhat.io' in json.load(sys.stdin).get('auths',{}))" 2>/dev/null \
    || echo "False")

if [ "$HAS_RH_REGISTRY" = "True" ]; then
    echo "  Pull secret includes registry.redhat.io — OK"
else
    echo "  WARNING: Pull secret missing registry.redhat.io credentials."
    echo "  Catalog sources will fail to pull operator indexes."
    echo "  Download pull secret from https://console.redhat.com/openshift/create/local"
    echo "  Apply: oc set data secret/pull-secret -n openshift-config --from-file=.dockerconfigjson=<path>"
fi

echo ""
echo "=== Cluster Health ==="
echo "Nodes:"
oc get nodes
echo ""
echo "Cluster version:"
oc get clusterversion
echo ""
echo "Resources:"
oc adm top nodes 2>/dev/null || echo "(metrics not yet available — wait a few minutes)"
echo ""
echo "=== Ready for pattern deployment ==="
echo "Next: ./scripts/deploy.sh"
'''

_SCRIPT_DEPLOY = r'''#!/bin/bash
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PATTERN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PATTERN_NAME=$(grep '^  pattern:' "$PATTERN_DIR/values-global.yaml" 2>/dev/null | awk '{print $2}' || basename "$PATTERN_DIR")
VALUES_SECRET_FILE="${VALUES_SECRET:-$HOME/values-secret-${PATTERN_NAME}.yaml}"

echo "=== Validated Pattern CRC Deployment ==="
echo "  Pattern:  $PATTERN_NAME"
echo "  Dir:      $PATTERN_DIR"
echo "  Secrets:  $VALUES_SECRET_FILE"
echo ""

# --- Pre-flight checks ---
echo "--- Pre-flight checks ---"

for cmd in oc helm git python3; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "ERROR: $cmd not found."
        exit 1
    fi
done

CURRENT_USER=$(oc whoami 2>/dev/null || echo "")
if [ -z "$CURRENT_USER" ] || [ "$CURRENT_USER" != "kubeadmin" ]; then
    echo "  Need kubeadmin access (current: ${CURRENT_USER:-not logged in}). Logging in..."
    eval "$(crc oc-env 2>/dev/null || true)"
    KUBEADMIN_PASS=$(cat ~/.crc/machines/crc/kubeadmin-password 2>/dev/null || true)
    if [ -z "$KUBEADMIN_PASS" ]; then
        KUBEADMIN_PASS=$(crc console --credentials 2>&1 | grep kubeadmin | grep -oE "'[^']+'" | tail -1 | tr -d "'")
    fi
    if [ -n "$KUBEADMIN_PASS" ]; then
        oc login -u kubeadmin -p "$KUBEADMIN_PASS" https://api.crc.testing:6443 --insecure-skip-tls-verify=true
    else
        echo "ERROR: Could not extract kubeadmin password."
        echo "Run: oc login -u kubeadmin https://api.crc.testing:6443"
        exit 1
    fi
fi

echo "  Logged in as: $(oc whoami)"
echo "  Cluster: $(oc whoami --show-server)"

HAS_RH_REGISTRY=$(oc get secret pull-secret -n openshift-config -o jsonpath='{.data.\.dockerconfigjson}' 2>/dev/null \
    | base64 -d 2>/dev/null \
    | python3 -c "import sys,json; print('registry.redhat.io' in json.load(sys.stdin).get('auths',{}))" 2>/dev/null \
    || echo "False")

if [ "$HAS_RH_REGISTRY" != "True" ]; then
    echo "  WARNING: Pull secret missing registry.redhat.io — operators may fail to install."
    echo "  Fix: oc set data secret/pull-secret -n openshift-config --from-file=.dockerconfigjson=<pull-secret.json>"
fi
echo ""

# --- Git repo setup ---
echo "--- Git repo setup ---"

cd "$PATTERN_DIR"

if [ -d .git ]; then
    echo "  Already a git repo."
    if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
        echo "  Committing uncommitted changes..."
        git add -A
        git commit -m "Pre-deploy snapshot" --allow-empty 2>/dev/null || true
    fi
else
    echo "  Initializing git repo (required by VP framework)..."
    git init
    git add -A
    git commit -m "Initial pattern"
fi

TARGET_BRANCH=$(git rev-parse --abbrev-ref HEAD)
TARGET_REPO=$(git remote get-url origin 2>/dev/null || echo "")

if [ -z "$TARGET_REPO" ] || [[ "$TARGET_REPO" == file://* ]] || [[ "$TARGET_REPO" == /* ]]; then
    echo ""
    echo "  WARNING: Pattern repo must be HTTP-accessible from the cluster."
    echo "  Local/file:// repos won't work — the patterns operator runs inside a pod."
    if command -v gh &>/dev/null; then
        echo "  Creating GitHub repo..."
        gh repo create "${PATTERN_NAME}-test" --private --source=. --push 2>/dev/null || true
        gh repo edit --visibility public 2>/dev/null || true
        TARGET_REPO=$(git remote get-url origin 2>/dev/null || echo "")
        TARGET_BRANCH=$(git rev-parse --abbrev-ref HEAD)
    else
        echo "  Install gh CLI and run: gh repo create ${PATTERN_NAME}-test --public --source=. --push"
        exit 1
    fi
fi

echo "  Branch: $TARGET_BRANCH"
echo "  Repo: $TARGET_REPO"
echo ""

# --- Secrets file ---
echo "--- Secrets file ---"

if [ -f "$VALUES_SECRET_FILE" ]; then
    echo "  Using existing: $VALUES_SECRET_FILE"
elif [ -f "$PATTERN_DIR/values-secret.yaml.template" ]; then
    echo "  Generating dummy secrets from template..."
    python3 - "$PATTERN_DIR/values-secret.yaml.template" "$VALUES_SECRET_FILE" << 'PYEOF'
import re, sys
src = open(sys.argv[1]).read()
out_path = sys.argv[2]
src = re.sub(r'\n\s+onMissingValue:.*', '', src)
src = re.sub(r'\n\s+vaultPolicy:.*', '', src)
src = re.sub(
    r"(- name:\s+(\S+)\n\s+)value:\s*(?:''|\"\"|)\s*$",
    lambda m: m.group(1) + "value: dummy-" + m.group(2),
    src, flags=re.MULTILINE)
src = re.sub(
    r"^(\s+)(- name:\s+(\S+))\s*$(?!\n\s+value:)",
    lambda m: m.group(1) + "- name: " + m.group(3) + "\n" + m.group(1) + "  value: dummy-" + m.group(3),
    src, flags=re.MULTILINE)
open(out_path, 'w').write(src)
PYEOF
    echo "  Created: $VALUES_SECRET_FILE"
else
    echo "  No secrets template found. Skipping."
fi
echo ""

# --- Deploy pattern-install chart ---
echo "--- Deploying pattern (direct helm, bypassing utility container) ---"
echo ""

cd "$PATTERN_DIR"

HELM_OPTS=(
    --include-crds
    --name-template "$PATTERN_NAME"
    -f values-global.yaml
    --set main.git.repoURL="$TARGET_REPO"
    --set main.git.revision="$TARGET_BRANCH"
    --set global.pattern="$PATTERN_NAME"
    --set global.clusterDomain="$(oc get ingress.config cluster -o jsonpath='{.spec.domain}' 2>/dev/null || echo 'apps-crc.testing')"
    --set global.clusterVersion="$(oc get clusterversion version -o jsonpath='{.status.desired.version}' 2>/dev/null || echo '4.21')"
    --set global.clusterPlatform="None"
)

WORK=$(mktemp -d)
trap "rm -rf $WORK" EXIT

echo "Step 1: Rendering pattern-install chart..."
helm template "${HELM_OPTS[@]}" oci://quay.io/validatedpatterns/pattern-install > "$WORK/all.yaml" 2>/dev/null

echo "Step 2: Splitting CRDs and Pattern CR..."
python3 -c "
docs = open('$WORK/all.yaml').read().split('---')
crds, pattern = [], []
for doc in docs:
    stripped = doc.strip()
    if not stripped or 'kind:' not in stripped:
        continue
    if 'kind: Pattern' in stripped:
        pattern.append(stripped)
    else:
        crds.append(stripped)
open('$WORK/crds.yaml','w').write('\n---\n'.join(crds))
open('$WORK/pattern.yaml','w').write('\n---\n'.join(pattern))
"

echo "Step 3: Applying CRDs and operator subscription..."
oc apply -f "$WORK/crds.yaml" 2>&1

echo "Step 4: Waiting for Pattern CRD..."
for i in $(seq 1 30); do
    if oc get crd patterns.gitops.hybrid-cloud-patterns.io &>/dev/null; then
        echo "  Pattern CRD ready."
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "  ERROR: Pattern CRD not registered after 150s"
        exit 1
    fi
    sleep 5
done

echo "Step 5: Applying Pattern CR..."
oc apply -f "$WORK/pattern.yaml" 2>&1

echo ""
echo "Step 6: Waiting for patterns operator to install..."
for i in $(seq 1 60); do
    if oc get csv -n openshift-operators 2>/dev/null | grep -q "patterns-operator.*Succeeded"; then
        echo "  Patterns operator ready."
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "  WARNING: Patterns operator not ready after 5 min — continuing anyway."
    fi
    sleep 5
done

echo ""
echo "Step 7: Waiting for catalog sources (operator indexes)..."
for i in $(seq 1 36); do
    RUNNING=$(oc get pods -n openshift-marketplace --no-headers 2>/dev/null | grep -c "Running" || echo 0)
    if [ "$RUNNING" -ge 4 ]; then
        echo "  Catalog sources ready ($RUNNING pods running)."
        break
    fi
    if [ "$i" -eq 36 ]; then
        echo "  WARNING: Only $RUNNING catalog pods running. Check pull secret."
    fi
    sleep 10
done

echo ""
echo "Step 8: Waiting for ArgoCD applications..."
for i in $(seq 1 60); do
    APP_COUNT=$(oc get applications.argoproj.io -A --no-headers 2>/dev/null | wc -l | tr -d ' ')
    if [ "$APP_COUNT" -gt 0 ]; then
        echo "  $APP_COUNT ArgoCD applications found."
        oc get applications.argoproj.io -A --no-headers 2>/dev/null
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "  WARNING: No ArgoCD applications after 5 min."
    fi
    sleep 5
done

echo ""
echo "Step 9: Checking if RHODS needs DataScienceCluster CR..."
if oc get crd datascienceclusters.datasciencecluster.opendatahub.io &>/dev/null; then
    DSC_COUNT=$(oc get datascienceclusters -A --no-headers 2>/dev/null | wc -l | tr -d ' ')
    if [ "$DSC_COUNT" -eq 0 ]; then
        echo "  Creating DataScienceCluster CR (needed for Notebook/DSPA CRDs)..."
        oc apply -f "$SCRIPT_DIR/dsc.yaml" 2>/dev/null || echo "  WARNING: dsc.yaml not found"
        echo "  DataScienceCluster CR created."
    else
        echo "  DataScienceCluster already exists."
    fi
else
    echo "  RHODS not yet installed — DSC will be created on next run."
fi

echo ""
echo "=== Deploy complete ==="
echo ""
echo "Operators and ArgoCD will take 5-10 minutes to stabilize."
echo "Next steps:"
echo "  1. Watch:     oc get applications.argoproj.io -A -w"
echo "  2. Validate:  ./scripts/validate-deployment.sh"
echo "  3. Undeploy:  ./scripts/undeploy.sh"
'''

_SCRIPT_VALIDATE = r'''#!/bin/bash
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PATTERN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PATTERN_NAME=$(grep '^  pattern:' "$PATTERN_DIR/values-global.yaml" 2>/dev/null | awk '{print $2}' || basename "$PATTERN_DIR")

PASS=0
FAIL=0
WARN=0

pass() { echo "  PASS: $1"; ((PASS++)); }
fail() { echo "  FAIL: $1"; ((FAIL++)); }
warn() { echo "  WARN: $1"; ((WARN++)); }

echo "============================================================"
echo "  Validated Pattern Deployment Validation: $PATTERN_NAME"
echo "============================================================"
echo ""

# --- Phase 1: Pre-flight ---
echo "--- Phase 1: Pre-flight ---"

if oc whoami &>/dev/null; then
    USER=$(oc whoami)
    pass "Logged in as $USER"
else
    fail "Not logged into cluster"
    echo "Run: oc login -u kubeadmin https://api.crc.testing:6443"
    exit 1
fi

if oc get nodes &>/dev/null; then
    NODE_COUNT=$(oc get nodes --no-headers | wc -l | tr -d ' ')
    pass "Cluster accessible ($NODE_COUNT nodes)"
else
    fail "Cannot reach cluster"
    exit 1
fi

READY=$(oc get nodes --no-headers | grep -c " Ready" || echo 0)
if [ "$READY" -eq "$NODE_COUNT" ]; then
    pass "All nodes Ready"
else
    fail "$READY/$NODE_COUNT nodes Ready"
fi

echo ""

# --- Phase 2: Pattern CR ---
echo "--- Phase 2: Pattern CR ---"

PATTERN_STATUS=$(oc get pattern "$PATTERN_NAME" -n openshift-operators -o jsonpath='{.status.lastStep}' 2>/dev/null || echo "")
if [ -n "$PATTERN_STATUS" ]; then
    if [ "$PATTERN_STATUS" = "reconcile complete" ]; then
        pass "Pattern CR: $PATTERN_STATUS"
    else
        warn "Pattern CR: $PATTERN_STATUS"
    fi
    LAST_ERROR=$(oc get pattern "$PATTERN_NAME" -n openshift-operators -o jsonpath='{.status.lastError}' 2>/dev/null || echo "")
    if [ -n "$LAST_ERROR" ]; then
        warn "Pattern last error: $LAST_ERROR"
    fi
else
    fail "Pattern CR not found"
fi

echo ""

# --- Phase 3: Namespaces ---
echo "--- Phase 3: Namespaces ---"

APP_NS=$(grep -A2 'namespaces:' "$PATTERN_DIR/$VALUES_GROUP_FILE" 2>/dev/null | grep '^\s*-' | sed 's/^\s*-\s*//' | sed 's/:.*//' | tr -d ' ' || echo "")
EXPECTED_NS="vault external-secrets $APP_NS"
for ns in $EXPECTED_NS; do
    if [ -z "$ns" ]; then continue; fi
    if oc get namespace "$ns" &>/dev/null; then
        pass "Namespace $ns exists"
    else
        fail "Namespace $ns missing"
    fi
done

echo ""

# --- Phase 4: Operator Subscriptions ---
echo "--- Phase 4: Operator Subscriptions ---"

SUBS=$(oc get subscriptions -A --no-headers 2>/dev/null | grep -v "^openshift-operator" | sort -u -k2,2 || echo "")
if [ -n "$SUBS" ]; then
    while IFS= read -r line; do
        NS=$(echo "$line" | awk '{print $1}')
        NAME=$(echo "$line" | awk '{print $2}')
        pass "Subscription $NAME in $NS"
    done <<< "$SUBS"
else
    warn "No operator subscriptions found"
fi

echo ""
echo "CSV install status:"
oc get csv -A --no-headers 2>/dev/null | sort -u -k2,2 | while IFS= read -r line; do
    NS=$(echo "$line" | awk '{print $1}')
    NAME=$(echo "$line" | awk '{print $2}')
    PHASE=$(echo "$line" | awk '{print $NF}')
    if [ "$PHASE" = "Succeeded" ]; then
        pass "CSV $NAME ($PHASE)"
    elif [ "$PHASE" = "Failed" ]; then
        fail "CSV $NAME ($PHASE)"
    else
        warn "CSV $NAME ($PHASE)"
    fi
done

echo ""

# --- Phase 5: ArgoCD ---
echo "--- Phase 5: ArgoCD Applications ---"

APPS=$(oc get applications.argoproj.io -A --no-headers 2>/dev/null || echo "")
if [ -n "$APPS" ]; then
    while IFS= read -r line; do
        NS=$(echo "$line" | awk '{print $1}')
        APP_NAME=$(echo "$line" | awk '{print $2}')
        SYNC=$(echo "$line" | awk '{print $3}')
        HEALTH=$(echo "$line" | awk '{print $4}')
        if [ "$SYNC" = "Synced" ]; then
            pass "App $APP_NAME: $SYNC / $HEALTH"
        else
            warn "App $APP_NAME: $SYNC / $HEALTH"
        fi
    done <<< "$APPS"
else
    warn "No ArgoCD applications found yet"
fi

echo ""

# --- Phase 6: Infrastructure ---
echo "--- Phase 6: Infrastructure (Vault + ExternalSecrets) ---"

if oc get pods -n vault --no-headers 2>/dev/null | grep -q "Running"; then
    pass "Vault pods running"
else
    warn "Vault pods not running yet"
fi

if oc get pods -n external-secrets --no-headers 2>/dev/null | grep -q "Running"; then
    pass "External Secrets Operator pods running"
else
    warn "External Secrets Operator pods not running yet"
fi

if oc get crd externalsecrets.external-secrets.io &>/dev/null; then
    pass "ExternalSecret CRD registered"
else
    warn "ExternalSecret CRD not yet registered"
fi

if oc get crd clustersecretstores.external-secrets.io &>/dev/null; then
    pass "ClusterSecretStore CRD registered"
else
    warn "ClusterSecretStore CRD not yet registered"
fi

for ns in $APP_NS; do
    if [ -z "$ns" ]; then continue; fi
    ES_COUNT=$(oc get externalsecrets -n "$ns" --no-headers 2>/dev/null | wc -l | tr -d ' ')
    if [ "$ES_COUNT" -gt 0 ]; then
        pass "$ES_COUNT ExternalSecrets in $ns namespace"
    else
        warn "No ExternalSecrets in $ns namespace yet"
    fi
done

echo ""

# --- Phase 7: Application Resources ---
echo "--- Phase 7: Application Resources ---"

for ns in $APP_NS; do
    if [ -z "$ns" ]; then continue; fi
    if ! oc get namespace "$ns" &>/dev/null; then continue; fi

    echo "  Namespace: $ns"
    for kind in configmap service route deployment statefulset; do
        COUNT=$(oc get "$kind" -n "$ns" --no-headers 2>/dev/null | wc -l | tr -d ' ')
        if [ "$COUNT" -gt 0 ]; then
            pass "$COUNT ${kind}s in $ns"
        fi
    done

    PODS=$(oc get pods -n "$ns" --no-headers 2>/dev/null || echo "")
    if [ -n "$PODS" ]; then
        TOTAL=$(echo "$PODS" | wc -l | tr -d ' ')
        RUNNING=$(echo "$PODS" | grep -c "Running" || echo 0)
        PENDING=$(echo "$PODS" | grep -c "Pending" || echo 0)
        CRASH=$(echo "$PODS" | grep -c "CrashLoop\|Error\|ImagePull" || echo 0)
        echo "    Pods: $TOTAL total, $RUNNING running, $PENDING pending, $CRASH errored"
        if [ "$CRASH" -gt 0 ]; then
            warn "Some pods in error state in $ns (expected without GPU)"
        fi
        if [ "$RUNNING" -gt 0 ]; then
            pass "Pods running in $ns"
        fi
    else
        warn "No pods in $ns yet"
    fi
    echo ""
done

# --- Summary ---
echo "============================================================"
echo "  Summary: $PASS passed, $FAIL failed, $WARN warnings"
echo "============================================================"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
'''

_SCRIPT_UNDEPLOY = r'''#!/bin/bash
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PATTERN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PATTERN_NAME=$(grep '^  pattern:' "$PATTERN_DIR/values-global.yaml" 2>/dev/null | awk '{print $2}' || basename "$PATTERN_DIR")

echo "=== Removing Validated Pattern: $PATTERN_NAME ==="
echo ""

if ! oc whoami &>/dev/null; then
    echo "Not logged into cluster. Nothing to remove."
    exit 0
fi

echo "Deleting Pattern CR..."
if oc get pattern "$PATTERN_NAME" -n openshift-operators &>/dev/null; then
    oc delete pattern "$PATTERN_NAME" -n openshift-operators --wait=false 2>/dev/null || true
    echo "  Deleted pattern: $PATTERN_NAME"
fi

echo ""
echo "Deleting ArgoCD applications..."
for ns in $(oc get applications.argoproj.io -A --no-headers 2>/dev/null | awk '{print $1}' | sort -u); do
    for app in $(oc get applications.argoproj.io -n "$ns" --no-headers 2>/dev/null | awk '{print $1}'); do
        oc delete application "$app" -n "$ns" --wait=false 2>/dev/null || true
        echo "  Deleted application: $app (in $ns)"
    done
done

echo ""
echo "Waiting for application cleanup (30s)..."
sleep 30

echo "Deleting DataScienceCluster..."
oc delete datasciencecluster --all -A 2>/dev/null || true

echo ""
echo "Deleting application namespaces..."
APP_NS=$(grep -A2 'namespaces:' "$PATTERN_DIR/$VALUES_GROUP_FILE" 2>/dev/null | grep '^\s*-' | sed 's/^\s*-\s*//' | sed 's/:.*//' | tr -d ' ' || echo "")
for ns in vault external-secrets $APP_NS; do
    if [ -z "$ns" ]; then continue; fi
    if oc get namespace "$ns" &>/dev/null; then
        oc delete namespace "$ns" --wait=false 2>/dev/null || true
        echo "  Deleted namespace: $ns"
    fi
done

echo ""
echo "Deleting operator subscriptions..."
for sub in $(oc get subscriptions -A --no-headers 2>/dev/null | awk '{print $1 "/" $2}'); do
    ns="${sub%%/*}"
    name="${sub##*/}"
    CSV=$(oc get subscription "$name" -n "$ns" -o jsonpath='{.status.installedCSV}' 2>/dev/null || echo "")
    oc delete subscription "$name" -n "$ns" 2>/dev/null || true
    if [ -n "$CSV" ]; then
        oc delete csv "$CSV" -n "$ns" 2>/dev/null || true
    fi
    echo "  Deleted subscription: $name"
done

echo ""
echo "Deleting operator namespaces..."
for ns in openshift-nfd nvidia-gpu-operator redhat-ods-operator redhat-ods-applications redhat-ods-monitoring openshift-serverless; do
    if oc get namespace "$ns" &>/dev/null; then
        oc delete namespace "$ns" --wait=false 2>/dev/null || true
        echo "  Deleted namespace: $ns"
    fi
done

for ns in $(oc get namespaces --no-headers 2>/dev/null | awk '{print $1}' | grep -E "^${PATTERN_NAME}|^vp-|^imperative$"); do
    oc delete namespace "$ns" --wait=false 2>/dev/null || true
    echo "  Deleted namespace: $ns"
done

echo ""
echo "=== Undeploy complete ==="
echo "Note: Some resources may take a few minutes to fully terminate."
echo "To destroy the entire CRC VM instead: crc stop && crc delete"
'''

_SCRIPT_STATUS = r'''#!/bin/bash
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PATTERN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PATTERN_NAME=$(grep '^  pattern:' "$PATTERN_DIR/values-global.yaml" 2>/dev/null | awk '{print $2}' || basename "$PATTERN_DIR")

echo "=== Pattern Status: $PATTERN_NAME ==="
echo ""

if ! oc whoami &>/dev/null; then
    echo "Not logged into cluster."
    exit 1
fi

echo "--- Pattern CR ---"
oc get pattern "$PATTERN_NAME" -n openshift-operators 2>/dev/null || echo "Not found"

echo ""
echo "--- ArgoCD Applications ---"
oc get applications.argoproj.io -A 2>/dev/null || echo "None"

echo ""
echo "--- Operator CSVs (unique) ---"
oc get csv -A --no-headers 2>/dev/null | sort -u -k2,2 | awk '{printf "  %-55s %s\n", $2, $NF}'

echo ""
echo "--- Catalog Sources ---"
oc get pods -n openshift-marketplace --no-headers 2>/dev/null | awk '{printf "  %-50s %s\n", $1, $3}'

echo ""
echo "--- Application Pods ---"
APP_NS=$(grep -A2 'namespaces:' "$PATTERN_DIR/$VALUES_GROUP_FILE" 2>/dev/null | grep '^\s*-' | sed 's/^\s*-\s*//' | sed 's/:.*//' | tr -d ' ' || echo "")
for ns in vault external-secrets $APP_NS; do
    if [ -z "$ns" ]; then continue; fi
    if ! oc get namespace "$ns" &>/dev/null 2>&1; then continue; fi
    PODS=$(oc get pods -n "$ns" --no-headers 2>/dev/null || echo "")
    if [ -n "$PODS" ]; then
        TOTAL=$(echo "$PODS" | wc -l | tr -d ' ')
        RUNNING=$(echo "$PODS" | grep -c "Running" || echo 0)
        echo "  $ns: $RUNNING/$TOTAL running"
    fi
done

echo ""
echo "--- Node Resources ---"
oc adm top nodes 2>/dev/null || echo "  Metrics not available"
'''

_SCRIPT_README = '''# CRC Structural Validation for Validated Patterns

Validates that a pattern deploys correctly on CRC (CodeReady Containers) \
-- operators install, ArgoCD syncs, ExternalSecrets resolve. No GPU \
required; this is structural validation only.

These scripts are pattern-agnostic -- they derive the pattern name from \
`values-global.yaml`.

## Prerequisites

- CRC installed (download from https://console.redhat.com/openshift/create/local)
- Red Hat pull secret (from the same page)
- helm installed (`brew install helm`)
- git and python3 installed
- ~48GB free RAM (12 CPUs minimum for full operator stack)

**Note:** `brew install crc` does NOT work. CRC must be downloaded directly.

## First-time setup

```bash
crc setup
./scripts/crc-setup.sh
```

## Deploy

```bash
# Copy pattern to local disk first (SMB/NFS shares don\'t support git)
cp -R /path/to/pattern ~/my-pattern
cd ~/my-pattern
./scripts/deploy.sh
```

The deploy script handles: kubeadmin login, pull secret check, git init, \
GitHub push, dummy secrets generation, helm install (bypassing utility \
container), operator wait, DSC creation.

## Validate

```bash
./scripts/validate-deployment.sh
```

## Status

```bash
./scripts/status.sh
```

## Undeploy

```bash
./scripts/undeploy.sh
```

## Expected results

- Operators install (CSVs Succeeded), GPU operator has no work to do
- ArgoCD syncs all applications
- Vault + ExternalSecrets infrastructure deploys
- ExternalSecrets show errors (no real Vault secrets loaded)
- Application pods Pending/CrashLoop (no GPU, no model) -- expected
- **The structural win: all Kubernetes resources accepted by the API server**

## Known issues

- CRC needs 12+ CPUs -- 8 is insufficient for full VP operator stack
- RHODS requires a DataScienceCluster CR to register Notebook/DSPA CRDs
- utility container (`pattern.sh make install`) may crash on Apple Silicon \
under podman libkrun
- Pattern repo must be HTTP-accessible -- patterns operator runs inside a \
cluster pod
'''
