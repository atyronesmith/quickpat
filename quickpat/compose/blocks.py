"""Block type registry — maps block types to what they contribute to the VP."""

# Each entry declares:
#   operators:        operator keys from OPERATORS dict (in operators.py)
#   needs_oai_labels: whether app namespace needs opendatahub labels
#
# INFRA_CHARTS in operators.py already maps operator keys to local chart
# templates (dsc, nfd, nvidia-config), so we don't need to redeclare those.
# The compiler's job is to collect the right operator set from the blocks.

BLOCK_TYPES = {
    'ai-platform-foundation': {
        'operators': ['openshift-ai', 'serverless', 'servicemesh'],
        'needs_oai_labels': False,
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
        'operators': [],
        'needs_oai_labels': False,
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
