"""Domain (skill-area) taxonomy for tagging tickets, shared by the Suggested Assignments page.

A copy of darkstar's Intake taxonomy (DOMAIN_PATTERNS, DOMAIN_PRIORITY, DOMAIN_GROUP in
darkstar/dashboards/intake.html) so both apps classify work the same way; tests/test_assignments.py
fails if the two drift apart. Patterns are case-insensitive regular expressions matched against a
ticket's text; a ticket's *primary* domain is the first match in DOMAIN_PRIORITY (niche domains rank
above broad ones such as Kubernetes/GitOps or GitLab).
"""
from __future__ import annotations

import re
from functools import lru_cache

DOMAIN_PATTERNS: dict[str, list[str]] = {'VDI/WorkSpaces': ['vdi', 'workspace', 'gcve', 'vsphere', 'vmware', 'citrix'],
 'GCP Core': ['prj-',
              'gcp project',
              'landing zone',
              '\\bgcp\\b',
              'project build',
              'project factory',
              '\\bfolder',
              'cloud\\s?run',
              '\\bgcs\\b',
              'cloud storage',
              'artifact\\s?registry',
              'pub\\s?sub',
              'cloud\\s?logging',
              'cloud\\s?kms'],
 'BigQuery/Data': ['bigquery', 'big query', 'dataflow', 'dataproc', '\\bedp\\b', '\\bbq\\b', 'reservation'],
 'Grafana': ['grafana',
             '\\balert',
             'dashboard',
             'prometheus',
             '\\bmetric',
             'firehose',
             'scrape',
             '\\bloki\\b',
             'observability',
             'monitoring',
             '\\btempo\\b',
             'pyroscope',
             'datadog'],
 'Terraform/Terragrunt': ['terraform', 'tf-', 'terragrunt', '\\biac\\b', 'tflint', 'tfstate'],
 'Kubernetes/GitOps': ['kubernetes',
                       '\\bk8s\\b',
                       '\\beks\\b',
                       '\\bgke\\b',
                       'helm',
                       'cronjob',
                       'namespace',
                       '\\bcluster',
                       '\\bpod\\b',
                       'gitops',
                       'argocd',
                       '\\bflux\\b',
                       'kubectl',
                       'ingress',
                       'karpenter'],
 'Airflow': ['airflow', '\\bdag\\b'],
 'Composer': ['composer'],
 'Storage Transfer': ['storage transfer', '\\bsts\\b'],
 'IAM/RBAC': ['\\biam\\b',
              'service account',
              'workload identity',
              '\\bwif\\b',
              'permission',
              '\\brole',
              '\\brbac\\b',
              '\\bsso\\b',
              'okta'],
 'Networking': ['network',
                '\\bdns\\b',
                '\\bvpc\\b',
                'subnet',
                'firewall',
                'egress',
                'peering',
                '\\bvpn\\b',
                'load balancer',
                'cloudflare'],
 'Databases': ['cloudsql',
               'cloud sql',
               'alloydb',
               '\\brds\\b',
               'database',
               'postgres',
               'mysql',
               '\\bredis\\b',
               'memorystore'],
 'GitLab': ['pipeline',
            'ci/cd',
            'gitlab',
            'runner',
            '\\becr\\b',
            'docker image',
            'artifact registry',
            'cicd'],
 'Cost/FinOps': ['\\bcogs\\b', '\\bcost', 'billing', 'finops', 'budget', 'spend'],
 'Secrets/Vault': ['vault', '\\bsecret', 'credential'],
 'AWS Core': ['\\baws\\b', 'cloudwatch', '\\bec2\\b', '\\bs3\\b', 'lambda', 'organizations', 'control tower'],
 'AI Plugins': ['ai-plugins', 'ai plugin', 'claude code', 'claude-code', 'claude plugin'],
 'AWS Bedrock Agents': ['bedrock', 'pe-agent', 'claude-sdk', 'bedrock agent', 'agentcore', 'devops-agent'],
 'EKS': ['\\beks\\b'],
 'ECS': ['\\becs\\b', 'fargate'],
 'OpenSearch': ['opensearch', 'elasticsearch'],
 'MSK': ['\\bmsk\\b', 'kafka', 'managed streaming'],
 'GKE': ['\\bgke\\b'],
 'VertexAI': ['vertex ai', 'vertexai', '\\bvertex\\b', 'aiplatform'],
 'Kubeflow Pipelines': ['kubeflow', '\\bkfp\\b'],
 'Route53': ['route\\s?53', '\\br53\\b', 'tf-sharedservices'],
 'Fastly': ['fastly'],
 'GCP Agent Platform': ['agent-?platform', 'agent[-_ ]?space'],
 'Firestore': ['firestore'],
 'Firebase': ['firebase'],
 'Looker': ['looker'],
 'Knowledge Catalog': ['knowledge-?catalog', 'knowledge catalog'],
 'Terraform Modules': ['tf-module', 'terraform module', 'tf module', 'reusable module', 'module release']}

DOMAIN_PRIORITY: list[str] = ['VDI/WorkSpaces',
 'AI Plugins',
 'AWS Bedrock Agents',
 'GCP Agent Platform',
 'Firebase',
 'Firestore',
 'Looker',
 'Knowledge Catalog',
 'Terraform Modules',
 'EKS',
 'GKE',
 'VertexAI',
 'Kubeflow Pipelines',
 'OpenSearch',
 'ECS',
 'MSK',
 'Route53',
 'Fastly',
 'Airflow',
 'Composer',
 'Storage Transfer',
 'BigQuery/Data',
 'Databases',
 'GCP Core',
 'Grafana',
 'Networking',
 'IAM/RBAC',
 'Secrets/Vault',
 'Cost/FinOps',
 'Terraform/Terragrunt',
 'AWS Core',
 'Kubernetes/GitOps',
 'GitLab']

# Top-level grouping; domains not listed fall under "Other".
DOMAIN_GROUP: dict[str, str] = {'GCP Core': 'GCP',
 'BigQuery/Data': 'GCP',
 'Composer': 'GCP',
 'Airflow': 'GCP',
 'Storage Transfer': 'GCP',
 'GKE': 'GCP',
 'VertexAI': 'GCP',
 'Kubeflow Pipelines': 'GCP',
 'AWS Core': 'AWS',
 'AWS Bedrock Agents': 'AWS',
 'VDI/WorkSpaces': 'AWS',
 'EKS': 'AWS',
 'ECS': 'AWS',
 'OpenSearch': 'AWS',
 'MSK': 'AWS',
 'GCP Agent Platform': 'GCP',
 'Firebase': 'GCP',
 'Firestore': 'GCP',
 'Looker': 'GCP',
 'Knowledge Catalog': 'GCP'}


@lru_cache(maxsize=1)
def _compiled() -> list[tuple[str, list[re.Pattern]]]:
    return [(d, [re.compile(p, re.I) for p in ps]) for d, ps in DOMAIN_PATTERNS.items()]


def tag(text: str) -> list[str]:
    """Every domain whose patterns match `text`."""
    text = text or ""
    return [d for d, regexes in _compiled() if any(r.search(text) for r in regexes)]


def primary(domains: list[str]) -> str | None:
    return next((d for d in DOMAIN_PRIORITY if d in domains), None)


def group_of(domain: str) -> str:
    return DOMAIN_GROUP.get(domain, "Other")
