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
             'datadog',
             '\\bcontact\\W+points?\\b'],
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
 'GKE': ['\\bgke\\b', '\\bnode\\W+pool\\b', '\\bcontrol\\W+plane\\b', '\\bai-selfservice-nonprod\\b',
         '\\bai-initiatives-nonprod\\b'],
 'VertexAI': ['vertex ai', 'vertexai', '\\bvertex\\b', 'aiplatform', '\\bworkbench\\b', '\\bdata\\W+hub\\b',
              '\\blogging\\.logwriter\\b'],
 'Kubeflow Pipelines': ['kubeflow', '\\bkfp\\b'],
 'Route53': ['route\\s?53', '\\br53\\b', 'tf-sharedservices', '\\bcname\\b', '\\bapex\\b', '\\bpni\\b',
             '\\bentercom-prod\\b'],
 'Fastly': ['fastly', '\\bwww\\.audacy\\.com\\b', '\\bnameservers\\b'],
 'CloudFront': ['cloud\\s?front'],
 'Upwind': ['upwind'],
 'GCP Agent Platform': ['agent-?platform', 'agent[-_ ]?space'],
 'Firestore': ['firestore'],
 'Firebase': ['firebase'],
 'Looker': ['looker'],
 'Knowledge Catalog': ['knowledge-?catalog', 'knowledge catalog'],
 'Terraform Modules': ['tf-module', 'terraform module', 'tf module', 'reusable module', 'module release'],
 # Applications (mostly AdTech / AmperWave, released through CAR tickets): learned from ticket text, 2026-11.
 'Vader': ['\\bvader\\b'],
 'Arc': ['\\barc\\b'],
 'Clarity': ['\\bclarity\\b'],
 'Bifrost': ['\\bbifrost\\b'],
 'RAD': ['\\brad\\b'],
 'Studio Console': ['\\bstudio\\W+console\\b', '\\baw-awpod\\W+studio\\b'],
 'AdTech Insights': ['\\badtech\\W+insights\\b'],
 'ads.txt': ['\\b(app-)?ads\\.txt\\b']}

# Applications first: a ticket that names its application is routed by it (back-tested 2026-11: +3 top-3 hits).
DOMAIN_PRIORITY: list[str] = ['Vader',
 'Arc',
 'Clarity',
 'Bifrost',
 'RAD',
 'Studio Console',
 'AdTech Insights',
 'ads.txt',
 'VDI/WorkSpaces',
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
 'CloudFront',
 'Upwind',
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

# Top-level grouping; domains not listed fall under "Other". Security = SecOps tools. Applications = products the
# team deploys and supports (not cloud services).
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
 'Knowledge Catalog': 'GCP',
 'CloudFront': 'AWS',
 'Upwind': 'Security',
 'Vader': 'Applications',
 'Arc': 'Applications',
 'Clarity': 'Applications',
 'Bifrost': 'Applications',
 'RAD': 'Applications',
 'Studio Console': 'Applications',
 'AdTech Insights': 'Applications',
 'ads.txt': 'Applications'}


# Display order of the groups (darkstar's Intake page shows them in this order).
GROUP_ORDER: list[str] = ["AWS", "GCP", "Security", "Applications", "Other"]

@lru_cache(maxsize=1)
def _compiled() -> list[tuple[str, list[re.Pattern]]]:
    return [(d, [re.compile(p, re.I) for p in ps]) for d, ps in DOMAIN_PATTERNS.items()]


def tag(text: str) -> list[str]:
    """Every domain whose patterns match `text`."""
    text = text or ""
    return [d for d, regexes in _compiled() if any(r.search(text) for r in regexes)]


def ticket_text(row) -> str:
    """What a ticket is tagged from: title, description (data/build_dataframe_new keeps a cleaned copy without
    links) and human comments. Every page that tags tickets uses this, so they agree on a ticket's domains."""
    comments = row.get("comments")
    bodies = " ".join(c.get("body", "") for c in comments if isinstance(c, dict)) if isinstance(comments, list) else ""
    description = row.get("description_text")
    return f"{row.get('summary') or ''} {description if isinstance(description, str) else ''} {bodies}"


def primary(domains: list[str]) -> str | None:
    return next((d for d in DOMAIN_PRIORITY if d in domains), None)


def group_of(domain: str) -> str:
    return DOMAIN_GROUP.get(domain, "Other")
