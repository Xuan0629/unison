# P0 Liveness Proof Table
# Two-pass validation: parse all lines first, then check membership

| Condition | Input (stdout) | Actual | Expected | Pass |
|-----------|----------------|--------|----------|------|
| ps OSError | (exception) | unknown | unknown | ✅ |
| ps TimeoutExpired | (exception) | unknown | unknown | ✅ |
| ps nonzero exit | rc=1 | unknown | unknown | ✅ |
| empty output | "" | unknown | unknown | ✅ |
| whitespace only | "  
  " | unknown | unknown | ✅ |
| garbage output | "GARBAGE
" | unknown | unknown | ✅ |
| match before garbage | "9999
not-a-pgid
" | unknown | unknown | ✅ |
| garbage before match | "not-a-pgid
9999
" | unknown | unknown | ✅ |
| valid no-match | "1000
2000
3000
" | dead | dead | ✅ |
| valid match | "1000
9999
3000
" | live | live | ✅ |
| only matching pgid | "9999
" | live | live | ✅ |

## Key: live and unknown BOTH block resume (fail-closed).
## Only verified-empty ps (all parseable, no match) returns dead.