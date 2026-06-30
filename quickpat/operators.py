"""Registry of OpenShift operators and their detection indicators."""

OPERATORS = {
    'openshift-ai': {
        'subscription_key': 'rhoai',
        'subscription_name': 'rhods-operator',
        'display_name': 'Red Hat OpenShift AI',
        'namespace': 'redhat-ods-operator',
        'channel': 'fast',
        'source': 'redhat-operators',
        'indicators': [
            'inferenceservice', 'servingruntime', 'datasciencecluster',
            'llm-service', 'llama-stack', 'vllm', 'model-service',
            'openshift-ai', 'rhods',
        ],
        'co_dependencies': ['servicemesh', 'serverless'],
        'namespace_config': {
            'operatorGroup': True,
            'targetNamespaces': [],
        },
    },
    'openshift-pipelines': {
        'subscription_name': 'openshift-pipelines-operator-rh',
        'display_name': 'OpenShift Pipelines',
        'namespace': 'openshift-operators',
        'channel': 'latest',
        'source': 'redhat-operators',
        'indicators': [
            'pipeline', 'pipelinerun', 'task', 'taskrun',
            'ingestion-pipeline', 'tekton',
        ],
        'co_dependencies': [],
    },
    'nvidia-gpu': {
        'subscription_key': 'nvidia',
        'subscription_name': 'gpu-operator-certified',
        'display_name': 'NVIDIA GPU Operator',
        'namespace': 'nvidia-gpu-operator',
        'channel': 'v24.9',
        'source': 'certified-operators',
        'indicators': ['gpu', 'nvidia', 'cuda'],
        'co_dependencies': ['nfd'],
    },
    'nfd': {
        'subscription_name': 'nfd',
        'display_name': 'Node Feature Discovery',
        'namespace': 'openshift-nfd',
        'channel': 'stable',
        'source': 'redhat-operators',
        'indicators': [],
        'co_dependencies': [],
    },
    'servicemesh': {
        'subscription_name': 'servicemeshoperator',
        'display_name': 'OpenShift Service Mesh',
        'namespace': 'openshift-operators',
        'channel': 'stable',
        'source': 'redhat-operators',
        'indicators': ['servicemesh', 'istio', 'servicemeshcontrolplane'],
        'co_dependencies': [],
    },
    'serverless': {
        'subscription_name': 'serverless-operator',
        'display_name': 'OpenShift Serverless',
        'namespace': 'openshift-serverless',
        'channel': 'stable',
        'source': 'redhat-operators',
        'indicators': ['knativeserving', 'knative', 'serverless'],
        'co_dependencies': [],
        'namespace_config': {
            'operatorGroup': True,
            'targetNamespaces': [],
        },
    },
    'amq-streams': {
        'subscription_name': 'amq-streams',
        'display_name': 'AMQ Streams (Kafka)',
        'namespace': 'openshift-operators',
        'channel': 'stable',
        'source': 'redhat-operators',
        'indicators': ['kafka', 'kafkatopic', 'kafkaconnect', 'amq-streams'],
        'co_dependencies': [],
    },
}


def resolve_co_dependencies(selected_keys):
    """Given selected operator keys, transitively add co-dependencies."""
    resolved = set(selected_keys)
    changed = True
    while changed:
        changed = False
        for key in list(resolved):
            for dep in OPERATORS.get(key, {}).get('co_dependencies', []):
                if dep not in resolved:
                    resolved.add(dep)
                    changed = True
    return sorted(resolved)
