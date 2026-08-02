# Deterministic scanning

LLM tokens are expensive and deterministic tools are cheap, so anything a tool
can settle, a tool should settle. What the tools are bad at — deciding whether a
finding matters here — is what you are for.

Two rules govern everything below.

**Scanner output is a lead, not a finding.** Every item is verified against the
code before it reaches the report. Scanners do not understand this codebase's
context, and a report that forwards their output unfiltered inherits their false
positives.

**Silence is not a clean bill of health.** A tool that reports nothing gets its
exit code checked. A crash that produced an empty result set looks exactly like a
clean scan, and recording it as clean is a lie the report cannot afford. Non-zero
where non-zero is unexpected → `status: error` in `scans[]`, and it is disclosed.

Every tool below has a missing branch: skip the step, record `status: skipped`
with a reason, and disclose it in the published report. The review continues.

## Running them

`scripts/scan_runner.py` wraps all of it — invocation, exit code, archiving the
full output to the path in `workspace-paths.md`, and returning a compact digest.
Subagents read the digest, never the raw JSON: a `trivy fs` result on a
real project runs to megabytes and would consume the phase's entire budget.

```bash
uv run scripts/scan_runner.py <tool> --root <repo> --out <archive-prefix> --diff <diff-file>
```

Fan out `ncr-scan-trivy`, `ncr-scan-opengrep`, and `ncr-scan-lint` in parallel.

## Exclusions

`.git/` and `.codegraph/` are excluded from every scan. The latter is the symbol
index built during Phase 0; scanning your own index wastes time and produces
findings about a directory that does not exist in the repository.

## trivy — supply chain, misconfiguration, secrets

```bash
trivy fs \
  --scanners vuln,misconfig,secret \
  --severity CRITICAL,HIGH,MEDIUM \
  --format json \
  --output {artifact}.trivy.json \
  {repo}
```

One invocation covers dependency vulnerabilities, configuration mistakes, and
committed credentials. The `secret` scanner is doing double duty on purpose:
dedicated git-history credential scanners exist (`gitleaks` and friends), but
trivy's coverage of the working tree is enough here, and a second tool is a
second install and a second thing to maintain. A situation that needs credentials
that were committed and later deleted from history is the point at which to add
one.

The question to answer for each vulnerability is not its CVE score but **whether
this codebase's usage actually reaches it**. A HIGH on a path the code executes
matters more than a CRITICAL in a function nothing calls. Say which, with the
call site as evidence.

**Database.** `trivy image --download-db-only` refreshes the vulnerability
database. If the download fails — offline, proxy, rate limit — run with whatever
database is already cached and note the staleness. Only when there is no usable
database at all does the scan become `status: skipped`.

**Missing:** `trivy` not on PATH → skip, reason `trivy not installed`.

## opengrep — SAST

Rules come from a local checkout of the Semgrep rules repository, at
`$NCR_OPENGREP_RULES` or `~/semgrep-rules` by default.

Do not point `--config` at the repository root: it holds thousands of rules for
languages this codebase does not use. Select rule directories from the file
extensions present in the diff:

| In the diff | Rule directory |
|---|---|
| `.py` | `python/` |
| `.js` `.jsx` `.mjs` `.cjs` `.vue` | `javascript/` |
| `.ts` `.tsx` | `typescript/` |
| `Dockerfile*` | `dockerfile/` |
| `.yml` `.yaml` | `yaml/` |
| `.html` | `html/` |
| `.json` | `json/` |
| `.sh` `.bash` | `bash/` |

`generic/` is always included — it holds cross-language rules (hardcoded
credentials, insecure transport) that apply whatever the file type.

```bash
opengrep scan \
  --config {rules}/python --config {rules}/generic ... \
  --json --output {artifact}.opengrep.json \
  {repo}
```

**Missing:** `opengrep` not on PATH, or the rules directory absent → skip, with
the reason naming which of the two was missing.

## ruff / ty / oxlint — static analysis

```bash
ruff check --output-format json {repo}
ty check   --output-format concise {repo}   # ty has no json format; see below
oxlint     --format json {repo}
```

`ty` accepts `full`, `concise`, `gitlab`, `github`, and `junit` — not `json`. The
runner parses its `path:line:col: severity[rule] message` output and records a
note saying so, so a report reader can tell that line came from text parsing
rather than structured output.

**Scope and attribution.** These run over the whole project, because `ty` needs
the project to resolve types correctly and a file-by-file run produces phantom
errors. But the whole project is not what is under review, so attribute the
results:

- A diagnostic whose `file:line` falls inside a diff hunk → a finding.
- Everything else → not a finding. Aggregate it into a single disclosure line
  ("專案既有問題 N 件，不列入本次"). Reporting pre-existing debt as though this
  author introduced it is how a report loses its reader.
- **One exception**: a diagnostic caused by this change but landing outside the
  diff — a caller broken by a modified signature is the usual case. That is a
  finding, and it belongs to dimension I. This is precisely the class of defect
  the whole-project scan exists to catch, so do not let the attribution filter
  discard it.

**Missing:** each is independent. `ruff` absent does not stop `oxlint`; record a
separate `scans[]` entry per tool.

## CodeGraph and grep

`codegraph` indexes the repository into a graph whose nodes are symbols and whose
edges are `call`, `def`, and `import`. Built during Phase 0.

```bash
codegraph explore  <query>  -p <repo>            # symbols + call paths, one shot
codegraph callers  <symbol> -p <repo> -j         # who calls this (fan-out)
codegraph impact   <symbol> -p <repo> -d 2 -j    # what a change here reaches
codegraph affected <files...> -p <repo>          # test files touched by these changes
```

It is a navigation accelerator, not the final word on completeness — and the
reason is mechanical. The graph has three edge types. Any reference that does not
form a `call`, `def`, or `import` edge is invisible to it: dynamic dispatch,
`getattr`, string-keyed registries, template references, configuration that names
a handler.

So the division of labour:

- **Structural and transitive questions** — what does this change touch, who
  calls this, who breaks if the signature changes → **CodeGraph first**. Symbol
  queries are close to zero false positives and give a clean signal.
- **Completeness questions** — did I find every occurrence, is there an
  unmigrated caller left, is a decorator missing → **grep decides**. Before
  asserting "all" or "none", grep.

Run both. Each one's blind spot is covered by the other: CodeGraph sees the
second and third hops that a mental model of grep results drops; grep sees the
edges that were never on the graph. In a review where the cost of a miss is a
clinical system, that redundancy is worth more than the queries it saves.

The general principle, applied to any tool added here later: write down what it
*cannot see* before deciding what it is allowed to be responsible for.

**Missing:** `codegraph` absent or `init` failed → note it, and use `grep` for
everything. Dimensions E and I still get done; they just cost more searching.
