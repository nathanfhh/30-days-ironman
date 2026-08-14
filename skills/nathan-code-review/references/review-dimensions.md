# The nine dimensions

Phase 3's checklist. Every dimension gets an explicit `pass` / `fail` / `na`
verdict — that is the data behind the nine-cell grid in the report, and a
dimension you glossed over is a cell you cannot honestly fill in.

Anything these lists do not name is still reviewed, against general best
practices. The lists are where this team's calibration differs from the default;
they are not the whole of what a review looks at.

## Contents

- [Before the checklist: critical operation enumeration](#before-the-checklist-critical-operation-enumeration)
- [The requirement document is also under review](#the-requirement-document-is-also-under-review)
- [Is PHI in scope?](#is-phi-in-scope)
- [A — 風格 / Style](#a--風格--style)
- [B — 簡潔 / Simplicity](#b--簡潔--simplicity)
- [C — 安全 / Security](#c--安全--security)
- [D — API 慣例 / API conventions](#d--api-慣例--api-conventions)
- [E — 架構 / Architecture](#e--架構--architecture)
- [F — 資料取用、資料庫 / Data access and databases](#f--資料取用資料庫--data-access-and-databases)
- [G — 測試 / Tests](#g--測試--tests)
- [H — 非 Python 檔 / Non-Python files](#h--非-python-檔--non-python-files)
- [I — 回溯分析 / Retrospective analysis](#i--回溯分析--retrospective-analysis)

## Before the checklist: critical operation enumeration

The most common way a review misses something is not ignorance, it is stopping
early. You are checking a dangerous operation — marking a questionnaire as
submitted, deleting a record, releasing a result — and you ask "is this
guarded?". You trace one call path, you find a validation step, and the worry is
filed as handled. Search over.

But "does a guard exist" was never the question. The question is whether **every
path that reaches this operation passes through a guard**. A building with three
exits and one fire extinguisher is not covered because the extinguisher exists.

So before touching the dimensions:

1. Enumerate the dangerous operations this change can reach.
2. For each one, enumerate *every* path that arrives at it — direct calls,
   indirect callers, background jobs, admin endpoints, management commands,
   migrations, retries.
3. For each path, state whether the guard is on it.

`codegraph callers` and `codegraph impact` are the fast way to build the path
list. `grep` is what settles it: before asserting "all" or "none", grep. See
`scanners.md` for why the two tools cover different blind spots.

An operation where some paths are guarded and others are not is a finding, and
the unguarded path is the evidence.

## The requirement document is also under review

When the merge request carries a specification — an attachment, a linked design
doc, a decision log — you read it to judge whether the implementation covers what
was asked for. That is one direction. The other direction is the document itself:
**is it consistent with itself?**

Look for a section that still describes a mechanism a later section replaced; a
decision log whose entries contradict the prose they point at; a reference to a
constant or a flag that no longer exists in the code. These appear naturally in a
document that has been revised across several review rounds, and they survive
precisely because everyone checks the document against the code and nobody checks
it against itself.

The trap is that a correct implementation makes the contradiction feel harmless.
It is not. Once this merges, the document is the ground truth the next maintainer
reads — and if they open the stale section first, they will change working code
to match a design that was already abandoned. Reconciling the two versions in
your head and moving on is exactly the failure mode: you resolved it for
yourself, not for them.

Severity follows what happens to someone who acts on the wrong section: usually a
**Nit**, and a **Suggestion** when the contradiction covers a safety, security,
or data-integrity rule. The fix is to name both places and say which one is
current.

## Is PHI in scope?

Several rules below escalate when patient-identifiable data is in reach. Decide
this once, from evidence, and record it in `meta.phi_trigger`.

PHI is in scope when the data path this change touches can reach patient
identity or clinical content: 病歷號 / chart number, 身分證字號, name, birth date,
admission and encounter records, orders, lab or imaging results, medications,
diagnoses, or any HL7 / FHIR / DICOM payload.

Cite the `file:line` that made you decide. When it is not in scope, say so in the
report too — a reader who disagrees needs to be able to overturn the call rather
than guess whether you considered it.

## A — 風格 / Style

1. **Function length.** Twenty lines is a warning sign, not a limit. Ask whether
   the function is doing more than one thing and would read better split.
2. **Type hints.** Missing hints matter when they make the code harder to
   follow — what does this parameter accept, what comes back. Absence alone is
   not a finding; obstruction is.
3. **`pathlib` over `os`** for path manipulation.
4. **Names match behaviour.** A function whose name promises less, more, or
   other than what it does is the defect, not the name.
5. **A literal that looks like a typo has to defend itself.** When a constant's
   value differs from its own name, or from the spelling of a neighbouring
   constant, by only a character or two — `SPECIAL_SAT_VALS_PAR_ID =
   "MO08SpecialSetVals"` sitting next to `SAT_VALS_PAR_ID = "MO08SatVals"` — the
   next maintainer will read it as a slip and helpfully correct it. Values
   dictated by something outside this codebase (a `par_id`, an external API's
   field name, a vendor's code) cannot survive that. They need a comment saying
   this is not a mistake, and where the value came from.

   Missing that comment is a **Nit**. It is a **Suggestion** when correcting the
   value would fail silently rather than loudly — a lookup that quietly returns
   nothing is far worse here than one that raises.
6. **Booleans read positively.** `enabled`, not `disabled` — double negatives at
   call sites are where the misreadings happen.
7. **A changed signature with a stale docstring or type hint is Critical.** The
   documentation now actively lies to the next caller, which is worse than
   having none.
8. **Readability threshold.** If you had to read a block several times to
   understand it, that is the finding: ask for a comment explaining *why*, not a
   rewrite.
9. **Comments that restate the code** should go. They cost maintenance and drift
   out of sync.

## B — 簡潔 / Simplicity

1. **Duplicated logic.**
2. **Over-engineering.** Machinery built for a requirement that does not exist.
3. **Rule of Three.** Extract on the third occurrence, not the second.
4. **Dead imports, functions, files, dependencies.** A high-frequency by-product
   of AI-assisted changes: things get generated, superseded, and left behind.
5. **Hidden side effects.** Side effects are not banned. Invisible ones are: if a
   function mutates state, writes, or emits, that has to be legible from its name
   or its signature.

When these principles conflict: **KISS > DRY > YAGNI**. Simple beats
non-duplicated; non-duplicated beats anticipatory.

## C — 安全 / Security

Highest priority, and judged as risk management rather than blanket refusal. A
finding here is not "this is unsafe" but "this much exposure, against this
defence, in this environment".

Derive the treatment from `資料敏感度 × 網路環境 × 既有防禦`:

- **Accept** — the residual risk is understood and carried.
- **Transfer** — moved to a third party by contract or agreement.
- **Mitigate** — controls reduce likelihood or impact. Most findings land here.
- **Avoid** — the activity or approach is dropped entirely.

A High-severity issue on a path your code reliably executes outranks a Critical
one that nothing can reach. Say which it is.

**Always addressed:**

1. Unvalidated input.
2. Non-parameterised SQL.
3. Hardcoded credentials.
4. `eval` / `os.system` — always Critical.

**Rules:**

- Front-end validation does not exempt the back end. Ever. The client is not a
  trust boundary.
- **Every Critical security finding carries three parts**, or it is not
  publishable: a **POC** concrete enough to run (a `curl` invocation, not the
  words "there is a risk"); a **blast radius** — and when PHI is in scope, what
  it costs in PHI terms, spelled out; and a **specific fix**.

**Accepted risk — the internal package registry token.** A read-only token
committed in plain text is an accepted risk here, not an oversight, when all
three hold:

1. the credential's host is the same GitLab host serving this merge request;
2. its URL path is a package registry path (contains `/packages/`);
3. it appears in package installation configuration — `pyproject.toml`,
   `uv.toml`, a Poetry source, `pip.conf`, `.npmrc`.

All three, and it is not Critical: it is listed in the report with
`accepted_risk` set, so it stays visible. Any one of them missing and it is a
hardcoded credential like any other, with the three-part payload attached.

## D — API 慣例 / API conventions

1. **URLs use dashes, not underscores**, and back-end API URLs are lowercase
   throughout. Deviations are **Critical** — this is a hard team convention, and
   consistency is the whole value of a convention.
2. **A missing validation schema is Critical.**
3. **HTTP verb must match safe/idempotency semantics** (RFC 9110) — a `GET` with
   side effects is **Critical**. Retries assume idempotency; prefetchers and
   caches assume safety. Either broken contract bites.
4. **Authentication is not authorisation.** `@jwt_required` establishes who the
   caller is. It says nothing about whether they may touch *this* record. Look
   for the ownership or role check separately.
5. **PII/PHI never appears in a URL**, in the path or the query string. URLs land
   in access logs, proxy logs, referrer headers, and browser history — places
   with none of the protections the response body gets.
6. **Response time.** Under 150ms is the target; tolerance scales with how often
   the endpoint is called.
7. **Batch atomicity.** A partially applied batch leaves the system in a state no
   one designed. When PHI is in scope this is **Critical**, because the
   half-written state is a clinical record.

## E — 架構 / Architecture

1. **Trivial validation belongs in the schema layer** — types, lengths, formats,
   required fields.
2. **Business logic stays out of the repository layer.** A repository talks to
   the database: queries and writes. Business rules move to a module that owns
   them.
3. **Endpoints stay thin.** Validate the schema, call the repository, assemble
   the response, dispatch. Once real business rules, multi-step workflows, or
   reusable logic start growing inside an endpoint, that endpoint has quietly
   become a service layer and the logic should move out.
4. **One decision hardcoded across N modules.** When changing it means changing N
   places in step, that is a finding — and **Critical** when getting it wrong is
   irreversible.

## F — 資料取用、資料庫 / Data access and databases

1. **`dict.get(k, default)` does not defend against `None`.** When the key exists
   and its value is `None`, the default never applies and `None` flows onward.
   Where the value realistically can be `None`, this is **Critical**.
2. **Changing an existing shape breaks its consumers.** Dropping a column,
   renaming, changing a type, or altering a format that already has readers or
   stored data: the moment it ships, everything still using the old shape breaks
   at once (Hyrum's Law — with enough consumers, every observable behaviour is
   something's dependency, whatever the contract says). The safe sequence is:
   - **Expand** — the new shape lands beside the old one, not in place of it.
   - **Migrate** — backfill the existing data and dual-write to keep both
     consistent. A `server_default` or a sentinel constant is not a backfill; it
     makes the schema valid while the data stays semantically empty.
   - **Contract** — remove the old shape only once every consumer has moved.
3. **Multi-step writes without a transaction**; read-modify-write without
   concurrency protection.
4. **Any path touching shared state, multiple callers, or multiple instances**
   gets one question asked out loud: what happens when two requests arrive at the
   same time?
5. **Distributed reality.** Multiple gunicorn workers, multiple replicas. A
   shared resource coordinated by nothing but a local lock is not coordinated.
6. **One environment agreeing is not verification.** Date and number formats,
   timezone, locale, collation, encoding, filesystem case-sensitivity, driver
   and DB session settings all differ between a developer's machine, staging and
   production. Code that leans on one of them behaves correctly right up until
   it moves.

   "The author ran it and it worked" is evidence that it works *there*. It is
   not evidence that the code is correct, and it does not count as the disproof
   the assertion gate asks for. Where the behaviour in question is
   environment-dependent and you cannot check the other environments from here,
   it belongs in `open_questions` with what would settle it named — not dropped
   because someone reported success.

   The tell is a value crossing a boundary in a format nobody declared: a date
   bound as a string, a number parsed out of text, a path assembled by hand.

## G — 測試 / Tests

What matters is assertion quality: is the test checking *actual behaviour*, or
merely that something came back?

Counter-examples, each a finding:

1. Asserting `status_code == 200` without looking at the body.
2. `assert x is not None`.
3. Configuring a mock to return X and then asserting X came back — the test
   verifies the mock, not the code.

`codegraph affected <changed files>` lists the test files this change touches;
tests that should have moved with the code and did not are part of this
dimension.

## H — 非 Python 檔 / Non-Python files

Runs when the diff contains files other than Python. When it does not, the
verdict is `na` with a note saying so.

- **Multi-branch UI components** — every interactive branch gets reviewed, not
  just the happy one.
- **Render state completeness** — loading, empty, and error states each handled
  and displayed.
- **Vue** — XSS via `v-html`; Composition API with `script setup`; props
  validation; reactivity correctness.
- **Dockerfile** — slim or alpine base image; no build tooling left in the final
  image; multi-stage build where it applies.
- **nginx.conf** — caching strategy, and HTML is not cached.
- **docker-compose** — logging configuration, restart policy, volume mount paths.
- **Alembic migration** — reversibility (is there a `downgrade`), consistency
  with the ORM models, destructive DDL.

## I — 回溯分析 / Retrospective analysis

Applies whenever the change modifies an existing function. Three axes:

1. **Signature compatibility.** After the change, is every caller still
   compatible? `codegraph callers` builds the list; `grep` confirms it is
   complete.
2. **Precondition consistency across callers.** For a dangerous operation with
   several callers, list what each one guarantees before calling. Where they
   differ, the judgement that matters is: **is this inconsistency a bug, or is it
   deliberate?** Say which, and why.
3. **Implicit input contracts.** Follow the values, not the parameter names. A
   caller may be passing something that satisfies the signature and violates what
   the function actually assumes about it.

Type errors that a linter reports outside the diff but that this change caused —
a caller broken by a modified signature, for instance — belong here, not in the
"pre-existing project debt" bucket.
