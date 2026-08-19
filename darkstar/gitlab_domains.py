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
    "Fastly": r"fastly",  # 3rd-party CDN (distinct from AWS CloudFront) — specialized, called out on its own
}

_COMPILED: dict[str, re.Pattern[str]] = {
    domain: re.compile(pattern, re.IGNORECASE) for domain, pattern in _DOMAIN_PATTERNS.items()
}


def domains_for(project_path: str, paths: list[str]) -> set[str]:
    """The set of domains an MR touches, matched over its repo path and changed file paths."""
    haystack = project_path + "\n" + "\n".join(paths)
    return {domain for domain, pattern in _COMPILED.items() if pattern.search(haystack)}


# -- Deployment environment, read from the repo name ------------------------------------------
# Audacy names infrastructure repos by environment: tf-aardvark2-prod, tf-amperwave-nonprod. The
# check must be ordered and token-based, not a substring test, because "nonprod" *contains*
# "prod" -- a naive `"prod" in name` marks every nonprod repo as production. Splitting on the
# separators the names actually use also keeps "reporting" or "product" from matching.
PRODUCTION: str = "prod"
NONPRODUCTION: str = "nonprod"
OTHER_ENVIRONMENT: str = "other"
MIXED_ENVIRONMENT: str = "mixed"

_SEPARATORS = re.compile(r"[-_./]")


def _tokens(text: str) -> set[str]:
    return set(_SEPARATORS.split(text.lower()))


def environment_of(project_path: str, changed_paths: list[str]) -> str:
    """"prod", "nonprod", "mixed" or "other" for a merge request.

    The repo name wins. A repo named tf-aardvark2-prod deploys to production whatever directory a
    change happens to sit in, so the name is the stronger claim and paths are consulted only when
    it says nothing.

    Paths matter because several repos hold both environments — gitops-k8s-team-a2 keeps
    clusters/prod-fluxv2/namespaces/app/prod/... alongside its nonprod tree, so the repo name is
    silent while the change is unambiguously production. On the crawled corpus this classifies 192
    of the 955 otherwise-unknown MRs.

    An MR touching BOTH trees is "mixed", not one or the other: that is an unusual change spanning
    environments, and folding it into either bucket would misreport that bucket. It is excluded
    from the environment views and counted separately rather than dropped in silence.

    The check is ordered and token-based, never a substring test, because "nonprod" contains
    "prod" — a naive `"prod" in name` marks every non-production repo as production.
    """
    repo = _tokens(project_path.rsplit("/", 1)[-1])
    if NONPRODUCTION in repo:
        return NONPRODUCTION
    if PRODUCTION in repo:
        return PRODUCTION

    touched: set[str] = set()
    for path in changed_paths:
        touched |= _tokens(path)
    in_nonprod, in_prod = NONPRODUCTION in touched, PRODUCTION in touched
    if in_nonprod and in_prod:
        return MIXED_ENVIRONMENT
    if in_nonprod:
        return NONPRODUCTION
    if in_prod:
        return PRODUCTION
    return OTHER_ENVIRONMENT
