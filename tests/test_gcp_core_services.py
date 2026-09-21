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
