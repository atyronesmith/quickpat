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


INFRA_CHARTS = {
    'openshift-ai': {
        'chart_name': 'dsc',
        'description': 'DataScienceCluster configuration for RHOAI',
        'namespace': 'redhat-ods-operator',
        'template_name': 'datasciencecluster.yaml',
        'cr': {
            'apiVersion': 'datasciencecluster.opendatahub.io/v1',
            'kind': 'DataScienceCluster',
            'metadata': {'name': 'default-dsc'},
            'spec': {
                'components': {
                    'codeflare': {},
                    'dashboard': {'managementState': 'Managed'},
                    'datasciencepipelines': {'managementState': 'Removed'},
                    'kserve': {'managementState': 'Managed'},
                    'kueue': {'managementState': 'Removed'},
                    'modelmeshserving': {},
                    'ray': {'managementState': 'Removed'},
                    'trainingoperator': {'managementState': 'Removed'},
                    'trustyai': {'managementState': 'Managed'},
                    'workbenches': {'managementState': 'Removed'},
                },
            },
        },
    },
    'nfd': {
        'chart_name': 'nfd',
        'description': 'NodeFeatureDiscovery instance for GPU node detection',
        'namespace': 'openshift-nfd',
        'template_name': 'nodefeaturediscovery.yaml',
        'cr': {
            'apiVersion': 'nfd.openshift.io/v1',
            'kind': 'NodeFeatureDiscovery',
            'metadata': {'name': 'nfd-instance'},
            'spec': {
                'instance': '',
                'operand': {
                    'image': 'registry.redhat.io/openshift4/ose-node-feature-discovery-rhel9:latest',
                    'servicePort': 12000,
                },
                'topologyUpdater': False,
                'workerConfig': {
                    'configData': (
                        'core:\n'
                        '  sleepInterval: 60s\n'
                        'sources:\n'
                        '  pci:\n'
                        '    deviceClassWhitelist:\n'
                        '      - "0200"\n'
                        '      - "03"\n'
                        '      - "12"\n'
                        '    deviceLabelFields:\n'
                        '      - "vendor"\n'
                    ),
                },
            },
        },
    },
    'nvidia-gpu': {
        'chart_name': 'nvidia-config',
        'description': 'NVIDIA GPU Operator ClusterPolicy configuration',
        'namespace': 'nvidia-gpu-operator',
        'template_name': 'clusterpolicy.yaml',
        'cr': {
            'apiVersion': 'nvidia.com/v1',
            'kind': 'ClusterPolicy',
            'metadata': {'name': 'gpu-cluster-policy'},
            'spec': {
                'vgpuDeviceManager': {'enabled': True},
                'migManager': {'enabled': True},
                'operator': {
                    'defaultRuntime': 'crio',
                    'initContainer': {},
                    'runtimeClass': 'nvidia',
                    'use_ocp_driver_toolkit': True,
                },
                'dcgm': {'enabled': True},
                'gfd': {'enabled': True},
                'dcgmExporter': {
                    'config': {'name': ''},
                    'enabled': True,
                    'serviceMonitor': {'enabled': True},
                },
                'driver': {
                    'certConfig': {'name': ''},
                    'enabled': True,
                    'kernelModuleConfig': {'name': ''},
                    'licensingConfig': {'configMapName': '', 'nlsEnabled': False},
                    'upgradePolicy': {
                        'autoUpgrade': True,
                        'drain': {
                            'deleteEmptyDir': False,
                            'enable': False,
                            'force': False,
                            'timeoutSeconds': 300,
                        },
                        'maxParallelUpgrades': 1,
                        'maxUnavailable': '25%',
                        'podDeletion': {
                            'deleteEmptyDir': False,
                            'force': False,
                            'timeoutSeconds': 300,
                        },
                        'waitForCompletion': {'timeoutSeconds': 0},
                    },
                    'virtualTopology': {'config': ''},
                },
                'devicePlugin': {
                    'config': {'default': '', 'name': ''},
                    'enabled': True,
                },
                'mig': {'strategy': 'single'},
                'sandboxDevicePlugin': {'enabled': True},
                'validator': {
                    'plugin': {
                        'env': [{'name': 'WITH_WORKLOAD', 'value': 'false'}],
                    },
                },
                'nodeStatusExporter': {'enabled': True},
                'daemonsets': {
                    'rollingUpdate': {'maxUnavailable': '1'},
                    'tolerations': [{
                        'effect': 'NoSchedule',
                        'key': 'nvidia.com/gpu',
                        'value': 'true',
                    }],
                    'updateStrategy': 'RollingUpdate',
                },
                'sandboxWorkloads': {
                    'defaultWorkload': 'container',
                    'enabled': False,
                },
                'gds': {'enabled': False},
                'vgpuManager': {'enabled': False},
                'vfioManager': {'enabled': True},
                'toolkit': {'enabled': True, 'installDir': '/usr/local/nvidia'},
            },
        },
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
