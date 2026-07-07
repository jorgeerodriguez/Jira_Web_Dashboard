"""Tag a merge request to expertise domains from its repo path and changed file paths.

File paths are a far denser, more standardized domain signal than Jira ticket titles:
`.../eks-nodegroups/.../terragrunt.hcl` is unambiguously Kubernetes + Terraform, where the
ticket title ("fix ng min size") tags to nothing. Patterns map to the same domain names the
dashboard uses for the Jira corpus, so the two signals merge into one SME matrix.

Attribution is per-MR: each domain an MR touches counts once for its author, so a single large
refactor cannot dominate the signal.
"""
from __future__ import annotations

import re

# Domain -> case-insensitive regex over "<project_path>\n<changed paths>". Names match the
# dashboard's DOMAIN_PATTERNS keys so GitLab and Jira signals land in the same buckets.
_DOMAIN_PATTERNS: dict[str, str] = {
    "Kubernetes/GitOps": r"clusters/|namespaces/|helmrelease|kustomization|/helm/|\bk8s\b|karpenter|nodepool|nodeclass|kube-system|argocd|/flux|gitrepository|daemonset|statefulset|\bcrds?\b",
    "Terraform/Terragrunt": r"terragrunt\.hcl|\.tf$|\.tftpl|\.tfvars|/tf-|terraform|\.hcl$",
    "AWS Core": r"\baws\b|us-east-1|us-west-2|eu-west-1|\bec2\b|\bs3\b|cloudwatch|lambda|\becr\b|\brds\b|dynamodb|\bsqs\b|\bsns\b|cloudfront",
    "GCP Core": r"/gcp/|prj-|project-factory|landing.?zone|/folders?/|cloud-?run|/projects?/",
    "BigQuery/Data": r"bigquery|/bq/|\.sql$|dataflow|dataproc|looker|\bedp\b",
    "Grafana": r"grafana|dashboards?/|prometheus|\bloki\b|\btempo\b|alerting|servicemonitor|scrape",
    "GitLab": r"\.gitlab-ci|/\.gitlab/|(^|/)ci/|\bpipeline",
    "IAM/RBAC": r"\biam\b|/rbac|service-?account|workload-?identity|/roles?/|policies?/|clusterrole|\bsso\b|okta|tf-org\b",
    "Networking": r"\bvpc\b|subnet|/dns|networking|firewall|/network/|ingress|egress|peering|cloudflare",
    "Secrets/Vault": r"\bvault\b|/secrets?/|\bsops\b|sealed-?secret|external-?secret",
    "Databases": r"cloud-?sql|alloydb|\brds\b|postgres|mysql|\bredis\b|memorystore|/database",
    "Composer": r"composer",
    "Airflow": r"airflow|/dags?/",
    "Cost/FinOps": r"\bcogs\b|billing|finops|\bbudget",
    "Storage Transfer": r"storage-?transfer|\bsts\b",
    "VDI/WorkSpaces": r"workspace|\bvdi\b|gcve|vsphere|citrix",
    "AI Plugins": r"audacy-ai-plugins",
    "AWS Bedrock Agents": r"pe-agent|claude-sdk-pe-agent|bedrock",
    # specialized services pulled out of the core buckets (per Adam) — not everyday skills.
    "EKS": r"\beks\b|eks-node|eks-cluster",
    "ECS": r"\becs\b|fargate",
    "OpenSearch": r"opensearch|elasticsearch",
    "MSK": r"\bmsk\b|kafka",
    "GKE": r"\bgke\b",
    "VertexAI": r"vertex[\s_-]?ai|vertexai|aiplatform|\bvertex\b",
    "Kubeflow Pipelines": r"kubeflow|\bkfp\b",
    "Route53": r"tf-sharedservices|route\s?53|\br53\b",
}

_COMPILED: dict[str, re.Pattern[str]] = {
    domain: re.compile(pattern, re.IGNORECASE) for domain, pattern in _DOMAIN_PATTERNS.items()
}


def domains_for(project_path: str, paths: list[str]) -> set[str]:
    """The set of domains an MR touches, matched over its repo path and changed file paths."""
    haystack = project_path + "\n" + "\n".join(paths)
    return {domain for domain, pattern in _COMPILED.items() if pattern.search(haystack)}
