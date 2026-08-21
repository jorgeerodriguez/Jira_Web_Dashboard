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

# Self-service means a named workflow produced the merge request, and only these produce one.
# Everything else Claude touched is AI-ASSISTED, which is a different claim: it says the code was
# agent-written, not that a requester served themselves. Conflating them overstated the population
# by 2.1x -- 682 merge requests scored as self-service where 330 carry a workflow label, and 347 of
# the difference had no self-service label at all.
#
# Both tf-module spellings are matched because the two sides of the workflow disagree: the GitLab
# label in the data is `pe:tf-module` (14 merge requests) while the Jira watermark is
# `pe-tf-module-request`. Matching one spelling scores the other as zero.
SELF_SERVICE_LABELS: frozenset[str] = frozenset({
    "pe:iac-request",
    "pe:k8s-request",
    "pe:tf-module",
    "pe:tf-module-request",
})

# The workflow names, without the label prefix -- the footer spells them the same way the labels do.
SELF_SERVICE_WORKFLOWS: frozenset[str] = frozenset(
    label.removeprefix("pe:") for label in SELF_SERVICE_LABELS)

# Kept here rather than in slas.py because mrflow needs it too and slas already imports mrflow --
# putting it there would be a cycle.
_AGENT_FOOTER_RE = re.compile(
    r"generated\s+with\s+\[?claude\s+code|authored-by:\s*claude|claude\.com/claude-code",
    re.IGNORECASE,
)
# The whole footer line, not just the phrase that identifies it. Matching only the phrase truncates
# the part that matters: real footers read
#   "Generated with Claude Code via /iac-request · Install the PE plugin: ..."
# so the workflow is AFTER the bit the detector keys on.
_FOOTER_LINE_RE = re.compile(r"generated\s+with\s+\[?claude\s+code[^\n]*", re.IGNORECASE)
# `via /<workflow>`, optionally plugin-qualified as `via /<plugin>:<workflow>`. Both shapes are in
# the corpus: 267 plain and 145 qualified.
_VIA_WORKFLOW_RE = re.compile(
    r"\bvia\s+/(?:[a-z0-9][a-z0-9._-]*:)?([a-z0-9][a-z0-9._-]*)", re.IGNORECASE)


def has_agent_footer(description: str | None) -> bool:
    """True iff an MR description carries the skills' "Generated with Claude Code" footer."""
    return bool(_AGENT_FOOTER_RE.search(description or ""))


def footer_workflow(description: str | None) -> str | None:
    """The workflow a Claude footer names, or None if it names none.

    Measured over the corpus: 348 footers name /iac-request, 52 /troubleshoot, 7 /k8s-request,
    6 /tf-module-request, and 135 name nothing at all. So the footer is not one signal but two --
    which workflow ran, and whether one ran at all -- and only the first says "self-service".
    """
    line = _FOOTER_LINE_RE.search(description or "")
    if line is None:
        return None
    named = _VIA_WORKFLOW_RE.search(line.group(0))
    return named.group(1).lower() if named else None


def has_skill_label(labels: list[str] | None) -> bool:
    """True iff a label names one of the self-service workflows.

    Deliberately a fixed list, not the `pe:`-prefix test this used to be. That prefix mirrors the
    iac-request-labels.sh hook, which accepts any `pe:` label -- so it also admitted `pe:troubleshoot`
    and `pe:skill-introspective`, neither of which is a self-service request.
    """
    return any(str(label) in SELF_SERVICE_LABELS for label in (labels or []))


def is_self_service_mr(description: str | None, labels: list[str] | None) -> bool:
    """Whether a merge request came out of a self-service workflow.

    Two signals, both of which must NAME a workflow: a `pe:<workflow>` label, or a footer reading
    `via /<workflow>`. Either is evidence a requester served themselves.

    What does not count is a footer that names no workflow, or names one that is not self-service.
    `Generated with Claude Code` on its own says an agent wrote the code for someone already in the
    codebase, and `via /troubleshoot` says an agent helped debug -- neither is a request anybody
    filed. Admitting any agent footer overstated this population 2.1x.
    """
    return has_skill_label(labels) or footer_workflow(description) in SELF_SERVICE_WORKFLOWS


def is_ai_assisted_mr(description: str | None, labels: list[str] | None) -> bool:
    """Agent-written, but NOT through a self-service workflow.

    Mutually exclusive with is_self_service_mr, so the two can be reported side by side without
    double counting. This is the honest home for a footer that names no self-service workflow: work
    an agent wrote for someone already in the codebase, which is worth measuring and is not adoption.
    """
    return has_agent_footer(description) and not is_self_service_mr(description, labels)
