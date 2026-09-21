"""GCP Core recognises named GCP services, not just the `/gcp/` path segment.

Measured over PE's merged MRs (1,534 across 190 repos, trailing 6 months): the taxonomy is
AWS-shaped. Every AWS service resolves — s3, ecr, sqs, sns, lambda, cloudwatch, kms, codebuild,
dynamodb — and almost no GCP counterpart did. That is a large part of why 42% of PE's merges
resolve no further than "does Terraform" or "does Kubernetes": the GCP half of the estate had
nothing to resolve to, and GCP is where the volume now is.

These five are deliberately folded into **GCP Core** rather than given their own domains. They add
no tag to a `tf-gcp-*` repo today — the `/gcp/` segment already matches — so the value is that the
mapping becomes intentional rather than an accident of where repos happen to live, and a GCP
service provisioned from outside that tree is still recognised.
"""
import pytest

from darkstar import gitlab_domains

_GCP_SERVICES = ["gcs", "artifact-registry", "pubsub", "cloud-logging", "cloud-kms"]
# AWS estates that really do have a kms/ unit, found by sweeping their repo trees.
_AWS_ESTATES = ["audacy-inc/devops/terraform/tf-coreservices",
                "audacy-inc/devops/terraform/tf-aardvark2-prod",
                "audacy-inc/devops/terraform/tf-amperwave-nonprod"]


def _domains(repo, unit):
    return gitlab_domains.domains_for(repo, [f"{repo}/us-east4/prod/{unit}/terragrunt.hcl"])


@pytest.mark.parametrize("service", _GCP_SERVICES)
def test_a_named_gcp_service_is_gcp_core_wherever_it_lives(service):
    """Recognised by name, so the tag does not depend on the repo sitting under /gcp/."""
    assert "GCP Core" in _domains("audacy-inc/devops/terraform/tf-elsewhere", service)


@pytest.mark.parametrize("repo", _AWS_ESTATES)
def test_aws_kms_is_never_relabelled_as_gcp(repo):
    """The trap this pattern was written around, and the reason it is `cloud-?kms`.

    A bare `\\bkms\\b` looks like the obvious pattern and is wrong: these three AWS estates each
    carry a kms/ unit, and matching it would move real AWS work into the GCP bucket — quietly
    making whoever does it look like a GCP SME.
    """
    assert "GCP Core" not in _domains(repo, "kms")


def test_the_gcp_path_segment_still_wins_on_its_own():
    """The existing behaviour these additions must not disturb: a tf-gcp-* repo is GCP Core
    whatever its units are called, which is why this change adds no tags in practice."""
    repo = "audacy-inc/gcp/devops/tf-gcp-ai-traffic-prod"
    assert "GCP Core" in gitlab_domains.domains_for(repo, [])


def test_the_client_tagger_knows_the_same_services():
    """The two taggers must share domain NAMES or the signals land in different buckets.

    `gitlab_domains` reads changed file paths; the dashboard's DOMAIN_PATTERNS reads Jira titles.
    They had already drifted — `firestore` and `secret-manager` are in the client list and absent
    from the server one — so this pins the pair for the services added here.
    """
    import json
    import re
    from pathlib import Path
    page = (Path(__file__).resolve().parents[1]
            / "darkstar" / "dashboards" / "intake.html").read_text(encoding="utf-8")
    client = json.loads(re.search(r"const DOMAIN_PATTERNS = (\{.*?\});", page, re.S).group(1))
    patterns = client["GCP Core"]
    for term in ("gcs", "artifact", "pub", "logging", "kms"):
        assert any(term in p.lower() for p in patterns), f"client GCP Core is missing {term!r}"
    assert not any(re.fullmatch(r"\\\\bkms\\\\b", p) for p in patterns), \
        "a bare kms pattern would sweep AWS KMS into GCP Core on the Jira side too"


# --- domains added because the work is growing, not because it has volume yet --------------------
# Firebase, Firestore, Looker and GCP Agent Platform are called out on Adam's direction: all four
# are expected to grow. A domain with no history is hidden by the matrix, so an empty row costs
# nothing, where a MISSING domain files the work as generic GCP until somebody notices.

_NEW = {
    "GCP Agent Platform": ["tf-gcp-agent-platform", "agentspace", "agent-space"],
    "Firestore": ["tf-gcp-firestore", "firestore"],
    "Firebase": ["tf-gcp-firebase", "firebase"],
    "Looker": ["tf-gcp-looker-core", "looker"],
}


@pytest.mark.parametrize("domain,samples", list(_NEW.items()))
def test_each_new_domain_matches_its_own_work(domain, samples):
    for s in samples:
        assert domain in _domains("audacy-inc/devops/terraform/modules", s), f"{s} -> {domain}"


def test_firestore_and_looker_left_the_buckets_they_were_folded_into():
    """Pulled out the way EKS came out of Kubernetes/GitOps. Leaving them in both would double-count
    the same work and let the coarse bucket keep claiming the specialist."""
    assert "Databases" not in _domains("x", "firestore")
    assert "BigQuery/Data" not in _domains("x", "looker")


# --- agentcore is bedrock -------------------------------------------------------------------------

@pytest.mark.parametrize("repo", [
    "audacy-inc/devops/terraform/modules/tf-aws-agentcore/runtime",
    "audacy-inc/devops/terraform/modules/tf-aws-devops-agent",
    "audacy-inc/devops/agentcore/pe-agent",
])
def test_agentcore_work_lands_in_aws_bedrock_agents(repo):
    """Same competency, so the same domain (Adam's call). 33 MRs across the tf-aws-agentcore
    submodules alone read as plain AWS Core before this."""
    assert "AWS Bedrock Agents" in gitlab_domains.domains_for(repo, [])


@pytest.mark.parametrize("repo", [
    "audacy-inc/devops/terraform/modules/tf-gcp-sts-agent-pool",
    "audacy-inc/devops/terraform/modules/tf-aws-datasync-agent",
    "audacy-inc/devops/terraform/modules/tf-gcp-service-agents",
])
def test_unrelated_agents_are_not_swept_into_an_agent_domain(repo):
    """Why the patterns name `agentcore`/`devops-agent`/`agent-platform` and never a bare `agent`.

    A storage-transfer agent pool, a DataSync agent and GCP service agents have nothing to do with
    agentic AI, and matching them would credit that expertise to whoever wired up a transfer job.
    """
    found = gitlab_domains.domains_for(repo, [])
    assert "AWS Bedrock Agents" not in found and "GCP Agent Platform" not in found


# --- the tf-gcp-* modules ------------------------------------------------------------------------
# These 49 repos were the largest gap the sweep found, and the first attempt fixed them the wrong
# way: a `tf-gcp-` pattern pulled them into GCP Core. They are module repos, so they belong to
# Terraform Modules instead — the cloud in the name is what the module targets, not what the
# author was operating.

def test_gcp_modules_are_module_work_not_gcp_work():
    for repo in ("audacy-inc/devops/terraform/modules/tf-gcp-project",
                 "audacy-inc/devops/terraform/modules/tf-gcp-organization",
                 "audacy-inc/devops/terraform/modules/tf-gcp-eventarc"):
        found = gitlab_domains.domains_for(repo, [])
        assert found == {gitlab_domains.MODULES_DOMAIN}, found


def test_the_gcp_service_names_do_not_drag_modules_into_gcp_core():
    """tf-gcp-kms and tf-gcp-logging match the GCP Core service patterns on their names; the
    modules rule has to win, or the cloud bucket creeps back in through the side door."""
    for repo in ("audacy-inc/devops/terraform/modules/tf-gcp-kms",
                 "audacy-inc/devops/terraform/modules/tf-gcp-logging"):
        assert "GCP Core" not in gitlab_domains.domains_for(repo, [])


# --- the two taggers must agree on names ----------------------------------------------------------

def test_every_server_domain_exists_on_the_client_too():
    """The two lists are supposed to share names so the path signal and the title signal land in
    one bucket. They had already drifted, and nothing checked it — so this checks it."""
    import json
    import re
    from pathlib import Path
    page = (Path(__file__).resolve().parents[1]
            / "darkstar" / "dashboards" / "intake.html").read_text(encoding="utf-8")
    client = set(json.loads(re.search(r"const DOMAIN_PATTERNS = (\{.*?\});", page, re.S).group(1)))
    priority = set(json.loads(re.search(r"const DOMAIN_PRIORITY = (\[.*?\]);", page, re.S).group(1)))
    server = set(gitlab_domains._DOMAIN_PATTERNS)

    assert not server - client, f"domains the client cannot name: {server - client}"
    assert not client - priority, f"domains missing from DOMAIN_PRIORITY: {client - priority}"


# --- module authoring is its own competency ------------------------------------------------------
# Per Adam: anything under devops/terraform/modules/ is raw Terraform, one domain, whatever cloud
# the module targets. Writing tf-gcp-firestore is writing reusable HCL — variables, validation,
# examples, a release — not running Firestore in production, and it is frequently a different person.

_MODULES = "audacy-inc/devops/terraform/modules"


def test_a_module_repo_is_terraform_modules():
    assert gitlab_domains.MODULES_DOMAIN in gitlab_domains.domains_for(f"{_MODULES}/tf-gcp-project", [])


@pytest.mark.parametrize("repo,cloud", [("tf-gcp-project", "GCP Core"),
                                        ("tf-aws-datasync-agent", "AWS Core")])
def test_a_module_repo_loses_its_cloud_bucket(repo, cloud):
    """The point of the domain: module skill does not depend on which cloud the module targets."""
    assert cloud not in gitlab_domains.domains_for(f"{_MODULES}/{repo}", [])


def test_a_module_repo_does_not_also_claim_plain_terraform():
    """Terraform Modules is the more specific claim; keeping both says nothing extra."""
    assert "Terraform/Terragrunt" not in gitlab_domains.domains_for(f"{_MODULES}/tf-gcp-project", [])


@pytest.mark.parametrize("repo,kept", [
    ("tf-gcp-looker-core", "Looker"),
    ("tf-gcp-firestore", "Firestore"),
    ("tf-gcp-agent-platform", "GCP Agent Platform"),
    ("tf-aws-agentcore/runtime", "AWS Bedrock Agents"),
])
def test_a_module_repo_keeps_its_service_domain(repo, kept):
    """Service domains survive the suppression, and they have to.

    Every repo behind Looker, Firestore and GCP Agent Platform is a module — suppressing these
    would leave all three domains with no evidence at all, days after they were created.
    """
    found = gitlab_domains.domains_for(f"{_MODULES}/{repo}", [])
    assert kept in found and gitlab_domains.MODULES_DOMAIN in found


def test_an_estate_repo_is_untouched_by_the_modules_rule():
    """tf-coreservices and the tf-gcp-* estate repos are operations, not module authoring."""
    assert gitlab_domains.MODULES_DOMAIN not in gitlab_domains.domains_for(
        "audacy-inc/devops/terraform/tf-coreservices", [])
    gcp = gitlab_domains.domains_for("audacy-inc/gcp/devops/tf-gcp-ai-traffic-prod", [])
    assert "GCP Core" in gcp and gitlab_domains.MODULES_DOMAIN not in gcp
