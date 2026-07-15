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


@dataclass
class SecretDecl:
    vault_path: str
    key: str = ''
    generate: bool = False


@dataclass
class BlockInstance:
    name: str
    block_type: str
    profile: str = ''
    config: dict = field(default_factory=dict)
    secrets: dict = field(default_factory=dict)  # name → SecretDecl


@dataclass
class CustomComponent:
    name: str
    image: str
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

    upstream_raw = meta.get('upstream', {})
    upstream = UpstreamRef(
        repo=upstream_raw.get('repo', ''),
        path=upstream_raw.get('path', 'chart'),
        branch=upstream_raw.get('branch', 'main'),
    )

    blocks = _parse_blocks(raw.get('blocks', {}))
    custom = _parse_custom(raw.get('custom', {}))
    wiring = _parse_wiring(raw.get('wiring', []))

    return ApplicationSpec(
        name=meta['name'],
        description=meta.get('description', ''),
        tier=tier,
        upstream=upstream,
        blocks=blocks,
        custom=custom,
        wiring=wiring,
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

        blocks[block_name] = BlockInstance(
            name=block_name,
            block_type=block_raw['type'],
            profile=block_raw.get('profile', ''),
            config=block_raw.get('config', {}),
            secrets=secrets,
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

        custom[comp_name] = CustomComponent(
            name=comp_name,
            image=image,
            replicas=comp_raw.get('replicas', 1),
            ports=comp_raw.get('ports', []),
            env=comp_raw.get('env', {}),
            resources=comp_raw.get('resources', {}),
            probes=comp_raw.get('probes', {}),
            monitor=comp_raw.get('monitor', {}),
        )

    return custom


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
