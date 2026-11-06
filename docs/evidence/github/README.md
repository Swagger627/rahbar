# GitHub evidence screenshots (2026-11-06)

All taken in the Claude desktop app's built-in browser, signed in as the repository owner, while the repository was
PUBLIC and branch protection was enforced. Nothing was edited. On the settings pages, nothing was clicked except to
scroll.

| File | Shows |
| --- | --- |
| 01-branch-protection-rules.jpg | Settings → Branches: protection rules for `main` and `dev` |
| 02-main-rule-pr-and-1-approval.jpg | `main`: pull request required, 1 approval, stale approvals dismissed |
| 03-main-rule-status-checks-required.jpg | `main`: status checks required before merging |
| 04-main-rule-the-11-required-checks.jpg | the 11 required `ci.yml` checks |
| 05-main-rule-no-bypass-for-admins.jpg | conversation resolution, and "Do not allow bypassing the above settings" (admins included) |
| 06-pr1-merge-blocked-needs-approval.jpg | PR #1 (dev → main): "Merging is blocked - at least 1 approving review is required", merge button greyed |
| 07-demo-pr3-gate-failed-merge-blocked.jpg | demo PR #3: the `e2e-eval` gate failing (required), merge button greyed |
| 08-demo-pr3-fixed-all-checks-green.jpg | demo PR #3 after the fix: all 11 checks passed, merge possible (PR closed unmerged) |

See `../github-branch-protection.txt` and `../github-gate-demo.txt` for the matching command output.
