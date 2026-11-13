# ADR-0007 : Jenkins ingestion — poll the JSON API and `consoleText` (stdlib)

- Status: proposed
- Date: 2026-11-13

## Context

pipeline-doctor diagnoses failed GitHub Actions runs (ADR-0002 webhook workflow, ADR-0003
self-hosted poller). The v2 roadmap item adds **Jenkins**: a failing Jenkins build must be
diagnosed with the same guarantees — read-only towards the CI system, secrets masked before any
model, Ollama only (ADR-0004), zero dependency in the core (ADR-0001).

Facts relied on (Jenkins documentation, "Remote Access API"):

- Every Jenkins object exposes a REST-like API under `<object-url>/api/json`; the `tree` query
  parameter selects fields. A build exposes `number`, `result` (`SUCCESS`, `FAILURE`,
  `UNSTABLE`, `ABORTED`, or `null` while running) and `building`.
- A build's console log is available as plain text at `<build-url>/consoleText`.
- Secured instances accept HTTP Basic authentication with a user name and an **API token**.
  Read-only `GET` requests need no CSRF crumb.
- Jobs inside folders / multibranch projects are addressed as `/job/<a>/job/<b>/`.

To verify on the target instance (not guaranteed by the docs we checked):

- `tree` range syntax (`builds[...]{0,10}`): **not used** — we slice client-side instead.
- Whether `consoleText` ever contains hidden ConsoleNote annotations
  (`ESC[8mha:<base64>ESC[0m`): we strip them defensively (see Decision 5).
- The exact authorization needed to read console output under the instance's authorization
  strategy (expected: `Overall/Read` + `Job/Read`).
- Encoding of multibranch branch-job names containing `/` (expected `%2F`).

## Options considered

1. **Inbound webhook from Jenkins** (Jenkins → pipeline-doctor HTTP endpoint). Jenkins core
   ships no generic outbound "build finished" webhook as far as we know; it needs a plugin
   (e.g. Notification plugin) or a `post { failure { … } }` step in every Jenkinsfile. On our
   side it needs a listening HTTP server, a bind/port decision, request authentication (HMAC
   or shared secret) and exposure through NAT — the exact operational surface ADR-0003 and
   ADR-0006 rejected.
2. **Poll the Jenkins JSON API + `consoleText`** with `urllib` (pull). Same model as the GitHub
   poller (ADR-0003): works behind NAT, no inbound port, no Jenkins plugin, no Jenkinsfile edit.
3. **Third-party client library** (python-jenkins, jenkinsapi, api4jenkins). Adds a dependency
   to the core for two `GET` calls; violates ADR-0001 without benefit.
4. **Jenkinsfile pipes its own log into the CLI**
   (`curl …/consoleText | python -m pipeline_doctor -`). Already works today with no code;
   requires Ollama reachable from the Jenkins agent and an edit per pipeline.

## Decision

1. **Option 2 is the supported path.** A new stdlib module `jenkins.py` performs only `GET`
   requests:
   - list builds: `GET {base}/{job_path}/api/json?tree=builds[number,result,building]`,
     then sort by `number` descending and keep the newest `PD_JENKINS_PER_JOB` (default 10);
   - fetch the log: `GET {base}/{job_path}/{number}/consoleText`.
   A build is diagnosed iff `building is false` and `result == "FAILURE"`. `UNSTABLE` and
   `ABORTED` are ignored in v2 (parity with GitHub `conclusion == "failure"`).
2. **Configuration by environment**, like the GitHub poller: `PD_JENKINS_URL` (base URL),
   `PD_JENKINS_JOBS` (comma-separated slash paths, `folder/app/main` → `job/folder/job/app/job/main`),
   `PD_JENKINS_USER`, `PD_JENKINS_TOKEN` (API token of a dedicated read-only Jenkins user).
3. **URLs are built, never followed from responses.** Credentialed requests only go to URLs
   constructed from `PD_JENKINS_URL` + job path + build number; the `url` field Jenkins returns
   (derived from its configured root URL) is never used for a credentialed call. Redirects to
   another host drop `Authorization` (same `_SafeRedirect` rule as `github.py`). `http://` is
   refused unless the host is a loopback address (credentials would travel in clear).
4. **Bounded log read.** `consoleText` can be arbitrarily large and HTTP `Range` support is not
   documented, so the client streams the body in chunks and keeps at most the first 64 KiB and
   the last `PD_JENKINS_MAX_LOG_BYTES` (default 2 MiB); the sanitizer then truncates as today.
5. **Source-specific normalisation stays in `jenkins.py`**: hidden ConsoleNote payloads
   (`\x1b[8mha:` … `\x1b[0m`) are removed before the text leaves the module. `sanitizer.py` is
   not modified; the existing `sanitize()` (secrets, ANSI, truncation) is applied unchanged.
   Jenkins' own `****` masking of bound credentials is not relied upon.
6. **Same diagnosis core.** A Jenkins log goes through exactly the same path as a GitHub log:
   `sanitize` → `ask_ollama` (linear) or `graph.run` (graph engine) → Slack via the existing
   `build_diagnosis_payload` → journal. No Jenkins-specific prompt.
7. **Option 4 is documented** in the README as a zero-code alternative, not maintained code.
8. **Jenkins → fix PR is out of scope for v2** (ADR-0008 covers GitHub Actions runs only).
   Mapping a Jenkins job to a GitHub repository and commit SHA (Git plugin `BuildData`, to
   verify) is a follow-up.

## Consequences

- ADR-0001 (stdlib only), ADR-0003 (pull, behind NAT) and ADR-0004 (Ollama only) hold for
  Jenkins with no new dependency and no inbound port.
- A read-only Jenkins user and API token must be provisioned and stored outside the repo
  (`/etc/pipeline-doctor.env`, chmod 600), like the GitHub token.
- Latency = poll interval, as for GitHub.
- Enabling a job whose last builds already failed diagnoses up to `PD_JENKINS_PER_JOB` old
  failures on the first pass — the same behaviour as the GitHub poller today.
- Jenkins builds are journaled in a separate table keyed by build URL (ADR-0009): Jenkins build
  numbers are per-job small integers and would collide with GitHub `run_id`.
- The ADR-0006 journal viewer reads the `diagnostics` table only; showing Jenkins rows there is
  a follow-up.
