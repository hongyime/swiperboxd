# Decisions

- 2026-09-11: Use database conflict handling for immutable extension membership and placeholder inserts, avoiding read-before-write races. Preserve the existing `added` response meaning (accepted nonblank input entries), rather than representing it as a new-row count.
- 2026-09-11: Release b2d4124 passed all five hosted workflows and exact production deployment/public-route verification. Keep the post-release handoff notes for the next source commit to avoid an extra documentation-only Vercel build. The cryptography advisory remains a separate, uncompleted dependency follow-up.
- 2026-09-11: Update the cryptography pin and minimum to 50.0.1 for GHSA-g6cj-pr64-35w5. Keep key derivation and token serialization unchanged. Synthetic token decoding, tamper rejection and wrong-key rejection passed in both directions between 48.0.1 and 50.0.1, preserving a rollback path without inspecting real sessions.
