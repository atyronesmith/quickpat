# Shared Chart Reuse Across AI Quickstarts

## Shared Chart Repository

All quickstarts pull from a single shared Helm repo:
`https://rh-ai-quickstart.github.io/ai-architecture-charts`

### Available Charts (9)

| Chart | Latest Version |
|---|---|
| pgvector | 0.5.5 |
| llm-service | 0.5.9 |
| llama-stack | 0.7.0 |
| mcp-servers | 0.5.15 |
| minio | 0.5.4 |
| ingestion-pipeline | 0.7.0 |
| configure-pipeline | 0.5.6 |
| oracle-db | 0.5.5 |
| model-registry | 0.2.1 |

## Usage Across Quickstarts

| Shared Chart | # Quickstarts | Versions In Use |
|---|---|---|
| pgvector | 7 | 0.1.0, 0.5.0, 0.5.5 |
| llm-service | 5 | 0.5.2 – 0.5.9 |
| llama-stack | 5 | 0.5.2 – 0.6.11 |
| mcp-servers | 4 | 0.5.7 – 0.5.15 |
| minio | 2 | 0.1.0 |
| ingestion-pipeline | 2 | 0.6.5, 0.6.6 |
| configure-pipeline | 2 | 0.5.6 |
| oracle-db | 1 | 0.5.5 |

## Per-Quickstart Dependency Map

| Quickstart | Dependencies |
|---|---|
| RAG | pgvector 0.5.5, llm-service 0.5.9, llama-stack 0.6.11, mcp-servers 0.5.15, configure-pipeline 0.5.6, ingestion-pipeline 0.6.6 |
| ai-virtual-agent | pgvector 0.5.5, llm-service 0.5.9, llama-stack 0.6.10, mcp-servers 0.5.15, configure-pipeline 0.5.6, ingestion-pipeline 0.6.5, oracle-db 0.5.5 |
| ai-obs-summarizer | pgvector 0.5.0, llm-service 0.5.4, llama-stack 0.5.3, minio 0.1.0 |
| it-self-service-agent | pgvector 0.1.0, llm-service 0.5.6, llama-stack 0.6.9, mcp-servers 0.5.8 |
| f5-api-security | pgvector 0.1.0, llm-service 0.5.2, llama-stack 0.5.2 |
| ansible-log-analysis | pgvector 0.1.0, minio 0.1.0, mcp-servers 0.5.7 |
| product-recommender | pgvector 0.1.0 |
| llm-cpu-serving | (none — self-contained) |
| guardrailing-llms | (none — self-contained) |
| maas-code-assistant | (local charts only, no shared deps) |
| lls-observability | (local charts only, bundles own llama-stack) |

## Version Drift

Several quickstarts pin old versions:
- **pgvector**: 4 quickstarts on 0.1.0, latest is 0.5.5
- **llama-stack**: f5 on 0.5.2, ai-obs on 0.5.3, latest is 0.7.0
- **llm-service**: f5 on 0.5.2, latest is 0.5.9
- **minio**: ai-obs and ansible-log on 0.1.0, latest is 0.5.4

## Local Forks

Some quickstarts bundle their own copy of a shared chart instead of using the dependency:
- **ai-obs** bundles local `minio` at `deploy/helm/minio/`
- **product-rec** bundles local `minio` at `helm/minio/`
- **lls-obs** bundles local `llama-stack` at `helm/03-ai-services/llama-stack/`
