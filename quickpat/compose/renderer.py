"""Jinja2 template context builder for block generates sections.

Provides make_context() and render_template() for resolving {{ app.name }},
{{ block.name }}, {{ config.x }}, and {{ blocks.x.output.y }} references.
Used now for custom component stub generation; will drive full block template
rendering in future phases.
"""

from jinja2 import Environment, Undefined


def make_context(spec, block_name: str, block_instance, resolved_outputs: dict = None) -> dict:
    """Build a Jinja2 template context for a single block.

    Args:
        spec: ApplicationSpec instance
        block_name: name of the block being rendered
        block_instance: BlockInstance for that block
        resolved_outputs: map of block_name -> output_name -> value,
                          for resolving cross-block {{ blocks.x.output.y }} refs

    Returns:
        dict suitable for passing to render_template()
    """
    return {
        'app': {'name': spec.name},
        'block': {
            'name': block_name,
            'type': block_instance.block_type,
        },
        'config': block_instance.config,
        'blocks': resolved_outputs or {},
    }


def make_custom_context(spec, comp_name: str, comp, resolved_outputs: dict = None) -> dict:
    """Build a Jinja2 template context for a custom component."""
    return {
        'app': {'name': spec.name},
        'component': {'name': comp_name},
        'config': {
            'image': comp.image,
            'replicas': comp.replicas,
            'ports': comp.ports,
            'env': comp.env,
            'resources': comp.resources,
        },
        'blocks': resolved_outputs or {},
    }


def render_template(template_str: str, context: dict) -> str:
    """Render a Jinja2 template string with the given context.

    Unknown variables resolve to empty string rather than raising.
    """
    env = Environment(undefined=Undefined)
    return env.from_string(template_str).render(**context)
