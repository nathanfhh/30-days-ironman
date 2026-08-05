# The verifier is not a ruler

Two PRs had their verification run twice — not by design, but because the agent
re-ran and overwrote its own output. The disagreement between the two passes is
the honest error bar on every corrected number in this report.

| PR | pass 1 (real / not_real / unclear) | pass 2 | claims that moved |
|---|---|---|---|
| `sentry-95633` | 31 / 6 / 2 | 30 / 7 / 2 | 1 of 39 (3%) |
| `discourse-graphite-1` | 18 / 4 / 3 | 17 / 2 / 6 | 4 of 25 (16%) |

The scored files hold pass 2 in both cases — last write wins, not a judgement
that it was better.

The `discourse-graphite-1` movement is worth reading, because it is not noise.
Both passes were asked whether `Tempfile#size` goes stale after an in-place
rewrite. Pass 1 said the claim was `not_real`: ImageOptim writes to a temp path
and renames, so a fresh `File.size` is correct. Pass 2 read the Ruby 3.3.6
stdlib, confirmed `Tempfile#size` fstats the live fd every call, reproduced both
the truncate case and the rename case — and then moved the claim to `unclear`,
because settling it needs to know which of the two ImageOptim 0.20.2 actually
does, and the gem is not installed.

So the second pass did strictly more work and became *less* certain. That is the
right direction for a verifier to move, and it is also a warning: a single-pass
verdict of `not_real` carries less weight than a single-pass verdict of `real`,
because `not_real` is the one you reach by stopping early.

What this means for the corrected scores: treat them as accurate to a few
percentage points, not to the decimal. The gap they close between raw precision
and corrected precision is far larger than this variance, which is why the
conclusion survives it — but any two tools within a few points of each other
after correction are tied, not ranked.

## A third re-run, and a larger swing

`keycloak-33832` also ran twice, and moved much further than the other two:

| pass | real | not_real | unclear |
|---|---|---|---|
| 1 | 17 | 4 | 2 |
| 2 | 21 | 0 | 2 |

Pass 1's four `not_real` verdicts each named a specific disproof — the null guard
the claim said was missing is at `CryptoIntegration.java:43-52`; `CryptoProvider`
has no `order()` in this tree; there is no `META-INF/services` file for it; the
`Time.currentTime()` call is not in this diff. Pass 2 did not revisit any of
them: it spent its effort compiling the two new ASN.1 classes against a local
JDK 21 and fuzzing the decoder, and reported its own distribution as "unusual".

The scored file holds pass 2, so four claims that pass 1 refuted with citations
are counted as `real`. That is last-write-wins, not a considered choice, and it
moves the corrected numbers slightly in *our* tool's favour on this PR. It is
recorded here rather than re-run to a preferred answer.

Taken together the three double-runs put the verifier's self-agreement at
roughly 88–97% per PR. Read the corrected tables with that in mind.
(Superseded: with all five double-runs recomputed, the range is 81–97%. See
the closing section.)

## A fourth double-run, in the 50-PR extension

`keycloak-36880` ran twice during the extension to all 50 PRs:

| pass | real | not_real | unclear |
|---|---|---|---|
| 1 | 25 | 4 | 2 |
| 2 | 27 | 4 | 0 |

The same 4 `not_real` verdicts in both passes, with the same reasoning — the
Dependabot-label claims, the `findByName` lookup, and the feature-flag guard.
What moved was the two `unclear`s: pass 1 left the V1-copy-divergence question
and the "resource with no type" model question open; pass 2 resolved both to
`real`. Nothing moved from `real` to `not_real` or back.

The scored file holds pass 2, again by last-write-wins rather than by choosing.

Across four double-runs the verifier's self-agreement is roughly 83-97% per PR,
and the disagreements cluster on the `unclear` boundary rather than on the
real/not_real one. That is the more reassuring failure mode: what a second look
changes is mostly how much it is willing to commit, not which way it commits.

## A fifth double-run, and the largest disagreement in the set

`keycloak-38446` ran twice:

| pass | real | not_real | unclear |
|---|---|---|---|
| 1 | 29 | 5 | 2 |
| 2 | 36 | 0 | 0 |

Every `not_real` and every `unclear` flipped. This is the widest gap of the five
double-runs, and it moves entirely in our tool's favour, so it needs stating
plainly rather than burying.

Pass 1's five refutations were specific and citable — `getSecretData()` cannot
return null because `buildCredentialModel` goes through `createFromValues` →
`setSecretData`; the `@Override` the claim says is missing is present at line 71;
`getType()` is inherited from `AbstractUserAdapter`; a claim describes a `List`
return where the signature says `Stream`; the new provider is *not* in
`META-INF/services`. Pass 2 does not address any of them. It reports "nothing in
the set turned out to be a hallucinated identifier or a mis-described guard",
which is a different claim from "I checked these five and pass 1 was wrong".

Pass 2 is the scored file, by last-write-wins. On the face of it pass 1's
verdicts on those five are better evidenced. The honest reading is that this PR's
numbers are the least reliable in the run, and that the corrected precision for
every tool on `keycloak-38446` is inflated by up to five claims.

Across the five PRs that ran twice, per-PR self-agreement is 81-97%:
`sentry-95633` 97%, `keycloak-36880` 94%, `discourse-graphite-1` 88%,
`keycloak-33832` 83%, `keycloak-38446` 81%.

Note what that does **not** show: by magnitude, `keycloak-38446` is not an
outlier — 81% against a next-lowest of 83%. What sets it apart is direction.
Every claim that moved went the same way, and that way favours the tool under
test. A verifier that moves a few claims in both directions is noisy; one that
moves seven claims all one way is showing a different failure. Treat the
corrected tables as accurate to a few points overall, and this slug's row as
the softest in the set.
