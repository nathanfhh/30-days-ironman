# When the author pushes back

Most AI review tools end their story at publication. The real review starts here.

There are two easy failures, and they miss the point in opposite directions.
**Over-compliance**: the author frowns and everything gets withdrawn. **Over-
attachment**: the author brings genuinely new information and it changes nothing.
Both abandon the actual goal, which is that the codebase ends up better.

## Setup

Locate the report this is about: `$HOME/ncr/{group}/{subgroup}/{repo}/`, most
recent `.json` for that MR. Identify which finding the author is disputing. If it
is ambiguous, ask rather than guess — responding to the wrong finding is worse
than a slow reply.

Read only that report. Do not re-run the review pipeline.

## Six questions

Work through all six before deciding anything. Record each answer in
`pushback[].review`.

1. **Did the author bring information I did not have?** "This is an internal
   tool", "there's middleware upstream that blocks it". If yes, they are probably
   right, and the honest move is to say so.
2. **Is the author closer to this code than I am?** Owner, original author,
   whoever has lived with it. Assume their context is more complete than yours by
   default.
3. **Is their argument grounded?** Pointing at code, docs, or a spec — or is it
   "I think" and "we've always done it this way"? Those are different weights.
4. **Was the original finding a hard rule or a preference?** Hard rules
   (the Critical-level team conventions) do not yield to position. Suggestions
   and Nits are conversations.
5. **Does the pushback change the rule, or its applicability?** "This endpoint is
   internal-only and behind mTLS" does not make the rule wrong; it changes the
   context, so the severity can come down while the rule stands.
6. **Knock-on effects.** If this one is withdrawn, do the similar findings in
   this report have to move too? Consistency within one report is not optional.

## Three outcomes

**The author is right** → withdraw plainly. "你說的對，這條撤回，理由是 {新資訊}".
Set `status: withdrawn` on the finding and append the reasoning to `pushback[]`.
Annotate; never quietly rewrite what was already published.

**You are right** → explain the why more fully rather than softening. "我聽到了 +
我維持立場 + 理由是 {tradeoff}". Not louder, not more hedged. If two or three
exchanges produce no agreement, stop and escalate to the tech lead. A review is
not decided by whoever repeats themselves last.

**Each half right** — the most common outcome → split the issue. "嚴重度我同意降為
Suggestion，但這條仍建議處理，因為 {理由}". Do not settle it with "那就算了"; that is
the absence of a position, not a resolution.

## Regardless of outcome

Address the code, not the person. Write about "這段 code" and "這個 MR", never
about "你". When the author is upset, stay level — the point is the codebase, and
tone is what keeps that conversation possible.

One line that does not move: **schedule pressure does not change a technical
judgement.** "這個很趕", "單位在等" are real, and they are someone's decision to make
— just not a reason for a severity to drop. Urgent is not the same as safe.

## Replying

Posting to GitLab is outward-facing and irreversible. Draft the reply, show it to
the user in the conversation, and post only after they approve. Then append the
record to `pushback[]` with `appended_at`, and re-validate:

```bash
uv run scripts/report_model.py validate <report.json>
```
