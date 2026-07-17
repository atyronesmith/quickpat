"""Translate an ApplicationSpec into (QuickstartAnalysis, config) for PatternGenerator."""

from ..analyzer import QuickstartAnalysis, ChartInfo, SecretRef
from ..operators import resolve_co_dependencies
from .blocks import get_block_def
from .parser import ApplicationSpec


class ComposeError(Exception):
    """Raised when a spec cannot be compiled."""


def compile_spec(
    spec: ApplicationSpec,
    output_dir: str,
    spec_dir: str = None,
    create_service_account: bool = True,
) -> tuple:
    """Compile an ApplicationSpec into (analysis, config) for PatternGenerator.

    Args:
        spec: Parsed ApplicationSpec.
        output_dir: Where to write the VP output. When None, caller should
                    default to spec_dir/vp-out/.
        spec_dir: Directory containing spec.yaml. Used to auto-detect existing
                  hand-written charts and to resolve the default output_dir.

    Returns (QuickstartAnalysis, config_dict) ready for PatternGenerator.generate().
    """
    # 1. Collect operators and flags from all blocks
    raw_ops = set()
    needs_oai_labels = False

    for block_name, block in spec.blocks.items():
        try:
            block_def = get_block_def(block.block_type)
        except KeyError as e:
            raise ComposeError(str(e))

        raw_ops.update(block_def['operators'])
        if block_def['needs_oai_labels']:
            needs_oai_labels = True

    # Resolve co-dependencies (e.g. openshift-ai pulls serverless+servicemesh,
    # though we also declare them explicitly in ai-platform-foundation)
    operators = list(resolve_co_dependencies(raw_ops))

    # 2. Build secret_groups from block secret declarations.
    #    Format mirrors what _profile_to_config() produces so PatternGenerator
    #    can write the ExternalSecret templates and values-secret.yaml.template.
    secret_groups = {}
    all_secrets = []

    for block_name, block in spec.blocks.items():
        for sec_name, sec_decl in block.secrets.items():
            classification = 'auto-generate' if sec_decl.generate else 'vault-secret'
            group = secret_groups.setdefault(block_name, [])
            group.append({
                'name': sec_name,
                'classification': classification,
                'vault_path': sec_decl.vault_path,
                'vault_key': sec_decl.key or sec_name,
                'default_value': '',
            })
            all_secrets.append(SecretRef(
                name=f"{block_name}-{sec_name}",
                path=sec_decl.vault_path,
            ))

    use_vault = bool(all_secrets)

    # 3. Build the main chart (upstream QS, remote strategy).
    #    Application-level blocks (model-serving, object-storage, guardrails)
    #    are served by the upstream chart — no decomposition in Phase 1.
    main_chart = ChartInfo(name=spec.name)
    main_chart.strategy = 'remote'
    main_chart.needs_oai_labels = needs_oai_labels
    if spec.upstream.repo:
        main_chart.chart_path = ''

    # 4. Build QuickstartAnalysis
    analysis = QuickstartAnalysis(
        name=spec.name,
        version='0.1.0',
        description=spec.description,
        charts=[main_chart],
        detected_operators=set(operators),
        detected_secrets=all_secrets,
    )

    # 5. Extract infra block configs so the generator can produce
    #    config-driven DSC and ClusterPolicy CRs rather than hardcoded defaults.
    dsc_config = {}
    gpu_config = {}
    has_llama_stack = False
    for block_name, block in spec.blocks.items():
        if block.block_type == 'ai-platform-foundation':
            dsc_config = block.config.get('dsc', {})
        elif block.block_type == 'gpu-compute':
            gpu_config = block.config
        elif block.block_type == 'llama-stack':
            has_llama_stack = True

    # When a llama-stack block is present, ensure llamastackoperator is Managed
    # in the DSC so the RHOAI operator installs and manages the LlamaStack runtime.
    if has_llama_stack:
        dsc_config = dict(dsc_config)
        dsc_config.setdefault('llamastackoperator', 'Managed')

    # 6. Detect hand-written charts in the application repo.
    #    Any custom component that already has a Chart.yaml next to spec.yaml
    #    gets copied rather than stubbed.
    from pathlib import Path as _Path
    existing_custom_charts = set()
    if spec_dir:
        for comp_name in spec.custom:
            chart_yaml = _Path(spec_dir) / 'charts' / comp_name / 'Chart.yaml'
            if chart_yaml.exists():
                existing_custom_charts.add(comp_name)

    # 7. Build config
    config = {
        'pattern_name': spec.name,
        'app_name': spec.name,
        'app_namespace': spec.name,
        'operators': operators,
        'chart_strategy': 'remote',
        'use_vault': use_vault,
        'output_dir': output_dir,
        'clustergroup_version': '0.9.*',
        'tier': spec.tier,
        'git_repo_url': spec.upstream.repo,
        'chart_path_in_repo': spec.upstream.path,
        'chart_branch': spec.upstream.branch,
        'secret_groups': secret_groups,
        # Config-driven infra chart overrides
        'dsc_config': dsc_config,
        'gpu_config': gpu_config,
        # Custom components — generator copies real charts or produces stubs
        'custom_components': dict(spec.custom),
        'existing_custom_charts': existing_custom_charts,
        'spec_dir': spec_dir,
        # Add "Generated by quickpat compose" headers to all YAML output
        'generated_headers': True,
        # Whether to generate SA + Role + RoleBinding for ODF setup Job
        'create_service_account': create_service_account,
        # Raw block configs for future use
        '_block_configs': {
            name: block.config
            for name, block in spec.blocks.items()
        },
    }

    return analysis, config
