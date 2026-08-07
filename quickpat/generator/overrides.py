"""Pattern generator: override value files."""

import shutil
from pathlib import Path

import yaml

from ..config import get as cfg
from ..operators import OPERATORS, INFRA_CHARTS

class OverridesGeneratorMixin:
    """Mixin for PatternGenerator."""

    # ── overrides/ ──────────────────────────────────────────────────

    # Platform → default storage provider for object-storage blocks.
    # These are sensible starting points — operators override as needed.
    _PLATFORM_STORAGE = {
        'AWS':      ('s3',    'https://s3.amazonaws.com', 'us-east-1'),
        'Azure':    ('s3',    'https://<account>.blob.core.windows.net', 'eastus'),
        'GCP':      ('s3',    'https://storage.googleapis.com', 'us-central1'),
        'IBMCloud': ('odf',   '', ''),
        'None':     ('minio', '', ''),
    }

    def _generate_overrides(self):
        overrides_dir = self.output_dir / 'overrides'
        overrides_dir.mkdir(exist_ok=True)

        # Simpler: check existing_custom_charts keys don't matter — use _block_configs
        # to find all block types. We need block type info from config.
        # The compiler puts block configs in _block_configs but not types.
        # Use custom_components absence: object-storage blocks show up in
        # _block_configs. We detect them by checking the spec blocks via config.
        # For now, collect from 'dsc_config' absence + 'gpu_config' absence
        # and check the keys. Best approach: compiler should pass block types.
        # Workaround: generator writes storage overrides for ALL platforms
        # when storage-related config keys exist in the config dict.
        has_object_storage = bool(self.config.get('secret_groups'))

        platforms = cfg("platforms", ["AWS", "Azure", "GCP", "IBMCloud", "None"])
        if platforms is None:
            platforms = ["AWS", "Azure", "GCP", "IBMCloud", "None"]
        for platform in platforms:
            path = overrides_dir / f'values-{platform}.yaml'
            provider, endpoint, region = self._PLATFORM_STORAGE.get(
                platform, ('minio', '', '')
            )
            lines = [f'# Platform-specific overrides for {platform}']

            if has_object_storage:
                lines += [
                    '',
                    '# Object storage backend for this platform.',
                    '# Change provider to match your environment.',
                    '# Providers: minio (in-cluster), odf (OpenShift Data Foundation), s3 (external S3)',
                ]
                if provider == 'minio':
                    lines += [
                        '# objectStorage:',
                        '#   provider: minio  # default for bare metal',
                    ]
                elif provider == 'odf':
                    lines += [
                        '# objectStorage:',
                        '#   provider: odf',
                        '#   odfStorageClass: openshift-storage.noobaa.io',
                    ]
                else:  # s3 / azure
                    lines += [
                        '# objectStorage:',
                        '#   provider: s3',
                        f'#   endpoint: {endpoint}',
                        f'#   region: {region}',
                        '#   bucket: <your-pre-existing-bucket-name>',
                    ]

            path.write_text('\n'.join(lines) + '\n')

        # ── Device override files ────────────────────────────────────────────
        # When the spec declares devices: [cpu, gpu], generate per-device overrides.
        # GPU operators (NFD + NVIDIA) live here rather than in values-prod.yaml.
        devices = self.config.get('devices', [])
        operators = self.config.get('operators', [])
        device_ops = self._device_operators()

        if 'gpu' in devices and device_ops:
            gpu_ns, gpu_subs, gpu_apps = {}, {}, {}
            for op_key in operators:
                if op_key not in device_ops:
                    continue
                op = OPERATORS[op_key]
                ns = op['namespace']
                if ns != 'openshift-operators':
                    ns_cfg = op.get('namespace_config')
                    gpu_ns[ns] = ns_cfg if ns_cfg else {}
                sub_key = op.get('subscription_key', op_key)
                sub = {'name': op['subscription_name'], 'namespace': op['namespace']}
                if op.get('source') and op['source'] != 'redhat-operators':
                    sub['source'] = op['source']
                gpu_subs[sub_key] = sub
                if op_key in INFRA_CHARTS:
                    ic = INFRA_CHARTS[op_key]
                    gpu_apps[ic['chart_name']] = {
                        'name': ic['chart_name'],
                        'namespace': ic['namespace'],
                        'path': f'charts/{ic["chart_name"]}',
                    }
            gpu_data = {'clusterGroup': {
                'namespaces': gpu_ns,
                'subscriptions': gpu_subs,
                'applications': gpu_apps,
            }}
            gpu_path = overrides_dir / 'values-gpu.yaml'
            with open(gpu_path, 'w') as f:
                f.write('# Generated by quickpat compose -- do not edit\n')
                import yaml as _yaml
                _yaml.dump(gpu_data, f, default_flow_style=False, sort_keys=False)

        if 'cpu' in devices:
            cpu_path = overrides_dir / 'values-cpu.yaml'
            cpu_path.write_text(
                '# Generated by quickpat compose -- do not edit\n'
                '# CPU deployment — no GPU operators.\n'
                '# Set model resources appropriate for CPU inference.\n'
            )

        # ── Upstream application-specific override file ──────────────────────
        # upstream.extraValues in the spec → overrides/<app-name>.yaml
        upstream_extra = self.config.get('upstream_extra_values', {})
        if upstream_extra:
            app_name = self.config.get('app_name', self.analysis.name)
            extra_path = overrides_dir / f'{app_name}.yaml'
            with open(extra_path, 'w') as f:
                f.write('# Generated by quickpat compose -- do not edit\n')
                import yaml as _yaml
                _yaml.dump(upstream_extra, f, default_flow_style=False, sort_keys=False)

        self._generate_custom_component_overrides(overrides_dir)

    def _generate_custom_component_overrides(self, overrides_dir: Path):
        """Emit overrides/<file>.yaml for custom: components.

        Precedence per path under overrides/:
          1. custom.<name>.extraValues → write overrides/<name>.yaml
          2. else copy <spec_dir>/overrides/<file> for each extraValueFiles entry
          3. else write a documented stub so the ArgoCD reference always resolves

        Source overrides/ next to spec.yaml is the hand-authored layer (same
        model as charts/) — never hand-edit vp-out/overrides/.
        """
        custom_components = self.config.get('custom_components', {}) or {}
        if not custom_components:
            return

        spec_dir = self.config.get('spec_dir')
        written: set = set()

        for comp_name, comp in custom_components.items():
            extra_values = getattr(comp, 'extra_values', None) or {}
            if extra_values:
                rel = f'{comp_name}.yaml'
                path = overrides_dir / rel
                with open(path, 'w') as f:
                    f.write('# Generated by quickpat compose -- do not edit\n')
                    yaml.dump(
                        extra_values, f, default_flow_style=False, sort_keys=False,
                    )
                written.add(rel)

            for evf in getattr(comp, 'extra_value_files', None) or []:
                if not isinstance(evf, str):
                    continue
                # Normalize "/overrides/foo.yaml" → "foo.yaml"
                rel = evf.lstrip('/')
                if rel.startswith('overrides/'):
                    rel = rel[len('overrides/'):]
                # Only handle simple overrides/<file.yaml> paths
                if not rel or '/' in rel or rel in written:
                    continue

                dest = overrides_dir / rel
                src = Path(spec_dir) / 'overrides' / rel if spec_dir else None
                if src is not None and src.is_file():
                    shutil.copy2(src, dest)
                    written.add(rel)
                    continue

                dest.write_text(
                    '# Stub generated by quickpat compose — do not edit\n'
                    f'# Referenced by custom.{comp_name}.extraValueFiles but no\n'
                    f'# source found at overrides/{rel} (next to spec.yaml).\n'
                    f'# Add that file, or set custom.{comp_name}.extraValues.\n'
                )
                written.add(rel)
                self.warnings.append(
                    f'[warning] overrides/{rel}: referenced by custom.{comp_name} '
                    f'but no source file and no extraValues — wrote stub'
                )
