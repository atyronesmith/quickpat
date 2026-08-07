"""Quickstart analysis report generation."""

from ..operators import OPERATORS

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
        for op_key in sorted(analysis.detected_operators):
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
