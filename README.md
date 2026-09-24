# release

[![pre-commit.ci status](https://results.pre-commit.ci/badge/github/conda-forge/release/main.svg)](https://results.pre-commit.ci/latest/github/conda-forge/release/main) [![tests](https://github.com/conda-forge/release/actions/workflows/tests.yml/badge.svg)](https://github.com/conda-forge/release/actions/workflows/tests.yml)

GitHub Action to update the version of a feedstock, with a GitLab CI/CD job template that does the same thing.

## How it works

The job proves which CI job it is with the identity token its provider issues it, and conda-forge opens the version update pull request itself, as the `@conda-forge-admin, please update version` command would. Nothing secret is stored anywhere. This is known as trusted publishing.

First, list the job in the feedstock's `conda-forge.yml`, on the branch it will update:

```yaml
trusted_publishers:
  # a GitHub Actions workflow
  - provider: github
    repository: <owner>/<repository>
    # curl -s https://api.github.com/users/<owner> | jq .id
    repository_owner_id: <id>
    workflow: update-feedstock-version.yml
    environment: conda-forge
  # a GitLab CI/CD job
  - provider: gitlab
    # only for a self-managed instance conda-forge accepts tokens from
    url: https://gitlab.cern.ch
    project_path: <group>/<project>
    # curl -s https://gitlab.cern.ch/api/v4/groups/<group> | jq .id
    namespace_id: <id>
    ref_type: tag
    ref_protected: true
```

## Usage on GitHub Actions

Add a workflow, here `.github/workflows/update-feedstock-version.yml`, and give the job `id-token: write`:

```yaml
name: update feedstock version
on:
  workflow_dispatch:
    inputs:
      version:
        description: 'the new version'
        required: true
        type: string

jobs:
  update-feedstock-version:
    runs-on: ubuntu-latest
    environment: conda-forge
    permissions:
      id-token: write
    steps:
      - uses: conda-forge/release@main
        with:
          feedstock: <name of feedstock>-feedstock
          version: ${{ inputs.version }}
```

Then you can trigger the version update by dispatching the workflow in the UI. It is also possible to trigger the workflow on GitHub release events. See the [action.yml](action.yml) for details on possible inputs and options.

Give the `conda-forge` environment a deployment branch or tag policy listing what you release from, and name it in the feedstock's entry, so that a workflow run from any other branch cannot publish.

## Usage on GitLab CI/CD

Include the job template, pinning the URL and the `ref` input to the same tag:

```yaml
stages:
  - deploy

include:
  - remote: https://raw.githubusercontent.com/conda-forge/release/<tag>/gitlab/trusted-publish.yml
    inputs:
      feedstock: <name of feedstock>-feedstock
      ref: <tag>
```

That adds an `update-feedstock-version` job which runs on tag pipelines and takes the new version from `$CI_COMMIT_TAG`, with any leading `v` stripped. Set the `version` input to override that, and the `rules` input to run the job at some other time. See [gitlab/trusted-publish.yml](gitlab/trusted-publish.yml) for the full list of inputs.

Release from protected tags and set `ref_type: tag` and `ref_protected: true` on the feedstock's entry, so that only a release can publish.

## Limits

- conda-forge accepts tokens from GitHub Actions, gitlab.com and the GitLab instances listed in [conda-forge-webservices](https://github.com/conda-forge/conda-forge-webservices/blob/main/conda_forge_webservices/trusted_issuers.yaml).
- A token is accepted once, and for no more than 15 minutes after it is issued. GitLab issues one per job, as the job starts, so the template's job installs nothing and sends it straight away; a job that fails after its request was accepted has to be run again.
- Anything that runs earlier in the same job can read the token, so keep the job that sends it small.
- One update per feedstock is opened a minute.
- `automerge` and `branch` need a personal access token, since with trusted publishing conda-forge opens the pull request.

<details>
<summary>Using a personal access token instead</summary>

The action can also open the pull request itself with a GitHub personal access token, which is what `automerge` and `branch` need. Trusted publishing is the better choice otherwise: a token has to be stored, can be leaked, and has to be renewed.

### Usage

Pass the token to `github-token`:

```yaml
name: update feedstock version
on:
  workflow_dispatch:
    inputs:
      version:
        description: 'the new version'
        required: true
        type: string

jobs:
  update-feedstock-version:
    runs-on: ubuntu-latest
    name: update-feedstock-version
    steps:
      - name: run
        uses: conda-forge/release@main
        with:
          feedstock: <name of feedstock>-feedstock
          version: ${{ inputs.version }}
          github-token: ${{ secrets.GITHUB_PAT }}
          automerge: true
```

### Required Token Permissions and Scopes

#### Classic Tokens

For classic tokens, you need read/write permissions for the the `repo` and `workflow` scopes. For classic tokens, you pass the token to the `github-token` input.

#### Fine-grained Tokens

For fine-grained tokens, you need to generate two tokens with different scopes and pass them to different inputs. You also need to have an existing fork of the target feedstock. The token permissions are as follows:

| Action Input Parameter  | Allowed Repositories         | Repository Scopes (permissions)               |
| ----------------------- | ---------------------------- | --------------------------------------------- |
| `github-token`          | upstream feedstock           | pull_request (read/write)                     |
| `github-token-for-fork` | your fork of the feedstock   | contents (read/write), workflows (read/write) |

Give both tokens an expiry of less than 365 days. conda-forge caps the lifetime of fine-grained tokens, and while only `github-token` names a conda-forge repository, the cap ends up being applied to `github-token-for-fork` as well.

### Protecting the Token

The token given to this action can open pull requests on the feedstock and, with `automerge: true`, add the `automerge` label to them. The conda-forge automerge service acts on that label regardless of who opened the pull request, so the token is better thought of as one that can land code in the package than one that can only propose changes.

Stored as a plain repository secret, it can be read by any workflow on any branch of the repository holding it. Storing it in a [GitHub environment](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments) instead, and naming that environment on the job, is a better default:

```yaml
jobs:
  update-feedstock-version:
    runs-on: ubuntu-latest
    name: update-feedstock-version
    environment: conda-forge
    steps:
      - name: run
        uses: conda-forge/release@main
        with:
          feedstock: <name of feedstock>-feedstock
          version: ${{ inputs.version }}
          github-token: ${{ secrets.GITHUB_PAT }}
          automerge: true
```

Give the environment a deployment branch policy listing the branches you release from, so that workflows on any other branch cannot read the secret. Required reviewers can be added as well, but note that the approval gates the whole job before it starts: a reviewer is approving the update being made at all, rather than reviewing the pull request it produces.

</details>

## Versioning and Deprecation Policy

This action follows [CalVer](https://calver.org/) with the format `YYYY.MM.DD`. Version tags are preceded by the letter `v`. The action's behavior, inputs, and outputs have a 60-day deprecation policy.
