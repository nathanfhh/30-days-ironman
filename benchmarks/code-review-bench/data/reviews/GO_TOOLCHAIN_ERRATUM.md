# Erratum: the Go batch was told the wrong thing about its toolchain

The eight `grafana-*` review agents were dispatched with this line in their
prompt:

> Go repository, no Go toolchain and no network, so nothing compiles or runs.

The first half is false. This machine has:

```
/usr/local/go/bin/go
/usr/local/go/bin/gofmt
/usr/local/bin/golangci-lint
```

The `grafana-103633` reviewer checked instead of taking the instruction on
trust, found the binaries, ran `gofmt -l` (clean), and said so in its report.
Every other agent in that batch took the claim at face value and recorded the
Go toolchain as absent.

What is actually true: there is no module cache and no network, so `go build`,
`go vet` and `go test` cannot resolve dependencies and do not run. `gofmt` runs
fine, and the subset of `golangci-lint` analysers that do not need type
resolution would likely run too.

Direction of the error: it makes the Go batch's *disclosed scan coverage* lower
than what was available. It does not inflate any finding, and it cannot inflate
the score — an unrun linter produces no findings either way. But the `scans[]`
rows in seven of the eight Go reports describe an environment slightly worse
than the real one, and that is my error, not the reviewers'.

Not re-run. The reviews themselves are static reading in all six languages, and
`gofmt` reports formatting only — it would not have changed a finding.
