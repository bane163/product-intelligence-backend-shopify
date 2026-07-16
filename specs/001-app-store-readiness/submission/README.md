# Submission Workspace

Use this directory to collect the reviewer-facing evidence required for Shopify
App Store submission and internal dry runs.

## Expected artifacts

- `review-environment.md` — review store, deployment, and environment notes
- `reviewer-access.md` — reviewer access instructions and secure credential handoff references
- `reviewer-dry-run.md` — end-to-end reviewer journey results and timings
- `listing-copy.md` — App Store listing text and pricing summary
- `screencast-script.md` — screencast walkthrough script
- `screenshot-shotlist.md` — screenshot plan and capture checklist
- `submission-checklist.md` — final submission verification checklist

## Evidence status

- Backend readiness evidence: automated checks recorded in `reviewer-dry-run.md`
- Frontend readiness evidence: automated checks recorded in `reviewer-dry-run.md`
- Reviewer dry-run evidence: template created; live store timing/results still required
- Listing and media evidence: draft artifacts created; final screenshots/video capture still required
- Support and emergency contact evidence: checklist and placeholders created; owner details still required

## Submission rule

Any placeholder labeled `REPLACE_BEFORE_SUBMISSION` is a release blocker for the
Shopify review package.

Audit command:

```bash
cd /Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend
rg -n "REPLACE_BEFORE_SUBMISSION" specs/001-app-store-readiness/submission
```
