# Submission Checklist

## Listing and metadata

- [ ] App name is `Stockpile` everywhere customer-facing
- [ ] `extractor-v3/shopify.app.toml` matches the listing name and deployed URL
- [ ] App icon is current and uploaded
- [ ] Listing copy matches `listing-copy.md`
- [ ] Pricing in the listing matches `extractor-v3/app/config/billing.ts`
- [ ] Privacy policy and support URLs are current

## Reviewer package

- [ ] `review-environment.md` contains the deployed frontend URL, backend URL, review store domain, billing mode, and sample file path
- [ ] `reviewer-access.md` contains valid reviewer access instructions, secure credential handoff references, and contact data
- [ ] `reviewer-dry-run.md` contains current automated results and manual timing evidence
- [ ] `screencast-script.md` matches the live reviewer journey
- [ ] `screenshot-shotlist.md` has been captured and delivered

## Functional confidence

- [ ] Reviewer install path lands in the embedded app without manual store entry
- [ ] Documents upload -> extraction -> review -> product creation flow works end to end
- [ ] Billing selection and return states are understandable
- [ ] Billing & Usage page does not silently show fake usage when billing data is unavailable
- [ ] Recovery states explain what the reviewer should do next

## Support and operations

- [ ] Support owner is current
- [ ] Emergency contact is current
- [ ] Rollback owner and trigger criteria are documented
- [ ] Release artifact includes the latest verification command evidence

## Release blockers

- [ ] No `REPLACE_BEFORE_SUBMISSION` placeholder remains anywhere in `specs/001-app-store-readiness/submission/`
- [ ] `cd /Users/freemansenecharles/developer/server/shopify_extractor/shopify_supabase_backend && rg -n "REPLACE_BEFORE_SUBMISSION" specs/001-app-store-readiness/submission` returns no matches
- [ ] No screenshot, screencast, or credential reference points to stale data
- [ ] No reviewer passwords, backup codes, or one-time codes are committed in tracked files
- [ ] All required verification commands have current passing evidence
