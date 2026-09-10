# Decisions

- 2026-09-11: Use database conflict handling for immutable extension membership and placeholder inserts, avoiding read-before-write races. Preserve the existing `added` response meaning (accepted nonblank input entries), rather than representing it as a new-row count.
