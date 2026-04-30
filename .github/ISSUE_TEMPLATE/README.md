# Issue & PR Templates

GitHub issue and pull request templates that standardize how contributors report bugs, request features, and submit changes.

## Table of Contents

- [Files](#files)
- [How It Works](#how-it-works)
- [Modifying Templates](#modifying-templates)

## Files

| File | Description |
|------|-------------|
| `bug_report.md` | Bug report template — environment details, reproduction steps, expected vs actual behavior |
| `feature_request.md` | Feature request template — problem description, proposed solution, alternatives considered |
| `config.yml` | Issue chooser configuration — controls the "New Issue" page layout and adds contact/discussion links |

The pull request template lives one level up at `.github/pull_request_template.md` (GitHub requires this location).

## How It Works

When a user clicks "New Issue" on GitHub, they see the templates defined here. The `config.yml` file controls whether blank issues are allowed and adds external links (e.g. to discussions or documentation).

Pull request descriptions are pre-filled with `.github/pull_request_template.md` automatically.

## Modifying Templates

Edit the Markdown files directly. GitHub renders them as forms when creating new issues. The `config.yml` uses GitHub's [issue template chooser](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/configuring-issue-templates-for-your-repository) format.
