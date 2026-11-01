# [Docs/UX] Seed account web login fails with "password encrypt error" — root cause + drop-in reset script

## Summary

Fresh installs of the standalone image cannot log into the web UI with the seed
account (`admin/xxx` printed in the startup log): the page returns
`password encrypt error`. The REST API login works when the password is sent as the
protocol-encoded value, but the same plaintext works nowhere. This matches
issue #1905 and we'd like to contribute a root-cause note + a mitigation script.

## Root cause (as far as we can tell)

- The web login page encrypts the password with a **frontend-embedded public-key
  routine** whose output differs from what `POST /api/auth/user/login` expects.
- The seed row in `s2_user` stores a password hash produced by a **different
  protocol version**. Result: web-encrypted input can never validate against the
  stored value, while an API client that re-encodes with the currently expected
  scheme succeeds.

## Mitigation (zero source modification)

1. Login once via API with the protocol-encoded password to obtain a token.
2. Update the user row through the official user-update endpoint — writing a new
   password through the same protocol — after which **web login works again**.
   Alternatively, a direct metadata-DB update of `s2_user.password` with a value
   produced by the API-side encoder also restores web login.

We packaged this as `scripts/reset_web_login.mjs` (Node, uses only the official
endpoints) in our distribution; happy to upstream it under `scripts/` or turn it
into a docs note under the known-issues section.

## Ask

- Confirm whether the frontend encrypt routine and backend validator are expected to
  drift; if it's a known packaging artifact, a docs warning on the quick-start page
  would prevent most fresh-install frustration.
- We can open a PR with the reset script + a paragraph for the install docs.
