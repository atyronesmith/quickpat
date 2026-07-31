# Adding a New Block Type to QuickPat

Block types are the named building blocks a spec author puts in the `blocks:` section of a composition spec. Each one maps to a set of operators, optionally an infra chart CR, and a set of outputs that downstream blocks can wire to.

Adding a new block type touches two files and (if it needs an operator that doesn't exist yet) possibly a third.

---

## 1. Decide what the block IS

A block type represents a platform capability, not a specific application. Ask:

- Does it install an operator? Which one?
- Does it require a CR to activate (like a HyperConverged or DataScienceCluster)? That becomes an infra chart.
- Does it produce a URL, hostname, or connection string that other blocks need? Those are `outputs`.
- Does the namespace it runs in need `opendatahub.io` labels? (Only RHOAI-managed namespaces do.)

---

## 2. Add the operator (if new) — `quickpat/operators.py`

If the block needs an operator that isn't already in `OPERATORS`, add an entry:

```python
'my-operator-key': {
    'subscription_name': 'the-operator-package-name',  # OperatorHub package name
    'display_name': 'Human Readable Name',
    'namespace': 'operator-namespace',
    'channel': 'stable',                               # OLM channel
    'source': 'redhat-operators',                      # or 'certified-operators', 'community-operators'
    'indicators': ['crd-kind', 'keyword'],             # used by analyzer to auto-detect this operator
    'co_dependencies': [],                             # other operator keys that must also be installed
    'namespace_config': {                              # optional: if the namespace needs an OperatorGroup
        'operatorGroup': True,
        'targetNamespaces': ['operator-namespace'],    # [] means all-namespace watch
    },
},
```

If the operator also needs a CR to activate (e.g., a HyperConverged, DataScienceCluster, ClusterPolicy), add an entry to `INFRA_CHARTS` in the same file:

```python
INFRA_CHARTS = {
    'my-operator-key': {
        'chart_name': 'my-infra-chart',        # folder name under charts/ in the generated VP
        'description': 'One-line description',
        'namespace': 'operator-namespace',
        'template_name': 'my-cr.yaml',         # file name inside templates/
        'cr': {
            'apiVersion': 'example.io/v1',
            'kind': 'MyCR',
            'metadata': {'name': 'instance-name'},
            'spec': { ... },                   # full default CR spec
        },
    },
}
```

The compiler copies the `cr` dict into the chart template verbatim. If the CR needs per-pattern customization (like DSC component flags), the block's `config` values are merged over the defaults at compile time.

---

## 3. Register the block type — `quickpat/compose/blocks.py`

Add an entry to `BLOCK_TYPES`:

```python
'my-block-type': {
    # Operator keys from OPERATORS that this block requires.
    # The compiler adds these to values-prod.yaml subscriptions automatically.
    # Use [] if no additional operators are needed (e.g., the block depends on
    # another block's operator rather than its own).
    'operators': ['my-operator-key'],

    # True only if the block's namespace must have opendatahub.io labels
    # (required for KServe InferenceService namespaces managed by RHOAI).
    'needs_oai_labels': False,

    # Document outputs here as a comment — there's no runtime schema enforcement yet.
    # outputs: issuer_url, gateway_route, connection_name, predictor_host, etc.
    # These are referenced in spec wiring as {{ blocks.<name>.output.<key> }}
},
```

Add a comment explaining what the block does and what it outputs. The `get_block_def()` function raises a clear `KeyError` with the list of known types if an author uses a typo — so the list here is the authoritative registry.

---

## 4. Write a test

Add a minimal smoke test to `tests/test_compose_app_spec.py`:

```python
def test_my_block_type_is_valid():
    spec_yaml = """
apiVersion: supplychain/v1alpha1
kind: ApplicationSpec
metadata:
  name: test-my-block
blocks:
  thing:
    type: my-block-type
"""
    spec = ApplicationSpec.from_yaml(spec_yaml)
    assert spec.blocks['thing'].block_type == 'my-block-type'
```

Run the suite:

```bash
uv run pytest tests/
```

---

## 5. Update the spec example (optional but recommended)

If the block type is general enough to be reused across patterns, add a usage example to `examples/sample-spec.yaml` with a comment explaining the config options.

---

## Example: blocks added for secure-agent-workspace (2026-07-30)

Three block types were added together because they form a unit — VM isolation requires all three:

| Block type | Operator key | INFRA_CHART | Outputs |
|---|---|---|---|
| `openshift-virtualization` | `openshift-virtualization` (kubevirt-hyperconverged) | `openshift-cnv` (HyperConverged CR) | none |
| `keycloak-oidc` | `rhbk` (rhbk-operator) | `keycloak-config` (Keycloak + RealmImport CRs) | `issuer_url` |
| `vm-workspace` | none (uses openshift-virtualization's runtime) | none | `gateway_route`, `dashboard_route` |

The `vm-workspace` block lists no operators because it depends on the `openshift-virtualization` block being present in the same spec — the compiler enforces that dependency via the `wiring:` or `inputs:` declarations.

Reference spec: `examples/secure-agent-workspace-spec.yaml` (or `topics/quickpat/artifacts/` in the fcto-work repo).
