"""Parse ApplicationSpec YAML into typed dataclasses."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml


class AppSpecError(Exception):
    """Raised when an ApplicationSpec is invalid."""


@dataclass
class UpstreamRef:
    repo: str
    path: str = 'chart'
    branch: str = 'main'
    extra_values: dict = field(default_factory=dict)      # upstream.extraValues → overrides/<name>.yaml
    ignore_differences: list = field(default_factory=list) # upstream.ignoreDifferences → ArgoCD app


@dataclass
class SecretDecl:
    vault_path: str
    key: str = ''
    generate: bool = False


@dataclass
class SecretField:
    name: str


@dataclass
class TargetSpec:
    """Platform version target declared in spec.yaml or passed via --target.

    Controls operator channels, installPlanApproval, DSC component defaults,
    and required co-dependencies for a specific platform release.

    Example spec.yaml:
        target:
          platform: rhoai
          version: "3.5"

    CLI override:
        quickpat compose spec.yaml --target rhoai=3.5
    """
    platform: str   # e.g. 'rhoai'
    version: str    # e.g. '3.5'


@dataclass
class DocEntry:
    """A documentation file to be processed and written into the generated output.

    source: path relative to spec.yaml location (e.g. 'docs/README.md')
    target: output path inside vp-out/ or qs-out/ (e.g. 'README.md')
    deploy: 'both' (default) | 'vp' | 'qs'
            Controls which generated output receives this file.
            Sections within the file can be further filtered with
            <!-- vp-only --> / <!-- qs-only --> / <!-- end --> markers.
    """
    source: str
    target: str
    deploy: str = 'both'


@dataclass
class TopLevelSecret:
    """A named secret group in the spec's top-level secrets: list.

    Maps to one entry in values-secret.yaml.template.  vault_path is the
    Vault key prefix (e.g. 'secure-agent-workspace/anthropic'); name is the
    display name / last path segment used in the template.
    """
    name: str
    vault_path: str = ''
    on_missing: str = 'prompt'  # prompt | skip | generate
    fields: list = field(default_factory=list)  # list of SecretField


@dataclass
class BlockInstance:
    name: str
    block_type: str
    profile: str = ''
    config: dict = field(default_factory=dict)
    secrets: dict = field(default_factory=dict)  # name → SecretDecl
    inputs: dict = field(default_factory=dict)   # role → block_name (e.g. vector_store: "db")


@dataclass
class CustomComponent:
    name: str
    image: str
    namespace: str = ''
    source_chart: str = ''        # source.chart path (local), if declared
    extra_value_files: list = field(default_factory=list)
    # 'argocd' (default) — creates an ArgoCD app in values-prod.yaml
    # 'manual'           — chart is in the repo but NOT managed by ArgoCD
    #                      (use for one-time build steps, pre-flight jobs, etc.)
    deploy: str = 'argocd'
    replicas: int = 1
    ports: list = field(default_factory=list)
    env: dict = field(default_factory=dict)
    resources: dict = field(default_factory=dict)
    probes: dict = field(default_factory=dict)
    monitor: dict = field(default_factory=dict)


@dataclass
class WiringEntry:
    from_block: str
    to_block: str
    via: str


@dataclass
class ApplicationSpec:
    name: str
    description: str
    tier: str
    upstream: UpstreamRef
    blocks: dict   # name → BlockInstance (ordered)
    custom: dict   # name → CustomComponent
    wiring: list   # list of WiringEntry
    devices: list = field(default_factory=list)   # [cpu, gpu, hpu] — deployment device modes
    vault_enabled: bool = False                   # vault: {enabled: true} in spec
    top_level_secrets: list = field(default_factory=list)  # list of TopLevelSecret
    docs: list = field(default_factory=list)      # list of DocEntry
    target: object = None                         # TargetSpec | None


VALID_TIERS = {'sandbox', 'tested', 'maintained'}


def load_application_spec(path: str) -> ApplicationSpec:
    """Load and parse an ApplicationSpec YAML file."""
    p = Path(path)
    if not p.exists():
        raise AppSpecError(f"Spec file not found: {path}")

    with open(p) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise AppSpecError("Spec must be a YAML mapping")

    api = raw.get('apiVersion', '')
    kind = raw.get('kind', '')
    if api != 'supplychain/v1alpha1' or kind != 'ApplicationSpec':
        raise AppSpecError(
            f"Expected apiVersion: supplychain/v1alpha1 / kind: ApplicationSpec, "
            f"got {api!r} / {kind!r}"
        )

    meta = raw.get('metadata', {})
    if not meta.get('name'):
        raise AppSpecError("metadata.name is required")

    tier = meta.get('tier', 'sandbox')
    if tier not in VALID_TIERS:
        raise AppSpecError(f"Invalid tier {tier!r}, must be one of: {sorted(VALID_TIERS)}")

    upstream_raw = meta.get('upstream', {}) or {}
    upstream = UpstreamRef(
        repo=upstream_raw.get('repo', ''),
        path=upstream_raw.get('path', 'chart'),
        branch=upstream_raw.get('branch', 'main'),
        extra_values=upstream_raw.get('extraValues', {}) or {},
        ignore_differences=upstream_raw.get('ignoreDifferences', []) or [],
    )

    devices = meta.get('devices', []) or []
    if not isinstance(devices, list):
        raise AppSpecError("metadata.devices must be a list (e.g. [cpu, gpu])")

    blocks = _parse_blocks(raw.get('blocks', {}))
    custom = _parse_custom(raw.get('custom', {}))
    wiring = _parse_wiring(raw.get('wiring', []))

    vault_raw = raw.get('vault', {}) or {}
    vault_enabled = bool(vault_raw.get('enabled', False))

    top_level_secrets = _parse_top_level_secrets(raw.get('secrets', []) or [])
    docs = _parse_docs(raw.get('docs', []) or [])
    target = _parse_target(raw.get('target'))

    return ApplicationSpec(
        name=meta['name'],
        description=meta.get('description', ''),
        tier=tier,
        upstream=upstream,
        blocks=blocks,
        custom=custom,
        wiring=wiring,
        devices=devices,
        vault_enabled=vault_enabled,
        top_level_secrets=top_level_secrets,
        docs=docs,
        target=target,
    )


def _parse_blocks(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise AppSpecError("'blocks' must be a mapping")

    blocks = {}
    for block_name, block_raw in raw.items():
        if not isinstance(block_raw, dict):
            raise AppSpecError(f"blocks.{block_name} must be a mapping")
        if 'type' not in block_raw:
            raise AppSpecError(f"blocks.{block_name}: missing 'type'")

        secrets = {}
        for sec_name, sec_raw in (block_raw.get('secrets') or {}).items():
            if not isinstance(sec_raw, dict):
                raise AppSpecError(
                    f"blocks.{block_name}.secrets.{sec_name} must be a mapping"
                )
            secrets[sec_name] = SecretDecl(
                vault_path=sec_raw.get('vault_path', f"{block_name}/{sec_name}"),
                key=sec_raw.get('key', sec_name),
                generate=bool(sec_raw.get('generate', False)),
            )

        inputs = block_raw.get('inputs', {}) or {}
        if not isinstance(inputs, dict):
            raise AppSpecError(f"blocks.{block_name}.inputs must be a mapping")

        blocks[block_name] = BlockInstance(
            name=block_name,
            block_type=block_raw['type'],
            profile=block_raw.get('profile', ''),
            config=block_raw.get('config', {}),
            secrets=secrets,
            inputs=inputs,
        )

    return blocks


def _parse_custom(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise AppSpecError("'custom' must be a mapping")

    custom = {}
    for comp_name, comp_raw in raw.items():
        if not isinstance(comp_raw, dict):
            raise AppSpecError(f"custom.{comp_name} must be a mapping")

        source = comp_raw.get('source', {})
        image = source.get('image', '') if isinstance(source, dict) else ''
        source_chart = source.get('chart', '') if isinstance(source, dict) else ''

        deploy = comp_raw.get('deploy', 'argocd')
        if deploy not in ('argocd', 'manual'):
            raise AppSpecError(
                f"custom.{comp_name}.deploy must be 'argocd' or 'manual', got {deploy!r}"
            )

        custom[comp_name] = CustomComponent(
            name=comp_name,
            image=image,
            namespace=comp_raw.get('namespace') or '',
            source_chart=source_chart,
            extra_value_files=comp_raw.get('extraValueFiles') or [],
            deploy=deploy,
            replicas=comp_raw.get('replicas') or 1,
            ports=comp_raw.get('ports') or [],
            env=comp_raw.get('env') or {},
            resources=comp_raw.get('resources') or {},
            probes=comp_raw.get('probes') or {},
            monitor=comp_raw.get('monitor') or {},
        )

    return custom


def _parse_target(raw) -> object:
    """Parse target: {platform: rhoai, version: "3.5"} or return None."""
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise AppSpecError("'target' must be a mapping with 'platform' and 'version'")
    platform = raw.get('platform', '')
    version = str(raw.get('version', ''))
    if not platform:
        raise AppSpecError("target.platform is required (e.g. 'rhoai')")
    if not version:
        raise AppSpecError("target.version is required (e.g. '3.5')")
    # Validate the platform/version exists in the registry
    from .version_registry import resolve_version
    try:
        resolve_version(platform, version)
    except ValueError as e:
        raise AppSpecError(str(e))
    return TargetSpec(platform=platform, version=version)


_VALID_DOC_DEPLOY = {'both', 'vp', 'qs'}
_VALID_ON_MISSING = {'prompt', 'skip', 'generate'}


def _parse_docs(raw: list) -> list:
    """Parse the docs: list into DocEntry objects."""
    if not isinstance(raw, list):
        raise AppSpecError("'docs' must be a list")

    docs = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise AppSpecError(f"docs[{i}] must be a mapping")
        if not entry.get('source'):
            raise AppSpecError(f"docs[{i}]: 'source' is required and must be non-empty")
        if not entry.get('target'):
            raise AppSpecError(f"docs[{i}]: 'target' is required and must be non-empty")
        deploy = entry.get('deploy', 'both')
        if deploy not in _VALID_DOC_DEPLOY:
            raise AppSpecError(
                f"docs[{i}].deploy must be one of {sorted(_VALID_DOC_DEPLOY)}, got {deploy!r}"
            )
        docs.append(DocEntry(
            source=entry['source'],
            target=entry['target'],
            deploy=deploy,
        ))
    return docs


def _parse_top_level_secrets(raw: list) -> list:
    """Parse the top-level secrets: list into TopLevelSecret objects.

    Each entry in the list describes one named secret group in Vault.
    The generated values-secret.yaml.template will have one entry per group.

    Example spec entry:
        - name: anthropic
          vault_path: mypattern/anthropic
          onMissingValue: skip
          fields:
            - name: api_key

    Produces template entry:
        - name: anthropic
          fields:
          - name: api_key
            value: null
    """
    if not isinstance(raw, list):
        raise AppSpecError("'secrets' must be a list")

    secrets = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise AppSpecError(f"secrets[{i}] must be a mapping")
        if 'name' not in entry:
            raise AppSpecError(f"secrets[{i}]: missing 'name'")

        raw_fields = entry.get('fields', []) or []
        if not isinstance(raw_fields, list):
            raise AppSpecError(f"secrets[{i}].fields must be a list")

        parsed_fields = []
        for j, f in enumerate(raw_fields):
            if isinstance(f, str):
                parsed_fields.append(SecretField(name=f))
            elif isinstance(f, dict):
                if 'name' not in f:
                    raise AppSpecError(f"secrets[{i}].fields[{j}]: missing 'name'")
                parsed_fields.append(SecretField(name=f['name']))
            else:
                raise AppSpecError(f"secrets[{i}].fields[{j}] must be a string or mapping")

        name = entry['name']
        vault_path = entry.get('vault_path', name)
        on_missing = entry.get('onMissingValue', 'prompt')
        if on_missing not in _VALID_ON_MISSING:
            raise AppSpecError(
                f"secrets[{i}].onMissingValue must be one of "
                f"{sorted(_VALID_ON_MISSING)}, got {on_missing!r}"
            )
        secrets.append(TopLevelSecret(
            name=name,
            vault_path=vault_path,
            on_missing=on_missing,
            fields=parsed_fields,
        ))

    return secrets


def _parse_wiring(raw: list) -> list:
    if not isinstance(raw, list):
        raise AppSpecError("'wiring' must be a list")

    wiring = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise AppSpecError(f"wiring[{i}] must be a mapping")
        if 'from' not in entry or 'to' not in entry:
            raise AppSpecError(f"wiring[{i}]: must have 'from' and 'to'")
        wiring.append(WiringEntry(
            from_block=entry['from'],
            to_block=entry['to'],
            via=entry.get('via', ''),
        ))

    return wiring
