# GitHub security configuration

The workflows and Dependabot configuration in this repository are versioned. The following controls live in GitHub repository settings and must be enabled by an owner after the new public repository is created.

## Code security and analysis

Open **Settings → Advanced Security** (or **Settings → Code security and analysis**) and enable:

- Dependency graph
- Dependabot alerts
- Dependabot security updates
- Secret scanning
- Push protection
- Private vulnerability reporting

Keep the checked-in `.github/workflows/codeql.yml` as the only CodeQL setup. If GitHub offers **CodeQL default setup**, do not enable both default and advanced setup for the same language.

## Ruleset for `main`

Create a branch ruleset targeting `main`:

- require a pull request before merging;
- require one approval, including a Code Owner review for matching paths;
- dismiss stale approvals when new commits are pushed;
- require conversation resolution;
- require the `Python 3.11`, `docker`, `audit`, `Analyze Python`, and `review` checks after their first successful run;
- block force pushes and branch deletion; and
- allow repository administrators to bypass only for documented incident recovery.

## Actions

Under **Settings → Actions → General**:

- allow actions used by this repository;
- set workflow permissions to **Read repository contents and packages permissions**;
- do not allow workflows to create or approve pull requests; and
- review every Dependabot pull request that changes a pinned Action SHA.

No production Feishu/Lark or model credential is needed by CI. Add no repository secret until a specific deployment workflow requires it, and scope any future environment secret to a protected GitHub Environment.

## First-run verification

After the initial push, wait for all workflows and then check:

1. the Python 3.11 test run is green;
2. the container starts and `/health` succeeds;
3. CodeQL uploads a Python database and reports no unresolved high-severity alert;
4. the supply-chain job uploads the SBOM and audit evidence;
5. Dependabot recognizes pip, Actions, and Docker; and
6. a synthetic secret in a throwaway branch is blocked by push protection, then removed before merge.
