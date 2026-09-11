# Source history and release hygiene

The repository had no commits before this review. Commit `ddeb51c` preserves the
existing implementation as the first local baseline, after excluding build/state
artifacts and running the source secret scanner. Later changes should be separate,
reviewable commits. No remote is configured; local commits are not an off-device
backup. Choose a private remote before relying on this machine for durable history.

Keep virtual environments, derived data, runtime databases, credentials, personal
content and verification output outside source archives. Retain source and synthetic
fixtures in Git. Generate a review archive from a committed revision:

```bash
git archive --format=zip --output=/tmp/lifeos-source-review.zip HEAD
```

Use a distinct output name to preserve a previous archive. The archive contains
committed source only; send verification reports separately after reviewing content.
It is not an installable application or a copy of runtime data.

CI actions are pinned to full commit SHAs resolved from their official repositories;
checkout does not persist Git credentials, and Dependabot proposes action updates.
Follow [GitHub's immutable action guidance](https://docs.github.com/en/actions/reference/security/secure-use).
Pinning reduces mutable-reference risk; it does not replace code review, dependency
scanning, provenance verification or signing release artifacts.
