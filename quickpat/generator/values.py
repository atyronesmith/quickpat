"""Pattern generator: values YAML generation."""

from ..config import get as cfg
from ..operators import OPERATORS, INFRA_CHARTS

class ValuesGeneratorMixin:
    """Mixin for PatternGenerator."""

    def _has_ssh_key_secret(self) -> bool:
        """True when a top-level secret looks like an SSH keypair.

        Charts that inject authorized_keys via Helm values (e.g. openshell-saw)
        expect ``global.sshPublicKey`` in values-global.yaml so scripts like
        generate-keys.sh can sed-patch in the real public key. Detect by
        private_key + public_key fields (the SSH keypair convention).
        """
        for secret in self.config.get('top_level_secrets', []) or []:
            field_names = {f.name for f in secret.fields}
            if 'public_key' in field_names and 'private_key' in field_names:
                return True
        return False

    def _generate_values_global(self):
        global_vals = {
            'pattern': self.config['pattern_name'],
            'singleArgoCD': True,
            'secretLoader': {'disabled': False},
        }
        # Placeholder for generate-keys.sh (and similar) to sed-replace.
        # Empty string matches `sshPublicKey:.*`; without this field the
        # sed patch silently no-ops and sandbox VMs get no SSH key.
        if self._has_ssh_key_secret():
            global_vals['sshPublicKey'] = ''
        data = {
            'global': global_vals,
            'main': {
                'clusterGroupName': self.config.get(
                    'cluster_group_name', 'prod'
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

    def _build_shared_value_files(self) -> list:
        files = ['/overrides/values-{{ $.Values.global.clusterPlatform }}.yaml']
        if self.config.get('devices'):
            files.append('/overrides/values-{{ $.Values.global.device }}.yaml')
        return files

    def _generate_values_hub(self):
        operators = self.config.get('operators', [])
        app_namespace = self.config.get('app_namespace', self.analysis.name)
        app_name = self.config.get('app_name', self.analysis.name)
        use_vault = self.config.get('use_vault', False)

        # Filter GPU-specific operators out of main values when device overrides are active
        device_ops = self._device_operators()
        base_operators = [op for op in operators if op not in device_ops]

        namespaces = self._build_namespaces(base_operators, app_namespace, use_vault)
        subscriptions = self._build_subscriptions(base_operators)
        applications = self._build_applications(
            app_name, app_namespace, use_vault
        )

        group_name = self.config.get('cluster_group_name', 'prod')
        cg = {
            'name': group_name,
            'sharedValueFiles': self._build_shared_value_files(),
            'namespaces': namespaces,
            'subscriptions': subscriptions,
            'applications': applications,
        }
        data = {'clusterGroup': cg}
        self._write_yaml(
            self.output_dir / f'values-{group_name}.yaml', data
        )

    def _get_app_charts(self):
        """Return list of (app_name, namespace, ChartInfo) for all charts."""
        ns_overrides = self.config.get('namespace_overrides', {})
        if len(self.analysis.charts) > 1:
            result = []
            for ci in self.analysis.charts:
                # Skip remote charts when there's no upstream URL — same logic
                # as the single-chart path to prevent orphan namespace entries.
                if (ci.strategy or self.config.get('chart_strategy', 'remote')) == 'remote':
                    if not self.config.get('git_repo_url', ''):
                        continue
                ns = ns_overrides.get(ci.name, ci.group or ci.name)
                result.append((ci.name, ns, ci))
            return result
        ci = self.analysis.charts[0]
        # Skip the main chart if it's a remote strategy with no upstream URL —
        # all components are custom charts; there's no upstream app to reference.
        if (ci.strategy or self.config.get('chart_strategy', 'remote')) == 'remote':
            if not self.config.get('git_repo_url', ''):
                return []
        app_name = self.config.get('app_name', self.analysis.name)
        app_ns = ns_overrides.get(ci.name, self.config.get('app_namespace', self.analysis.name))
        return [(app_name, app_ns, ci)]

    # Operators that belong in the device override (values-gpu.yaml) rather
    # than values-prod.yaml when the spec declares multiple devices.
    _GPU_DEVICE_OPERATORS = {'nvidia-gpu', 'nfd'}

    def _device_operators(self) -> set:
        """Return operator keys that should move to device overrides."""
        if self.config.get('devices'):
            return self._GPU_DEVICE_OPERATORS & set(self.config.get('operators', []))
        return set()

    def _build_namespaces(self, operators, app_namespace, use_vault):
        namespaces = {}
        seen = set()

        # Infrastructure namespaces
        if use_vault:
            namespaces['vault'] = {}
            seen.add('vault')
            namespaces['external-secrets-operator'] = {
                'operatorGroup': True,
                'targetNamespaces': [],
            }
            seen.add('external-secrets-operator')
            namespaces['external-secrets'] = {}
            seen.add('external-secrets')

        # Operator namespaces
        for op_key in operators:
            op = OPERATORS.get(op_key)
            if op is None:
                continue
            ns = op['namespace']
            if ns in seen or ns == 'openshift-operators':
                continue
            seen.add(ns)

            ns_config = op.get('namespace_config')
            namespaces[ns] = ns_config if ns_config else {}

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
                # No operatorGroup/targetNamespaces here: any namespace an
                # operator subscription actually targets was already claimed
                # by the "Operator namespaces" loop above (and would have hit
                # the `ns in seen` check), so an app namespace reaching this
                # branch never has a subscription pointed at it. Setting one
                # anyway leaves a stray OperatorGroup that blocks installing a
                # real operator into this namespace later (OLM allows only
                # one per namespace).
                namespaces[ns] = {
                    'labels': {
                        'opendatahub.io/dashboard': 'true',
                        'modelmesh-enabled': 'false',
                    },
                }
            else:
                namespaces[ns] = {}

        return namespaces

    def _build_subscriptions(self, operators):
        subscriptions = {}
        version_op_overrides = self.config.get('version_overrides', {}).get('operators', {})

        for op_key in operators:
            op = OPERATORS.get(op_key)
            if op is None:
                continue  # unknown operator key — skip rather than crash
            sub_key = op.get('subscription_key', op_key)
            sub = {
                'name': op['subscription_name'],
                'namespace': op['namespace'],
            }

            # Apply version-specific overrides for this operator if a target is set.
            # Version overrides take precedence over OPERATORS defaults.
            ver_override = version_op_overrides.get(op_key, {})
            channel = ver_override.get('channel') or op.get('channel')
            if channel:
                sub['channel'] = channel
            if ver_override.get('installPlanApproval'):
                sub['installPlanApproval'] = ver_override['installPlanApproval']

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

        # Vault and external secrets (standard infrastructure)
        if use_vault:
            applications['vault'] = {
                'name': 'vault',
                'namespace': 'vault',
                'chart': 'hashicorp-vault',
                'chartVersion': cfg(
                    "infrastructure.vault_chart_version", "0.1.*"
                ),
            }
            applications['openshift-external-secrets'] = {
                'name': 'openshift-external-secrets',
                'namespace': 'external-secrets',
                'chart': 'openshift-external-secrets',
                'chartVersion': cfg(
                    "infrastructure.external_secrets_chart_version", "0.2.*"
                ),
            }

        # Infrastructure config charts (operator CRs)
        # Skip device-specific operators — they appear in values-gpu.yaml etc.
        # Also skip when a custom component with the same chart_name exists —
        # the hand-written chart takes precedence and both would collide.
        operators = self.config.get('operators', [])
        device_ops = self._device_operators()
        custom_chart_names = {
            name for name in self.config.get('custom_components', {})
        }
        for op_key in operators:
            if op_key in device_ops:
                continue
            if op_key in INFRA_CHARTS:
                ic = INFRA_CHARTS[op_key]
                chart_name = ic['chart_name']
                if chart_name in custom_chart_names:
                    continue  # hand-written chart handles this; no auto-generated app
                applications[chart_name] = {
                    'name': chart_name,
                    'namespace': ic['namespace'],
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
                    'path': f'charts/{name}',
                }
            elif strategy == 'remote':
                git_url = self.config.get('git_repo_url', '')
                if not git_url:
                    # No upstream repo — all components are custom charts; skip remote app.
                    continue
                has_remote = True
                chart_path = self.config.get('chart_path_in_repo', '') or '.'
                app_entry = {
                    'name': name,
                    'namespace': ns,
                    'repoURL': git_url,
                    'path': chart_path,
                    'chartVersion': self.config.get('chart_branch', 'main'),
                }
                # extra_value_files from old config path (backward compat)
                extra_files = self.config.get('extra_value_files')
                if extra_files:
                    app_entry['extraValueFiles'] = extra_files
                # upstream.extraValues → generate overrides/<name>.yaml + reference it
                if self.config.get('upstream_extra_values'):
                    override_path = f'/overrides/{name}.yaml'
                    app_entry.setdefault('extraValueFiles', [])
                    if override_path not in app_entry['extraValueFiles']:
                        app_entry['extraValueFiles'].append(override_path)
                # ignoreDifferences from old config path (backward compat)
                ignore_diffs = self.config.get('ignore_differences')
                if ignore_diffs:
                    app_entry['ignoreDifferences'] = ignore_diffs
                # upstream.ignoreDifferences from spec
                upstream_ignores = self.config.get('upstream_ignore_differences', [])
                if upstream_ignores:
                    app_entry['ignoreDifferences'] = upstream_ignores
                applications[name] = app_entry
            else:
                repo_url = ci.repo_url or self.config.get('chart_repo_url', '')
                applications[name] = {
                    'name': name,
                    'namespace': ns,
                    'repoURL': repo_url,
                    'chart': name,
                    'targetRevision': self.config.get(
                        'chart_version', ci.version or self.analysis.version
                    ),
                }

        # Secrets chart for remote strategy with vault (only if secrets exist)
        secret_groups = self.config.get('secret_groups', {})
        if has_remote and use_vault and secret_groups:
            app_namespace = self.config.get('app_namespace', self.analysis.name)
            secrets_chart_name = f"{app_name}-secrets"
            applications[secrets_chart_name] = {
                'name': secrets_chart_name,
                'namespace': app_namespace,
                'path': f'charts/{secrets_chart_name}',
            }

        # Custom component stubs — one ArgoCD app per component, pointing at
        # charts/<comp-name>/ which _generate_custom_component_stubs creates.
        custom_components = self.config.get('custom_components', {})
        default_namespace = self.config.get('app_namespace', self.analysis.name)
        for comp_name, comp in custom_components.items():
            # Skip components marked deploy: manual — chart lives in the repo
            # for reference / manual apply, but is not ArgoCD-managed.
            if hasattr(comp, 'deploy') and comp.deploy == 'manual':
                continue
            ns = (comp.namespace or default_namespace) if hasattr(comp, 'namespace') else default_namespace
            app_entry = {
                'name': comp_name,
                'namespace': ns,
                'path': f'charts/{comp_name}',
            }
            # extraValueFiles from the spec, plus auto-add /overrides/<name>.yaml
            # when custom.<name>.extraValues is set (parity with upstream.extraValues).
            extra_files = list(getattr(comp, 'extra_value_files', None) or [])
            if getattr(comp, 'extra_values', None):
                override_path = f'/overrides/{comp_name}.yaml'
                if override_path not in extra_files:
                    extra_files.append(override_path)
            if extra_files:
                app_entry['extraValueFiles'] = extra_files
            applications[comp_name] = app_entry

        return applications

    # ── values-secret.yaml.template ─────────────────────────────────

    def _generate_values_secret_template(self):
        if not self.config.get('use_vault'):
            return

        # VP v2 format when top-level secrets are declared in spec
        top_level_secrets = self.config.get('top_level_secrets', [])
        if top_level_secrets:
            self._generate_vp_v2_secret_template(top_level_secrets)
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

    def _generate_vp_v2_secret_template(self, top_level_secrets):
        """Write values-secret.yaml.template in the VP v2 backingStore format.

        This is the format expected by the VP secret loader:
            https://validatedpatterns.io/learn/secrets-management-in-the-validated-patterns-framework/

        Each TopLevelSecret in the spec maps to one entry under secrets:.
        The VP loader defaults a field's onMissingValue to 'error', which
        fails validation on an empty value — so every field must carry an
        explicit onMissingValue matching the secret's on_missing:
          - 'generate': the loader auto-generates the value; no value key.
          - 'skip' / 'prompt' (default): the VP loader has no 'skip' concept,
            so both map to 'prompt' with an empty default — the closest
            equivalent to the QS path's "press Enter to skip" behavior.
        """
        secrets_entries = []
        for secret in top_level_secrets:
            fields = []
            for f in secret.fields:
                if f.path is not None:
                    fields.append({'name': f.name, 'path': f.path})
                elif f.value is not None:
                    fields.append({'name': f.name, 'value': f.value})
                elif secret.on_missing == 'generate':
                    fields.append({
                        'name': f.name,
                        'onMissingValue': 'generate',
                        'vaultPolicy': 'validatedPatternDefaultPolicy',
                    })
                else:
                    fields.append({'name': f.name, 'value': '', 'onMissingValue': 'prompt'})
            if not fields:
                # Declared secret with no fields — add a single 'value' field
                # as a placeholder so the template is always well-formed.
                fields = [{'name': 'value', 'value': '', 'onMissingValue': 'prompt'}]
            secrets_entries.append({'name': secret.name, 'fields': fields})

        doc = {
            'version': '2.0',
            'backingStore': 'vault',
            'secrets': secrets_entries,
        }
        self._write_yaml(self.output_dir / 'values-secret.yaml.template', doc)

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

