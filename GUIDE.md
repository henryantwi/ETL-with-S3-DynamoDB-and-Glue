wi# Music ETL Pipeline — Complete Guide (Plain English)

A walkthrough of the whole system for someone defending it in a code review.
No prior knowledge assumed. Read top to bottom; each section builds on the last.

---

## 1. What this project actually does

You run a **music streaming analytics pipeline**. Raw data files land in cloud
storage; the system automatically cleans them, computes daily "what genre was
popular" statistics, and stores those stats in a fast database that a dashboard
or API can read.

**The one-sentence version:** drop 3 CSV files in an S3 bucket → a few minutes
later, daily genre KPIs appear in a DynamoDB table, and your raw files are
safely archived.

### The three input files

| File goes to prefix | Contains | Required columns |
|---|---|---|
| `song-catalog/` | the music library | `track_id`, `track_name`, `artists`, `track_genre`, `duration_ms` |
| `user-profiles/` | who the listeners are | `user_id`, `user_name`, `user_country` |
| `listening-activity/` | the play events (the "facts") | `user_id`, `track_id`, `listen_time` |

Only **`listening-activity/`** uploads start the pipeline. The other two are
reference data that must already be sitting in the bucket when the streams land.

### The output

A DynamoDB table called **`MusicKPIs`**, one row per `(genre, date)`, holding:
listen count, unique listeners, total + average listening time, top 3 songs,
and top 5 genres for that day.

---

## 2. The big picture (how a file becomes a KPI)

```
You upload streams.csv to s3://raw-data/listening-activity/
        │
        ▼
[S3 bucket]  emits an "Object Created" event
        │
        ▼
[EventBridge rule]  "is the key under listening-activity/? yes → forward"
        │
        ▼
[SQS dispatch queue]  holds the event(s) briefly  ← the coalesce + serialize layer
        │
        ▼
[Dispatcher Lambda]  "is a run already going? no → start ONE pipeline run"
        │
        ▼
[Step Functions state machine "etl-pipeline"]  the conductor — runs 4 steps in order:
        │
        ├─ 1. ValidateFiles   (Glue)  → headers/encoding OK on all 3 files? bad ones quarantined
        ├─ 2. TransformMetrics (Glue/Spark) → join + aggregate → Parquet in processed bucket
        ├─ 3. LoadMetrics     (Glue)  → read Parquet → write rows to DynamoDB
        └─ 4. ArchiveFiles    (Glue)  → move raw files to the archive bucket (raw emptied)
        │
        ▼
[DynamoDB MusicKPIs]  results land here. Done.
```

Every box above is created by Terraform. The rest of this guide explains each
one and **why it's built the way it is** — which is what a reviewer will probe.

---

## 3. Terraform — the foundations

### 3.1 What Terraform is (the mental model)

Terraform is **"infrastructure as code."** Instead of clicking around the AWS
console to create buckets, databases, and permissions, you *describe* them in
`.tf` files. Terraform compares your description to what currently exists in AWS
and makes reality match the description.

Three commands matter:
- `terraform init` — download the AWS plugin, connect to where state is stored.
- `terraform plan` — "here's what I *would* change." (Read-only. Safe.)
- `terraform apply` — actually make the changes.

**State file:** Terraform remembers what it built in a file called
`terraform.tfstate`. Ours lives in an S3 bucket (not on a laptop) so the CI
robot and any teammate share one source of truth. A DynamoDB **lock table**
stops two people applying at the same moment and corrupting it.

> Review talking point: "Why remote state?" → So CI and humans don't fork
> reality. "Why the lock table?" → Prevents concurrent applies from racing.

### 3.2 How our Terraform is organized

```
terraform/
├── backend.tf        # where state lives (S3 + lock table) + provider/region
├── variables.tf      # the knobs (project name, region, account id, queue tunables)
├── main.tf           # the whole stack wired together (the "root" module)
├── outputs.tf        # values printed after apply (bucket names, ARNs)
├── modules/          # reusable building blocks
│   ├── s3/           #   a hardened S3 bucket (encryption, block-public, lifecycle)
│   ├── glue/         #   a Glue job (handles both Spark + Python-shell types)
│   ├── dynamodb/     #   the MusicKPIs table
│   └── iam/          #   ALL the permission roles in one place
└── bootstrap/        # one-time setup: GitHub↔AWS trust + CI roles (applied by hand)
```

**Modules** are like functions. `modules/s3` is written once; `main.tf` "calls"
it four times (raw, archive, glue-scripts, processed) with different arguments.
That's why all four buckets are encrypted and locked-down identically — one
definition, reused.

> Review talking point: "Why modules?" → DRY. One hardened bucket definition,
> reused 4×; fix a security setting once and every bucket inherits it.

### 3.3 The S3 module (`modules/s3/main.tf`)

Every bucket gets, no exceptions:
- **AES256 encryption at rest** (`server_side_encryption_configuration`).
- **All four public-access blocks ON** — the bucket physically cannot be made
  public even by accident.
- **Versioning** — optional, on a flag. Only the **raw** bucket turns it on
  (so an overwritten upload is recoverable).
- **Lifecycle** — optional, on a flag. Only the **archive** bucket turns it on:
  files move to cheap Glacier storage after 90 days, deleted after 365.

The clever bit: `count = var.enable_lifecycle ? 1 : 0`. This is Terraform's way
of saying "create this resource only if the flag is true." Same trick toggles
versioning via a ternary. One module, four behaviours.

### 3.4 The DynamoDB module (`modules/dynamodb/main.tf`)

The `MusicKPIs` table:
- **`hash_key = "genre"`, `range_key = "date"`** — the primary key is the pair
  `(genre, date)`. That's exactly one row per genre per day, and it's why
  re-running a day **overwrites** instead of duplicating (idempotent).
- **`PAY_PER_REQUEST`** — no fixed capacity to manage; you pay per read/write.
  Right call for spiky, unpredictable ETL traffic.
- **Encryption + point-in-time recovery + deletion protection** all ON — can't
  accidentally `terraform destroy` the table away.

> Review talking point: "Why (genre, date) as the key?" → It's the natural
> grain of the output and makes reloads idempotent — the writer PUTs the same
> key and the latest run's values win, no duplicate rows.

### 3.5 The Glue module (`modules/glue/main.tf`)

Glue is AWS's managed "run my Python/Spark script" service. We have **two
flavours** of job and one module that builds both:
- **`glueetl`** (Apache Spark) — heavy lifting, distributed. Used for the
  transform step (joining + aggregating lots of rows). Runs on `G.1X` workers.
- **`pythonshell`** — a single small Python process. Used for validation,
  the DynamoDB writer, and archiving — cheap tasks that don't need Spark.
  `max_capacity = 0.0625` = the smallest, cheapest Glue unit.

The module uses **`dynamic "command"`** blocks to emit the right configuration
for whichever type you asked for. The comments capture two real gotchas that
were learned the hard way (Python-shell needs Glue 3.0 + Python 3.9, or
`StartJobRun` rejects it).

> Review talking point: "Why two job types?" → Cost/fit. Spark for the one
> step that needs it; tiny Python-shell for the three that don't.

---

## 4. The orchestrator — Step Functions

`step_functions/etl_pipeline.asl.json` is the **conductor**. It's a JSON
definition of a state machine: do step 1, if it succeeds do step 2, etc., and
if any step fails, jump to a labelled failure state.

The flow:

1. **CheckRunDate / DeriveRunDate** — figures out which calendar day this batch
   belongs to. If the caller didn't pass a `run_date`, it derives `YYYY-MM-DD`
   from the execution's start time. This date becomes the partition label, so
   reruns of the same day overwrite cleanly.
2. **ValidateFiles** → runs the `etl-validate-files` Glue job.
3. **TransformMetrics** → runs the `etl-genre-metrics` Spark job.
4. **LoadMetrics** → runs the `etl-metrics-writer` job.
5. **ArchiveFiles** → runs the `etl-archive-files` job.
6. **PipelineSucceeded** (Succeed) — or one of the four **`*Failed`** states.

Two important patterns to point out in review:

- **`.sync` integration** (`arn:aws:states:::glue:startJobRun.sync`): Step
  Functions starts the Glue job **and waits** for it to finish before moving on.
  Without `.sync` it would fire-and-forget and the steps would race.
- **`Catch` on every step**: any error is caught and routed to a named failure
  state (`ValidationFailed`, `TransformFailed`, …). So a failed run tells you
  *exactly which stage* broke, and the later steps never run on bad data.

> Review talking point: "What happens if the transform crashes?" → The Catch
> sends it to `TransformFailed`, the run stops there, DynamoDB is never touched,
> and raw files are NOT archived — so you can fix and rerun with the same input.

---

## 5. The four Glue jobs (what each script does)

| Step | Job name | Type | Job in one line |
|---|---|---|---|
| Validate | `etl-validate-files` | python-shell | Check all 3 files have required headers + are valid UTF-8 CSV; move bad files to `rejected/<timestamp>/` and fail the run. |
| Transform | `etl-genre-metrics` | Spark | Join listening events to the song catalog, compute per-genre-per-day KPIs, write Parquet to the processed bucket. |
| Load | `etl-metrics-writer` | python-shell | Read that Parquet, convert rows to DynamoDB items, write them in transactions (idempotent upserts). |
| Archive | `etl-archive-files` | python-shell | Move raw files from the raw bucket to the archive bucket (copy + delete), emptying raw. |

**Why archive empties raw:** it's the "I've consumed this batch" marker. The
trade-off (called out in the upload guide): a second run needs a fresh complete
set of all three files, because run 1 took the reference data with it too.

---

## 6. The auto-trigger layer (EventBridge + SQS + Lambda)

This is the part most worth understanding — it's where the recent work went.

### 6.1 The naive version (and why it's not enough)

Simplest design: S3 event → EventBridge → start Step Functions directly. Two
problems:
1. **Bursts.** You upload streams1, streams2, streams3 seconds apart. That's 3
   events → 3 pipeline runs, all processing the same batch. Wasteful and racy.
2. **Overlap.** A run takes minutes. If new files land mid-run, you'd start a
   second run that collides with the first (and the first's archive step is
   busy emptying the bucket the second is reading).

### 6.2 Our version — a queue + a smart consumer

We slot an **SQS queue** and a **dispatcher Lambda** between EventBridge and
Step Functions. (`main.tf` lines ~298–448.)

```
EventBridge → SQS dispatch queue → Dispatcher Lambda → Step Functions
```

**Coalesce (solves bursts):** the Lambda's event-source mapping has a
**90-second batching window** (`maximum_batching_window_in_seconds`) and a
**batch size of 100**. So a burst of uploads is delivered to the Lambda as
*one* invocation → the Lambda starts *one* run. The log line proves it:
`{"event": "started", "coalesced": 1}`.

**Serialize (solves overlap):** before starting, the Lambda asks Step Functions
"is there already a RUNNING execution?" (`_has_running_execution()`). If yes, it
**defers** — it extends the messages' visibility timeout so they reappear later,
and starts nothing. So two runs can never overlap; one fresh run fires once the
current finishes.

**The handler logic** (`lambda/pipeline_dispatcher/handler.py`), in plain terms:
```
got a batch of messages?
  no  → do nothing
  yes → is a pipeline already RUNNING?
          yes → defer the whole batch (re-hide messages), start nothing
          no  → start exactly ONE execution
                  succeeded → tell SQS to delete the messages
                  failed    → return them as failures so SQS retries
```

### 6.3 The safety nets

- **Dead-letter queue (DLQ):** if a message genuinely can't be processed after
  **50 receives** (`maxReceiveCount`), it's parked in `etl-pipeline-dispatch-dlq`
  for inspection instead of looping forever. The count is deliberately high
  because normal "waiting for the current run" deferrals also count as
  receives — those aren't failures.
- **Visibility timeout = 960s:** must be longer than the longest pipeline run,
  so a deferred message doesn't reappear *during* the run it's waiting on.
- **Queue resource policy:** only *our* specific EventBridge rule is allowed to
  send to the queue (`aws:SourceArn` condition). Not the whole world.

> Review talking point — the known wart: **`reserved_concurrent_executions` was
> removed.** Ideally the dispatcher is pinned to 1 concurrent instance so the
> "is a run going?" check can't race itself. But this AWS account's total Lambda
> concurrency limit is **10**, and reserving any pulls the shared pool below the
> hard floor of 10 — AWS rejects it. So serialization currently leans on the
> in-handler RUNNING-guard, which has a small race window. The fix is documented
> inline: raise the account quota, then restore `reserved_concurrent_executions = 1`.

---

## 7. Security — IAM (least privilege)

All permission roles live in **`modules/iam/main.tf`**. The guiding rule:
**every role can do the minimum it needs, on the exact resources it needs, and
nothing else.** No `Action: "*"`, no `Resource: "*"` (except where AWS literally
forbids scoping, e.g. `cloudwatch:PutMetricData`, which is commented).

The roles:

| Role | Can do | On |
|---|---|---|
| `etl-glue-validation-role` | read raw, write to `rejected/`, delete bad files, write logs | raw bucket only |
| `etl-glue-transform-role` | read raw, read/write processed, push CloudWatch metrics | raw + processed only |
| `etl-glue-writer-role` | `PutItem`/`TransactWriteItems` to DynamoDB, read processed | MusicKPIs + processed only |
| `etl-glue-archive-role` | read raw, write archive, delete raw | raw + archive only |
| `etl-stepfunctions-role` | start/poll `etl-*` Glue jobs | the Glue jobs only |
| `pipeline-dispatcher-role` | consume the queue, start/list `etl-pipeline` executions | that queue + that state machine |
| `metrics-reader-policy` | `GetItem`/`Query` only (no `Scan`) | MusicKPIs — for dashboard consumers |

> Review talking point: "Show me least privilege." → Point at the writer role:
> it can write the KPI table and read processed Parquet, but it **cannot** touch
> the raw bucket or start jobs. Each role is a tight blast radius. Each is its
> own customer-managed policy with a descriptive name, so an auditor reads intent.

**Trust policies:** each role also has an "who is allowed to *assume* me" rule.
Glue roles trust `glue.amazonaws.com`, the dispatcher trusts
`lambda.amazonaws.com`, Step Functions trusts `states.amazonaws.com`. A service
can only wear a role explicitly handed to it.

---

## 8. CI/CD — how code reaches AWS

### 8.1 The bootstrap (one-time, by hand)

`terraform/bootstrap/` is a **separate, smaller** Terraform project applied once
with admin credentials. It's separate on purpose: it creates the very trust
relationship that the main stack's remote-state access depends on, so it can't
live inside that same state (chicken-and-egg). Its state stays local.

It creates:
- An **OIDC identity provider** — lets GitHub Actions prove "I'm a workflow from
  *this* repo" to AWS **without any long-lived secret keys**. This is the modern,
  secure way; no AWS access keys sitting in GitHub.
- **`Project1-CI-Plan`** role — read-only. Used by PRs and pushes to run
  `terraform plan`.
- **`Project1-CI-Deploy`** role — read/write. Assumable **only** from the gated
  `production` GitHub environment, and scoped to `repo:<repo>:environment:production`.

> Review talking point: "Where are the AWS keys in CI?" → There are none. OIDC
> federation issues short-lived credentials per run. The deploy role can only be
> assumed from the protected `production` environment, so a random PR can't deploy.

### 8.2 The pipeline (`.github/workflows/ci.yml`)

Three jobs, gated in sequence:

1. **`lint-and-test`** (every push + PR): ruff lint + format check, then
   `pytest tests/` (77 unit tests), then a JSON-parse sanity check on the Step
   Functions definition.
2. **`terraform-plan`** (needs lint-and-test): assumes the **read-only** plan
   role via OIDC, runs `init` → `validate` → `plan`. Catches infra mistakes
   before they're applied.
3. **`deploy`** (needs both, **only on push to `main`**): assumes the **deploy**
   role, runs `terraform apply -auto-approve`. Glue scripts upload implicitly —
   the `aws_s3_object` resources use `filemd5` etags, so a changed script is
   re-uploaded as part of apply. No separate build/package step.

So: a PR runs tests + plan but **cannot deploy**. Only a merge to `main` deploys.

> Review talking point: "How does a Glue script change get to AWS?" → The script
> files are `aws_s3_object` resources keyed by `filemd5`. Change the script →
> the etag changes → `terraform apply` re-uploads it. Infra and code ship together.

---

## 9. Observability — knowing it worked (or why it didn't)

- **Step Functions execution history** — the source of truth for "did the run
  succeed, and which step failed."
- **CloudWatch Logs** — every Glue job streams logs (`--enable-continuous-cloudwatch-log`);
  the dispatcher Lambda logs structured JSON (`started` / `deferred` / `start_failed`).
- **CloudWatch alarms** — two alarms watch the transform and writer jobs for
  failed tasks; on failure they notify the **`etl-pipeline-alerts`** SNS topic.
- **Verify commands** (from `docs/upload-guide.md`): list executions, scan
  MusicKPIs, check the queue depth.

---

## 10. Likely code-review questions — quick answers

| Question | Answer |
|---|---|
| Why a queue instead of triggering Step Functions directly? | Coalesce bursts into one run + serialize so runs never overlap. |
| Why is the dispatcher's reserved concurrency missing? | Account concurrency limit is 10; any reservation breaks the hard floor. Falls back to the handler RUNNING-guard; restore once quota is raised. |
| Why `(genre, date)` DynamoDB key? | Natural output grain; makes reloads idempotent (overwrite, no dupes). |
| Why `.sync` in Step Functions? | Wait for each Glue job to finish before the next step; otherwise they race. |
| Why does archive empty the raw bucket? | It's the "batch consumed" marker; trade-off is each run needs a fresh full set. |
| Why two Glue job types? | Spark only for the heavy join/aggregate; cheap python-shell for the rest. |
| Where are CI's AWS secrets? | None — GitHub OIDC federation issues short-lived creds; deploy gated to `production`. |
| How is least privilege enforced? | Per-task roles, exact resource ARNs, no wildcards except where AWS forbids scoping (commented). |
| What stops a PR from deploying? | Deploy job runs only on push to `main` and assumes a role trusting only the `production` environment. |
| How do schema-bad files behave? | Validation quarantines them to `rejected/<ts>/` and fails the run; bad data never reaches transform. |

---

## 11. The recent change set (what you just shipped)

The PR added the **SQS coalesce + serialize layer** in front of the existing
pipeline, plus the fixes to make it deploy and test cleanly:

1. **`AWS_DEFAULT_REGION` in the dispatcher test fixture** — the handler builds
   boto3 clients at import time; without a region, importing the module in tests
   threw `NoRegionError`. Added the env var → 77/77 tests pass.
2. **`sqs:*` + `lambda:*` on the CI deploy role** (bootstrap) — the new resources
   couldn't be created until the deploy role was allowed to manage them.
3. **Dropped `reserved_concurrent_executions`** — account limit of 10 made it
   un-applyable (see §6.3).

End-to-end validated live: 3 uploads coalesced into **one** SUCCEEDED run, raw
archived, **113 KPI rows** written to MusicKPIs.
```
