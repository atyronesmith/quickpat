"""Platform version registry for quickpat target versioning.

Maps (platform, version) → operator channel overrides, DSC component defaults,
required co-dependencies, and breaking-change notes for upgrade paths.

Usage:
    from quickpat.compose.version_registry import resolve_version, PLATFORM_VERSIONS
    overrides = resolve_version('rhoai', '3.5')
    # → {'operators': {'openshift-ai': {'channel': 'stable-3.5', ...}}, ...}
"""


# ── Platform version data ─────────────────────────────────────────────────────
#
# Each version entry has:
#   operators:        operator-key → {channel, installPlanApproval, ...}
#                     Keys in OPERATORS dict get channel/installPlanApproval
#                     overridden. Keys not in OPERATORS are new co-deps added
#                     by this version (e.g. cert-manager for RHOAI 3.x).
#   dsc_defaults:     DSC component name → managementState string. Merged over
#                     the block-level dsc: config at generation time.
#   co_dependencies:  extra operator keys required by this version. These are
#                     added to the subscription list alongside the base operators.
#   requires_oc:      minimum OpenShift version string (informational).

PLATFORM_VERSIONS = {
    'rhoai': {
        # RHOAI 2.25 — final 2.x release, maintenance mode.
        # No cert-manager / jobset requirements. KServe Serverless mode deprecated.
        '2.25': {
            'operators': {
                'openshift-ai': {
                    'channel': 'stable-2.25',
                    'installPlanApproval': 'Manual',
                },
            },
            'dsc_defaults': {
                'kserve': 'Managed',
                'dashboard': 'Managed',
                'modelmeshserving': 'Managed',
                'datasciencepipelines': 'Removed',
                'workbenches': 'Removed',
                'ray': 'Removed',
                'kueue': 'Removed',
                'trainingoperator': 'Removed',
                'trustyai': 'Unmanaged',
            },
            'co_dependencies': [],
            'requires_oc': '4.14',
        },

        # RHOAI 3.0 — first 3.x release. Hard break from 2.x.
        # cert-manager and jobset become required prerequisites.
        # llamastackoperator added to DSC.
        '3.0': {
            'operators': {
                'openshift-ai': {
                    'channel': 'stable-3.0',
                    'installPlanApproval': 'Manual',
                },
                'cert-manager': {
                    'channel': 'stable',
                },
                'jobset': {
                    'channel': 'stable',
                },
            },
            'dsc_defaults': {
                'kserve': 'Managed',
                'dashboard': 'Managed',
                'llamastackoperator': 'Managed',
                'modelmeshserving': 'Managed',
                'datasciencepipelines': 'Removed',
                'workbenches': 'Removed',
                'ray': 'Removed',
                'kueue': 'Removed',
                'trainingoperator': 'Removed',
            },
            'co_dependencies': ['cert-manager', 'jobset'],
            'requires_oc': '4.15',
        },

        # RHOAI 3.4 — mlflowoperator DSC component added.
        # Old mlflow dashboard feature flag removed.
        '3.4': {
            'operators': {
                'openshift-ai': {
                    'channel': 'stable-3.4',
                    'installPlanApproval': 'Manual',
                },
                'cert-manager': {
                    'channel': 'stable',
                },
                'jobset': {
                    'channel': 'stable',
                },
            },
            'dsc_defaults': {
                'kserve': 'Managed',
                'dashboard': 'Managed',
                'llamastackoperator': 'Managed',
                'mlflowoperator': 'Managed',
                'modelmeshserving': 'Managed',
                'datasciencepipelines': 'Removed',
                'workbenches': 'Removed',
                'ray': 'Removed',
                'kueue': 'Removed',
                'trainingoperator': 'Removed',
            },
            'co_dependencies': ['cert-manager', 'jobset'],
            'requires_oc': '4.16',
        },

        # RHOAI 3.5 — current GA release.
        '3.5': {
            'operators': {
                'openshift-ai': {
                    'channel': 'stable-3.5',
                    'installPlanApproval': 'Manual',
                },
                'cert-manager': {
                    'channel': 'stable',
                },
                'jobset': {
                    'channel': 'stable',
                },
            },
            'dsc_defaults': {
                'kserve': 'Managed',
                'dashboard': 'Managed',
                'llamastackoperator': 'Managed',
                'mlflowoperator': 'Managed',
                'modelmeshserving': 'Managed',
                'datasciencepipelines': 'Removed',
                'workbenches': 'Removed',
                'ray': 'Removed',
                'kueue': 'Removed',
                'trainingoperator': 'Removed',
            },
            'co_dependencies': ['cert-manager', 'jobset'],
            'requires_oc': '4.16',
        },
    },
}


# ── Breaking changes for upgrade path generation (Item 2b) ───────────────────
#
# Each entry is a list of dicts with:
#   description:  plain-English explanation of the breaking change
#   severity:     'blocking' (must resolve before upgrade) | 'warning' (proceed with care)
#   action:       what the operator must do before upgrading
#   docs:         optional URL to Red Hat documentation

UPGRADE_BREAKING_CHANGES = {
    ('rhoai', '2.25', '3.0'): [
        {
            'description': (
                'ModelMesh and KServe Serverless mode must be migrated to '
                'RawDeployment before upgrading. No automated conversion path exists.'
            ),
            'severity': 'blocking',
            'action': (
                'Migrate all InferenceServices from Serverless to RawDeployment. '
                'Set kserve.serving.ingressGateway.httpProxy.enabled=false. '
                'Verify no ServingRuntime uses knative annotations.'
            ),
            'docs': 'https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.0/html/upgrading_openshift_ai_self-managed/',
        },
        {
            'description': 'cert-manager and jobset operators are new hard prerequisites in 3.x.',
            'severity': 'blocking',
            'action': (
                'Install openshift-cert-manager-operator (stable channel) and '
                'openshift-jobset-operator (stable channel) before upgrading.'
            ),
        },
    ],
    # 3.0→3.4: covers any upgrade from the 3.0 baseline to 3.4.
    # PLATFORM_VERSIONS has no 3.1/3.2/3.3 entries; users on those versions
    # should consult Red Hat docs directly. The mlflow change applies to
    # any 3.x → 3.4 upgrade.
    ('rhoai', '3.0', '3.4'): [
        {
            'description': (
                'mlflowoperator DSC component added in 3.4. '
                'The old dashboard.mlflow feature flag is removed.'
            ),
            'severity': 'warning',
            'action': (
                'Remove any dashboard.mlflow feature flag from DSC. '
                'Add mlflowoperator: Managed to the DataScienceCluster spec.'
            ),
        },
    ],
    ('rhoai', '3.4', '3.5'): [
        # No blocking changes — standard channel bump.
    ],
}


# ── Public API ─────────────────────────────────────────────────────────────────


def resolve_version(platform: str, version: str) -> dict:
    """Return version-specific overrides for the given platform and version.

    Raises ValueError for unknown platform or version with a helpful message
    listing known alternatives.
    """
    platform_data = PLATFORM_VERSIONS.get(platform)
    if platform_data is None:
        known = sorted(PLATFORM_VERSIONS)
        raise ValueError(
            f"Unknown platform {platform!r}. "
            f"Known platforms: {', '.join(known)}"
        )

    version_data = platform_data.get(version)
    if version_data is None:
        known = sorted(k for k in platform_data)
        raise ValueError(
            f"Unknown version {version!r} for platform {platform!r}. "
            f"Known versions: {', '.join(known)}"
        )

    return version_data


def list_versions(platform: str) -> list:
    """Return sorted list of known version strings for a platform."""
    platform_data = PLATFORM_VERSIONS.get(platform, {})
    return sorted(platform_data)


def get_breaking_changes(platform: str, from_version: str, to_version: str) -> list:
    """Return breaking-change entries for an upgrade path, or [] if none."""
    return UPGRADE_BREAKING_CHANGES.get((platform, from_version, to_version), [])
