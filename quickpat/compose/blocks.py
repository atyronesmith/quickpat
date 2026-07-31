"""Block type registry — maps block types to what they contribute to the VP."""

# Each entry declares:
#   operators:        operator keys from OPERATORS dict (in operators.py)
#   needs_oai_labels: whether app namespace needs opendatahub labels
#
# INFRA_CHARTS in operators.py already maps operator keys to local chart
# templates (dsc, nfd, nvidia-config), so we don't need to redeclare those.
# The compiler's job is to collect the right operator set from the blocks.

BLOCK_TYPES = {
    # --- VM / Identity blocks ---

    'openshift-virtualization': {
        # Installs the kubevirt-hyperconverged operator and creates a HyperConverged CR.
        # Provides the KubeVirt runtime and CDI (Containerized Data Importer) that
        # vm-workspace blocks depend on for per-user VM provisioning.
        # outputs: none (runtime dependency, no URL/host produced)
        'operators': ['openshift-virtualization'],
        'needs_oai_labels': False,
    },
    'keycloak-oidc': {
        # Installs RHBK (Red Hat Build of Keycloak) and creates a Keycloak CR +
        # KeycloakRealmImport. The realm name, test users, and client config come
        # from block config. Outputs the OIDC issuer URL for downstream consumers.
        # outputs: issuer_url (used by vm-workspace inputs.oidc_issuer)
        'operators': ['rhbk'],
        'needs_oai_labels': False,
    },
    'vm-workspace': {
        # Provisions per-user KubeVirt VMs from a CDI DataSource (golden image).
        # Each VM runs the configured gateway + agent. The namespace_mode config
        # controls whether all sandboxes share one namespace or each gets its own.
        # Requires openshift-virtualization block in the same spec.
        # outputs: gateway_route (gRPC/TLS passthrough), dashboard_route (edge TLS)
        'operators': [],
        'needs_oai_labels': False,
    },
    'ai-platform-foundation': {
        'operators': ['openshift-ai', 'serverless', 'servicemesh'],
        'needs_oai_labels': False,
        # config.dsc: optional map of DSC component name → managementState string.
        # e.g. {trustyai: Managed, datasciencepipelines: Removed}
        # Merged over the default DSC CR at generation time. Omit to use
        # conservative defaults (kserve + dashboard only). Required when the
        # application uses TrustyAI guardrails or DataScience Pipelines.
    },
    'gpu-compute': {
        'operators': ['nvidia-gpu', 'nfd'],
        'needs_oai_labels': False,
    },
    'model-serving': {
        # No additional operators — depends on ai-platform-foundation
        'operators': [],
        'needs_oai_labels': True,  # KServe InferenceService namespace needs OAI labels
    },
    'object-storage': {
        'operators': [],
        'needs_oai_labels': False,
    },
    'guardrails-orchestrator': {
        # TrustyAI is enabled via DSC (trustyai: Managed), not a separate subscription
        'operators': [],
        'needs_oai_labels': False,
    },
    'vector-store': {
        'operators': [],
        'needs_oai_labels': False,
    },
    'data-pipeline': {
        'operators': ['openshift-pipelines'],
        'needs_oai_labels': False,
    },
    'sso-auth': {
        # Semantic marker only — installs NO operators.
        # Use this block to declare that your pattern requires SSO/OIDC,
        # but supply the actual identity provider (Keycloak, Dex, etc.)
        # as a custom component. If you need the RHBK operator installed,
        # use the keycloak-oidc block type instead.
        'operators': [],
        'needs_oai_labels': False,
    },
    'llama-stack': {
        # LlamaStack operator is enabled via DSC (llamastackoperator: Managed).
        # The compiler injects that DSC component automatically when this block
        # is present — no separate subscription needed.
        'operators': [],
        'needs_oai_labels': True,
    },
}


def get_block_def(block_type: str) -> dict:
    """Return block definition or raise KeyError for unknown types."""
    if block_type not in BLOCK_TYPES:
        raise KeyError(
            f"Unknown block type: {block_type!r}. "
            f"Known types: {sorted(BLOCK_TYPES)}"
        )
    return BLOCK_TYPES[block_type]
