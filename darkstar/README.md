# darkstar — Platform Engineering delivery dashboards

## What it is

A FastAPI app that answers four questions about how Platform Engineering is doing, from data it
already has: **who should pick up the next ticket**, **when the open work will land**, **how much
each engineer is completing**, and **how much of PE's output now comes through the self-service
skills, and how fast**.

It is not an AI product and uses no model. Every figure is arithmetic over two sources — the Jira
changelog and the GitLab merge-request API — pulled into a local DuckDB store by two background
pollers. Dashboards read only that store, so a page load makes no upstream call and cannot be
slowed or broken by Jira being slow.

It replaces the manual "Connect / Fetch" flow of the v1 Streamlit report. It lives in this repo but
shares no code with the Streamlit `app.py`; it has its own entrypoint and workload, and rides the
**same container image** (an isolated virtualenv keeps their dependencies apart — see Deploy).

## The panels, and what each is for

| Route | The question it answers | Derived from |
|---|---|---|
| `/intake` | Who should pick up this ticket? | Open Jira tickets + per-engineer capacity + an expertise signal built from what people actually build in GitLab |
| `/slas` | Is self-service carrying real load, and how fast does it deliver? | Jira labels + MR labels/footers, MR ready→merged times |
| `/delivery-forecast` | When will the open Initiative and Features land? | Monte-Carlo simulation over recent completion pace |
| `/velocity` | How much is each engineer completing per month? | Earliest Jira changelog transition to Done |
| `/lead-time` | How long do delivered stories take, and how much of it is waiting? | Lead (created→Done) vs cycle (time in active statuses) — **hidden from the nav**, still served |

`/` redirects to `/intake`; the nav lists Intake, Self-Service, Delivery Forecast, Velocity in that
order. `/slas` keeps its route name for existing bookmarks though the page is labelled Self-Service.
`/lead-time` is deliberately unlinked — it was not earning its place — but the route, its
`/api/lead-time` endpoint and `leadtime.py` are untouched; restoring it means putting the
`<a href="lead-time">` entry back in the four dashboard navs.

### What the self-service page shows

- **Impact cards** — share of all delivered PE work that came through a skill (with the share whose
  code was AI-written beside it), requests this month vs last, self-service delivery speed against
  the rest of PE delivery, and time to first review. That speed card carries the same significance
  caveat as the dropped monthly column — see **Delivery turnaround by month** below.
- **Delivery turnaround** — self-service requests grouped by the period they were created. **The
  grain follows the lookback** rather than being fixed: ≤14 days is daily, ≤31 days weekly, longer
  monthly. A fixed weekly grain broke at both ends — a 90-day window was 14 rows to scroll, and a
  7-day window was one row that said nothing about the week. Every preset now lands on one to five
  rows, and `?grain=day|week|month` (the **Rows** control) forces one when the default is not what you
  want. Medians cannot be re-aggregated from coarser medians, so the folding happens server-side from
  raw hours, not by collapsing rows in the browser.

  The grain matters because both readings are true at different zooms: monthly, the p50 runs 99.2h →
  13.8h → 10.0h → 2.9h across May to August, which is the improvement arc; weekly inside the last 30
  days it runs 4.9h, 5.5h, 1.7h, 3.6h, which is the noise floor the arc is made of. Reported as a
  table with a bar for the share closed inside one working day, because the p50 spans two orders of
  magnitude and a linear axis would bury exactly the recent periods the panel exists to show.

  Grouping by arrival means a recent cohort may not have closed, so the row carries **In** (requests
  created that week) beside **Done** (how many have landed). A `*` marks the gap: that week's p50
  counts only what finished, so it reads faster than it will once the rest land — the week of
  2026-08-17 showed 10 in, 4 done. Without that column a half-settled week looks like the fastest
  week on record.

  There is **no rest-of-PE comparison column**, and that is a measured decision rather than an
  omission. Head to head on live Jira for June, with abandoned statuses excluded from both arms,
  self-service ran 15.9h p50 against 37.9h but 174.8h p90 against 144.0h — better at the median,
  worse in the tail, Mann-Whitney z=+1.44 at n=26, which is not significant. A monthly multiple
  would read as a finding the sample cannot carry. What the work does demonstrate is capacity, which
  the next panel reports.
- **How requests arrive** — every request created that period split by **how it was created**: filed
  by a self-service skill, or filed by a person. The bar is the self-service share. This is the
  capacity argument in the unit that means something for it — demand PE absorbed without a person
  writing the ticket.

  **The unit is requests, not merge requests.** One request counts once however many MRs it took,
  which is often several: July ran 643 merged MRs against just **117 distinct tickets** (349 named a
  DEVOPS key, 294 named none, and one ticket was spread across 19 MRs). An earlier cut of this panel
  counted MRs and put 645 on the page beside a ~200 ticket count, which reads as double counting; it
  was neither double counting nor the right question, since a branch total says nothing about demand.

  A request counts as self-service if a skill's **Jira watermark** is on it, or if a merge request it
  produced carries a **pe:\*** label — some early skill-filed tickets never got the watermark and that
  label is the only remaining evidence. Reading a merge request for that signal is not the same as
  counting it, which is why the bounded lookback deliberately does not cut it off (see **Page
  controls**).

- **MR turnaround** — one panel, because the table and the daily chart are the same population under
  the same filters. Ready→merged per author, slowest first, then the same MRs cut by the day they
  landed as one coloured line per author. The table **is** the chart's legend: colours are assigned
  per author from the series order and shared by both, and hovering a table row isolates that
  author's line (click to pin). There is no separate legend, because it would only repeat the table.

The page is a two-column grid of bordered panels; the card row and MR turnaround span both
columns, collapsing to one column below 980px. Each panel's heading collapses its own section, and
each carries **one** collapsed "About this panel" explainer rather than standing prose — the page
was mostly text otherwise. Dynamic status stays visible: a crawl still owed, rows that cannot be
measured, mixed-environment exclusions, the result of adding an author. The derivation notes at the
foot are a `<details>` collapsed by default.

## Durations are formatted, never printed raw

Every duration on the page is business hours, and printing them as decimal hours made the reader do
the arithmetic: time to first review read `0.1h` for what is six minutes, and the MR medians read
`0.3h` and `0.1h` for eighteen and six. One formatter (`durText`) now owns all of them, in three
tiers because the values span four orders of magnitude:

| range | rendered | example |
|---|---|---|
| under a minute | `<1m` | 0.001h |
| under an hour | minutes | 0.1h → `6m` |
| under a working day | hours and minutes | 2.9h → `2h 54m` |
| a working day or more | days, hours **and minutes** | 43.9h → `4d 7h 54m` |

Minutes are carried at **every** tier. Dropping them past a day looked tidier and cost up to 59
minutes a cell, which made a decomposition that balances perfectly look an hour out — `4d 7h` beside a
`3h 12m` draft and a `5d 2h` total does not read as arithmetic even though `43.9 + 3.2 = 47.1` exactly.
Verbose and unambiguous beats tidy here, because the columns are meant to be checked.

**A day here is nine hours, not twenty-four**, because that is what the clock counts — so 45h renders
as `5d`, a working week, and rendering it as `1d 21h` would be a different kind of wrong. The decimal
figure stays in each cell's `title`, so nothing is lost. The chart's y axis stays in plain hours: it
is a linear hour scale, and mixed units on the ticks would make even spacing look arbitrary.

## Links carry a measured contrast

The drill-down's merge-request refs shipped as bare `<a>` tags, and the page defines no generic link
rule, so they fell back to the browser default. Measured against the panel that is **1.72:1**, and
**1.47:1** once visited, where AA body text needs 4.5:1 — unreadable, and reported as such.

`main.card a` now sets the accent (5.94:1 on `--panel`, 6.35:1 on `--well`) so no future link in the
card can fall back. The nav sits outside `main.card` and keeps its own quieter treatment, so the rule
cannot reach it. Table links additionally render in mono, since a merge-request ref is an identifier,
with a part-strength underline that would otherwise turn a column of them into a wall of rules.

The other dashboards were checked and do not have this problem: `intake`'s `.key` is `#7db0ff` at
7.99:1 and `delivery-forecast`'s `.k`/`.skey` are `--muted` at 4.78:1. Both pass, though the forecast
links are dim enough to be worth revisiting.

## The ready clock excludes red pipelines

`ready → merged` counts the hours an MR was **offered for review and mergeable**. Two spans are cut
out of it:

| excluded | why |
|---|---|
| **draft** | the author has not asked for review yet, so nobody is waiting |
| **red CI** | a failing pipeline blocks the merge whoever reviews it; the ball is with whoever pushes the fix |

The red exclusion is universal rather than conditional on who authored the MR. The alternative was
tempting — the clearest case is an outside author parking a broken MR while PE waits — but measured
over the slowest merge requests it is not where the time is: **32%** of open hours on PE-authored ones
were spent red against **4%** on externally-authored ones. The distribution is bimodal, not a smear:
an MR is either red for essentially its whole life or never. `tf-gcp-edp-dev!180` sat red for 187.6 of
187.7 hours across ten runs.

Two consequences worth understanding:

**It is interval arithmetic, not subtraction.** Draft and red overlap, so deducting red *hours* from
the ready total double-counts and can drive an MR to zero. `subtract_spans` removes the overlap
instead. There is a test for exactly this: 3h ready, 7h red, all of the red inside the draft window,
answer still 3h.

**Excluded is not hidden.** `red_hours` is reported on every row and shown as its own **Red CI** column
in the drill-down, so an MR parked broken for days still says so instead of merely reporting a small
number. Only `failed` stops the clock — a run still in flight is the normal state of a live MR.

A pipeline fetch that fails leaves the MR with its **full** clock and no exclusion. That errs toward
charging PE for time it may not owe, which is the safe direction: a network error must never quietly
make a merge request look fast.

This costs one API call per merge request and roughly doubles a full crawl, which already runs at
about 0.75s/MR. `mr_pipelines` stores the raw results rather than a computed red total, so the clock
can be retuned without re-crawling — the same reasoning as keeping MR descriptions verbatim.

`pipelines_fetched_at` on `merge_requests` is what makes the history fill at all. `_needs_backfill`
keys on it, so a store whose merge requests predate the table forces one full re-crawl; without it the
five other markers were already satisfied, the crawl stayed incremental forever, and the 2,649 stored
merge requests would never have been read for pipelines — the Red CI column permanently empty and red
time excluded from nothing. It has to be a **column**, not "has rows in `mr_pipelines`": an MR can
legitimately have zero pipelines (two of a 20-MR sample did), so absence of rows cannot mean "not yet
crawled" or the backfill never terminates. Exactly why `events_fetched_at` exists.

## Inspecting an outlier

**Slowest merge requests** under the MR turnaround chart is the drill-down behind the aggregates —
the same population, same filters, slowest first, **ten to a page** with the rank carried on each row
so a page of ten does not lose its place in the ordering.

Paged rather than scrolled, deliberately. A fixed box with an inner scrollbar hides how deep the tail
goes and is easy to miss inside a page that already scrolls; a pager states the population outright
(`1–5 of 100 slowest · 30 faster of 130 measured not listed`), which is the thing a drill-down has to
be honest about. It is also what let the server cap rise from 25 to 100 — at 25 the rest were
unreachable rather than merely unlisted.

**Five rows by default**, adjustable to 10, 20 or 50 and remembered in `localStorage`. Ten filled the
panel and pushed everything below it off screen, and this is a list for inspecting outliers rather than
browsing. Changing the size keeps the first visible row visible instead of jumping to the top —
widening the page to look closer at row 12 should not send you back to row 1. New data resets to page
one, so a narrower filter cannot leave you on a page that no longer exists.

The pager updates its own parts rather than being re-rendered, because the size control lives inside it
and would lose its state on every reload.

The duration columns are a **decomposition**, not a list, and they reconcile exactly:

```
Mergeable → merged  +  Red CI  +  Draft  =  Open
      4d 7h 54m     +   0m     + 3h 12m  =  5d 2h 6m
```

**`Mergeable → merged` is the one number to read.** It is the business hours the merge request was
approvable — marked ready, pipeline green — and therefore waiting on PE to merge it. The header says so
rather than making the reader infer it from a word like "turnaround".

Read right to left that is the whole span less the time the author had not offered it for review, less
the time a failing pipeline blocked the merge whoever looked at it, leaving the time that was
genuinely on PE. Read left to right it is the answer first and then what was taken out to reach it. A large number in either
middle column means the wait was never on review, which is the question this panel gets asked.

The order was originally Ready→merged, Open, Draft, Red CI — a flat list that hid the arithmetic
entirely. Adam spotted it on `gitops-k8s-team-a2!2109`: 5d 2h open, 3h 12m draft, 4d 7h turnaround,
which does not look like it subtracts. It did (47.1 = 43.9 + 3.2 + 0) and two things made it
unreadable: a **day here is nine hours**, not 24, and the formatter *floored* the remainder, losing up
to 59 minutes a cell. Minutes are now carried at every tier so the identity holds exactly, it is stated in the caption, the
nine-hour day is stated with it, and every cell keeps its exact decimal figure on hover.

### To review

Outside the sum, and on the same clock: the wait until the first review signal. Three signals, earliest
wins — a **comment**, an **approval**, or the **merge itself** where neither exists, because an MR from
outside PE cannot be merged by its requester, so the merge is PE's review action. A self-merge is not a
signal on its own.

**A zero here is real, and unexplained it reads as broken.** Of merge requests since June, 57.4%
produce a real elapsed figure, **31.6% were reviewed outside working hours** so no business time
elapsed, and **11.1% were reviewed before the author marked the MR ready** so no waiting time preceded
the review at all. `gitops-k8s-team-a2!2109` is the second kind: opened Wed 17:40, approved Wed 18:39,
marked ready Thu 11:07 — approved while still a draft, in the evening.

So the cell names which zero it is, `pre-ready` or `off-hours`, and every cell names the signal that
counted on hover. The panel note breaks the total down the same way — of 63 merge requests, 52 by
approval, 5 by comment, 6 by the merge alone — because "all 63 were reviewed" invites disbelief
otherwise, and it is worth being able to see that only 6 rest on the weakest signal.

## Visual hierarchy

The page reported as reading flat, and the cause was measurable rather than a matter of taste:

- `h2` and `h3` were 13px and 12px, both `--muted` at weight 600 — one pixel apart and identical in
  every other respect. A panel title was painted the *dimmest* ink on the page, the same colour as
  captions, hints and explainer prose. `h2` is now 14px in `--ink` at weight 700; `h3` is 11px and
  stays `--muted`, so the tiers separate on size, weight and brightness at once.
- `.stat`, `.fc` and `.panel` all used `var(--panel)`, so a score card sitting inside a panel had a
  1px border and nothing else dividing it from its own container. There are now three surfaces:
  `--panel`, `--head` (raised — a panel's title bar) and `--well` (recessed — anything holding a
  number).
- Panel titles are now bars rather than floating text: `.panel > h2` reaches back out through the
  panel's padding with negative margins and carries `--head` plus a bottom hairline.
- One accent (`--accent`, the `#5b9dff` already in the chart palette and already named `--blue` on
  two pages) marks only where the eye should land first: panel disclosure triangles, the eyebrow,
  focus rings, and the share bars in tables.

All five dashboards duplicate their own `<style>` and `:root`, so each of these had to be applied
five times, and two pages' token blocks had already diverged. `test_app_slas_route.py` pins the type
scale, the card surfaces, and that **no page references a token it does not declare** — a CSS
variable with no value fails silently, so nothing else would surface it. Extracting one shared
stylesheet is the real fix and is not done here.

## How a number gets made

1. **Jira poller** (`ingest.py`) — one full crawl, then incremental by `updated` watermark, plus
   each changed issue's changelog. Completion is the **earliest transition to `Done`**, not
   `resolutiondate`, which is null on ~85% of issues.
2. **GitLab poller** (`gitlab_ingest.py`) — merged MRs from the PE groups over a trailing window,
   plus each MR's changed file paths and its draft/ready/review events.
3. **Aggregation** — one module per view (`intake`, `delivery`, `velocity`, `leadtime`, `slas`,
   `mrflow`), each reading the store and returning JSON.
4. **Dashboards** — static HTML that fetch `/api/*` client-side.

Everything is a median or a nearest-rank percentile (`metrics.pctile`) over a windowed population;
there is no smoothing, weighting or modelling except the velocity forecast (a recency-weighted
average of the last three complete months) and the delivery forecast (Monte Carlo).

## Who merges what — and why it matters to the review metrics

PE engineers **merge their own MRs once another engineer has approved**. A self-merge is therefore
normal and is *not* evidence that nothing was reviewed — the approval is the review, and the merge
is the mechanic. Merge requests raised from **outside PE cannot be merged by the requester**;
PE merges them, so for that work the merge itself is the review action.

This is why review engagement cannot be read from comments alone: on the crawled sample 111 of 124
MRs carried an approval while only 23 drew a human comment.

**Time to first review** therefore takes the earliest of three signals
(`mrflow.first_review_at`):

| signal | what it means |
|---|---|
| comment | a human other than the author said something |
| approval | a colleague approved — the review for PE-authored MRs, since the self-merge follows it |
| merge by another | someone else pressed merge — the review for externally-raised MRs, which the requester cannot merge |

A **self-merge is not a signal on its own**: it is normal for PE and says nothing about whether
anyone looked. An **unknown merger** counts as a self-merge — absence of evidence is not review.

Counting comments alone covered 23 of 124 sampled MRs and reported a p90 of 5.4h; all three
signals cover 114 of 124 and report 22.7h, because the comment-only sample was the chatty, fast
minority.

## The turnaround clock

Turnaround used to be raw calendar elapsed time, which is what made a request filed at 16:00 and
closed at 09:00 next morning read as 17 hours.

One clock, everywhere: **business hours**, `metrics.business_hours_between` — Mon–Fri 08:00–17:00
`metrics.BUSINESS_TZ` (America/Los_Angeles), **excluding company holidays**. One business day is 9h
and one business week 45h, so `SLA_TARGETS_HOURS` reads directly in working days. Audacy's users are
overwhelmingly North American, so turnaround is judged against their working day. On the pilot data
this cut the iac-request median from 21.3 calendar hours to 6.3 business hours; the difference was
nights, weekends and holidays.

Raw calendar elapsed time is **not reported anywhere**. It bills a request for nights, weekends and
holidays nobody was working, which says nothing useful about delivery speed.

Holidays come from the `holidays` package, so the rules — including observance shifts, e.g.
Independence Day 2026 falls on a Saturday and is observed Friday 2026-07-03 — never go stale.
`metrics.OBSERVED_HOLIDAY_NAMES` decides which federal days Audacy actually closes for; it excludes
Washington's Birthday, Columbus Day and Veterans Day, which most private employers work. Add
company-specific closures (floating days, shutdown weeks) to `metrics.EXTRA_HOLIDAYS`.

One property to know when reading a single row of the per-author MR table: PE also has engineers
working EET, whose own working day falls inside the Pacific night, so an MR they open and merge
inside their own hours can score near 0.0 on the business clock.

Both aggregations use `metrics.window_start` — a rolling window floored at
`metrics.SELF_SERVICE_EPOCH` (2026-03-01), including the current month, unlike
`metrics.window_months` which yields complete months only and suits the month-bucketed charts. The
floor is measured, not assumed: the first MR carrying an agent footer opened 2026-03-18, and volume
ramps 6 (Mar) / 21 (Apr) / 74 (May) / 96 (Jun) / 211 (Jul).

The two views window differently, on purpose:

- **SLA (`slas.py`) — 3 months.** Delivery turnaround improved roughly 100x over the pilot (p50 by
  month created: 406.7h Apr, 114.0h May, 18.0h Jun, 13.9h Jul, 3.0h Aug), so six months calibrates
  against a team that no longer exists. Three keeps ~200 delivered requests, enough for a stable p90.
- **MR turnaround (`mrflow.py`) — 6 months.** Tracked back to the epoch, because the point of that
  table is the whole self-service era, not just the current quarter.

### The clock runs only while an MR is ready

`opened_at → merged_at` measured the wrong thing. Across the 20 slowest MRs, **67% of all
attributed hours were draft time** — one 612-hour MR was marked ready fifteen minutes before it
merged, and eight MRs from a single branch each carried 140.6h of which essentially all was draft.
An MR in draft is not waiting on review; the author is still working.

`mrflow.ready_hours` therefore accrues only the spells an MR spent marked ready, from GitLab's own
`marked this merge request as **draft**` / `**ready**` system notes, stored in `mr_events`. It
handles repeated toggling, and an MR whose first event is `ready` was opened as a draft so its
clock starts there. An MR with no events at all was never a draft and is measured whole.

Three signals were tested and rejected before landing on this one, and are worth not re-trying:

| signal | verdict |
|---|---|
| comment count (`user_notes_count`) | Spearman **+0.055** against turnaround — nothing. 37% of slow MRs have zero comments, and MRs with 11+ comments have a *median of 0.3h*: discussion means attention, and attention means merged. |
| commit timestamps | Unusable. Rebasing rewrites them — a three-week-old MR carried a single commit dated four hours before its merge, with squash off. |
| batching (many MRs per branch) | Real (37% of MRs share a branch, one produced 78) but not the driver: collapsing to one figure per branch leaves p90 unchanged at 18.0h. It distorts small samples, not the aggregate. |

**Time to first review** (`first_review`) rides the same ready-clock: the first note from a human
who is neither a bot nor the MR's own author. Bots are excluded by account name
(`gitlab_ingest._BOT_USERNAMES`) because GitLab Duo comments on essentially every MR and would make
each one look reviewed within seconds. This separates "nobody looked" from "reviewed, then
iterated" — the distinction the raw turnaround cannot make.

Reading the notes costs one extra API call per MR at ingest, doubling the per-MR calls (the crawl
already fetches changed paths). Completeness is tracked by `merge_requests.events_fetched_at`, not
by whether any events exist: an MR that was never a draft and drew no comments legitimately has
none, so keying on that would make the backfill run forever.

## SLA targets

The per-request-type SLA table was **dropped from the page** — the self-service view now shows
impact, MR turnaround by author, and daily turnaround. `slas_report` still computes `buckets`, and
`SLA_TARGETS_HOURS` still holds the targets, so restoring the panel is a dashboard change only.

`SLA_TARGETS_HOURS` holds **two tiers per bucket** rather than one number, because delivery
turnaround is bimodal: on August data 39% of requests closed inside 2h while the p90 sat at 29.8h. A
single "% under T" score blends those into a figure that is wrong about both ends. `p50` is what the
common case should hit; `p90` is the tail backstop. Each is scored on the distribution — bucket
median vs `p50`, bucket p90 vs `p90` — and `within_target_pct` is reported alongside as the "how
often does the fast path actually happen" read, against the `p50` target only.

Targets are calibrated to **August 2026** capability while the window is **3 months**, so most
buckets currently read as breaching. That is deliberate and not a fault: it shows the gap between a
good month and the trailing quarter, and it resolves itself as Jun/Jul age out of the window.

Actuals below are on the Pacific 08:00–17:00 clock (p50 / p90 business hours):

| bucket | p50 target | p90 target | 3-month actual | August actual |
|---|---|---|---|---|
| iac-request | 4h | 24h | 12.4h / 101.1h | 3.3h / 20.6h |
| troubleshoot | 2h | 16h | 3.6h / 26.0h | 1.9h / 14.7h |
| tf-module | 2h | 8h | 2.3h / 4.1h | 2.3h / 2.8h |
| other | 4h | 24h | 5.4h / 55.7h | 2.7h / 29.8h |

Widening the day from 8h to 9h nudged every figure up, so **tf-module's p50 now just misses its 2h
target on August data (2.3h)** where it previously met it. Targets have not been retuned for the new
clock — that is a team call.

One further lever, independent of any target: roughly **18% of median turnaround falls after the MR
merged** — work shipped, ticket still open (August p90 for that phase alone is 14h). An
auto-transition on merge would reduce every figure here for no engineering effort.

## Production vs non-production

`gitlab_domains.environment_of` reads the deployment environment from the repo name first, falling
back to the MR's changed file paths when the name says nothing. The check is **ordered and
token-based**, never a substring test, because `nonprod` contains `prod` — a naive `"prod" in name`
files every non-production repo as production.

The repo name wins outright: a repo called `tf-aardvark2-prod` deploys to production whatever
directory a change sits in. Paths only decide the cases the name cannot, which matters because
several repos hold both trees — `gitops-k8s-team-a2` keeps
`clusters/prod-fluxv2/namespaces/app/prod/...` beside its nonprod tree. Three ST-975 cutover MRs
there were filed as `other`, hiding a production coordination delay in an unclassified bucket. On
the crawled corpus paths classify **192 of the 955** otherwise-unknown MRs (104 prod, 88 nonprod),
roughly 8% of all merge requests.

An MR touching **both** trees is `mixed`: excluded from the environment views, because folding a
cross-environment change into either bucket misreports that bucket, and reported as a count so the
exclusion is visible rather than silent. Paths are fetched only for MRs whose repo name is silent,
not for every row. On the current corpus: 45 prod
repos / 737 MRs, 48 nonprod / 856, and 84 / 884 in neither. That third bucket is `other`, not a
failure: it covers dev/qa/shd repos and shared env-less ones like `gitops-k8s-team-a2` and
`tf-coreservices`, and folding it into either side would misreport both.

## Page controls

Two controls on `/slas`, both server-backed rather than cosmetic:

- **Lookback** — a preset that overrides the window on *every* panel, sent as
  `?since=YYYY-MM-DD&until=YYYY-MM-DD` to both `/api/slas` and `/api/mr-turnaround`. Presets are
  yesterday, last week, month to date, last month, last 30/60/90 days, and a custom from/to pair;
  "Panel defaults" means each panel keeps its own window (SLA 3 months, MR turnaround 6). Held in
  `localStorage` so it survives a refresh, and a stored bare date from before the presets is carried
  over as a custom range rather than dropped.

  `until` is **exclusive**, so two adjacent ranges cannot double count and a single day is expressed
  as `[day, day+1)`. Three rules keep the control honest rather than cosmetic:

  - **Presets resolve in the business timezone, not the browser's.** A colleague in EET picking
    "Yesterday" at 09:00 local is still hours behind Pacific midnight, so a browser-local computation
    would fetch a different day than everyone else sees. Resolved via
    `toLocaleDateString("en-CA", {timeZone: "America/Los_Angeles"})`.
  - **The custom from/to pair is hidden behind `.controls [hidden]{display:none}`**, which has to
    outrank `.filter{display:inline-flex}`. An author `display` rule beats the UA stylesheet's
    `[hidden]`, so on first cut the inputs stayed on screen while the code believed it had put them
    away — and a date typed there resolved against a select still reading "Panel defaults", which
    returns null, so the dates were silently dropped and the whole control looked inert. Editing
    either date now also switches the select to Custom, so typing a date can never be a no-op.
  - **`init()` wires every listener before its first load, and never returns early.** One missing
    element in one render used to throw out of `reload()`, which `init()` caught and returned from —
    so every listener after that point was never attached and the whole page went inert at once:
    lookback, author filter, environment filter, add and hide. The panels showed stale data with no
    error on them, so it read as "the picker does not work" rather than "a render crashed". A broken
    panel must cost that panel, not the page, and `test_app_slas_route.py` pins both the ordering and
    the render targets, since nothing else connects a `getElementById` to the markup carrying it.
  - **An inverted or zero-width window is a 400**, not an empty page. Empty panels read as "the team
    delivered nothing", which is a claim about the team rather than about the query.
  - **`until` bounds what is counted, not what is read** — in `slas.py` the merge-request query stays
    open-ended above, because it carries the footer, skill-label and first-review signals for each
    ticket as well as the capacity counts. A request created inside a "last month" window whose MR
    merged in August would otherwise lose those signals and be filed as non-AI, dropping it from the
    very panel it belongs in. The bound is applied in-loop to the capacity accumulation instead.
    `mrflow.py` bounds both ends in SQL, because there the population *is* MRs merged in the window.
- **MR authors** — add a GitLab username to the table, or hide a row. Persisted to
  `darkstar_mr_authors.json` beside the store (`mr_authors.py`), so edits are team-wide and survive
  restarts, exactly like the SME overrides. Hidden names are dropped from the rows *and* the team
  totals, so the total always describes what is on screen.

Adding an author is the one edit the store cannot serve on its own: the GitLab crawl is
incremental, so an author it has never attributed has no rows and an incremental pull will not
fetch their history. `POST /api/mr-authors` with `op: add` therefore clears the GitLab watermark
(`store.clear_gitlab_watermark`) to force a full-window crawl **and starts that crawl immediately**
as a background task, returning `recrawl_queued: true` so the page can say so.

What forces the full crawl is a **monotonic roster version**, not the watermark. Every add bumps
`version` in `darkstar_mr_authors.json`; a crawl reads the roster once at the start and records
*that* version when it finishes. An author added while a crawl is in flight therefore leaves
`version` ahead of what was recorded, so the next crawl is full and picks them up.

Recording the version at the *end* instead would be the bug: the crawl would stamp a version it
never actually crawled, the next crawl would go incremental, and the second author's existing merge
requests would never be fetched — silently, permanently, with no error anywhere. `_sync_scopes`
takes the roster as an argument rather than re-reading it for the same reason.

Starting it promptly matters too. The poller's default interval is `DARKSTAR_GITLAB_INTERVAL_SECONDS = 86400`,
so clearing the watermark alone meant a newly added author's merge requests might not appear for a
day — from the dashboard that is indistinguishable from the add having failed. The crawl is
serialized against the poller by the same `_write_lock`, and a failure is logged rather than
raised: the roster edit is already persisted, and the next scheduled crawl retries. A full
six-month crawl reads two GitLab API calls per merge request, so expect it to take minutes, not
seconds — the added author appears on the next page load after it finishes. Added authors are keyed in the store by their
**GitLab username** rather than a Jira accountId, precisely so they cannot leak into the
roster-gated views — velocity, capacity and the SME matrix all look up `ROSTER` by accountId and
simply miss.

## Month-over-month comparison

A period is flagged `partial` whenever the window or the clock cuts it short, and that means **both**
edges, not just the period in progress: a lookback starting mid-period (which "last 30 days" almost
always does, and a monthly window almost always does) truncates its first row, and a bounded range
truncates its last. A mid-week window over July showed 69 and 34 merge requests in its edge weeks
against ~120 for the full weeks between them — unflagged, that is a fabricated collapse at each end.

`requests_prev_month` is `None`, not `0`, whenever the window opens after the start of last month.
`created_by_month` only counts issues inside the window, so a lookback beginning on the 1st leaves
last month empty *by construction* — and the card rendered that as "up from 0", in green, which
reads as spectacular growth. It is a fact about the lookback, not the team. The card now says the
comparison is unavailable instead.

The same rule governs two other places, because the failure mode is always the same — an absence
rendered as a measurement:

- **Unread MR descriptions.** `description IS NULL` means the backfill has not read that MR yet, not
  that it lacks an agent footer. Counting those as hand-written would understate agent share by an
  amount that grows with crawl lag rather than with anything real, so they are counted into
  `unmeasured`, held out of the share denominator, and shown as a trailing `+n?`.
- **Schema nullability.** The ALTER-added `merge_requests` columns (`opened_at`, `labels`,
  `description`, `events_fetched_at`, `merged_by`) are nullable *on purpose*: NULL is the marker
  `_needs_backfill` selects on. `CREATE TABLE` had them `NOT NULL`, so a fresh store and a migrated
  one had different schemas — the deployed store held 117 NULL descriptions that a fresh store could
  not represent, which made the state untestable. The two paths are now aligned and
  `test_store_migration.py` pins them together, since nothing else enforces it.

## Population

A request counts iff it is a `metrics.DELIVERY_TYPES` issue (Story/Task/Bug/Hotfix/Sub-task) — a
Feature or Epic is a *container* for requests, and its months-long lifetime inflates the median
badly (it moved iac-request's p50 from 6.6h to 10.6h), which is why `leadtime`/`velocity`/`intake`
scope the same way.

Agent success counts terminal requests that are not abandoned. Note DEVOPS spells the abandon status
**`Will Not Do`**, not `Won't Do`; `_ABANDONED` holds both spellings plus Cancelled/Rejected, since
matching only the latter scored every abandoned request as a success and pinned the rate at 100%.

## Linking a request to its code

A request is tied to merge requests so the panels can read the MR's `pe:*` label, its agent footer and
its review timing. Four routes exist, and they are **not** equally believable, so they are tried
most-trusted-first and never unioned — and every link records which route found it.

| rank | route | source | why it ranks there |
|---|---|---|---|
| 1 | **Merge Request field** | Jira `customfield_11534` | a full GitLab MR URL somebody entered on purpose. Sampled clean: 18 of 18 were a single canonical `https://gitlab.com/<group>/<project>/-/merge_requests/<iid>` |
| 2 | **Branch name** | GitLab `source_branch` | generated from a convention, not typed as prose |
| 3 | **MR title** | GitLab | prose typed for another purpose; can be wrong |
| — | **MR description** | not read | `Supersedes DEVOPS-9001` is not a claim to have implemented it |

Coverage on the 221 requests created in August 2026: the Merge Request field is populated on **71**
(32%), Jira's development panel reports code on **105** (48%), and regex over titles and branches
reaches about 90 keys. The field catches **10** the development panel misses, so it is additive rather
than redundant. Neither Jira nor the regex is a superset of the other: on the same population Jira's
panel found **18** links the regex missed, and the regex found **9** Jira's panel did not.

**The description was dropped deliberately.** It was worth about two points of coverage and carried the
risk of marking an unrelated request self-service. A false positive corrupts a metric; a missing link
only leaves a request unclassified — and unclassified is now counted and shown, so the cheaper error is
the visible one.

The key pattern used for routes 2 and 3 is case-insensitive with a loose separator, because GitLab
humanises a branch into an MR title and mangles the key doing it: `Devops 9426`,
`Feature/devops 9257 prod cognito userpools`, `devops_10073`. A strict `DEVOPS-\d+` misses 48 of 5,878
merged MRs on those variants alone. It does **not** rescue a bare `feat(10117)` — nothing can, short of
matching every integer — which is why the branch is read at all.

### Reported, not hidden

The **Linked to code** strip under the impact cards breaks the count down by route, because a share
resting on a typed title deserves less weight than one resting on a URL somebody entered, and a reader
can only discount it if the split is visible. On a store whose syncs have not yet refilled the new
columns it reads *198 · MR title* and nothing else — which is the point.

Beside it, **in Jira, not matched here** counts requests whose development panel shows commits or a
pull request while none of our routes could name a merge request *this crawl has*. It is named for
whose gap it is, because there are three causes and they are not equally common:

| cause | reality | frequency |
|---|---|---|
| the merge request is not in this crawl | darkstar ingests only **merged** MRs from tracked authors and scopes, so an open MR, an outside author or an un-crawled project is invisible here | most of it |
| Jira linked it by commit message | a route not read here | some |
| there genuinely is no merge request | commits pushed to a branch, no MR ever opened | **6 of 221** August requests |

An earlier label read "code exists, not linked", which sounds like the last row and is mostly the
first. A hole in our own crawl is not a fact about the team.

**The Development field itself is not read.** `customfield_10400` serves a *cache*, and on DEVOPS-10117
it reported build count 5 where the panel showed 2, omitted the issue's merged pull request entirely,
and carried `"isStale": true`. A gap counter built on it reported "no pull request" for an issue that
had a merged one. JQL's `development[pullrequests]` index agreed with the panel, so the flags come from
**two extra JQL queries per sync** — one per predicate, since Jira rejects both `development[]` clauses
in a single query — scoped by the same JQL as the batch. `False` means Jira was asked and said no;
`None` means nobody asked, and only the first is evidence.

Note the panel labels these **Pull Request** even for GitLab, which is Jira's generic term; the JQL key
is `development[pullrequests]`.

### Migration

`source_branch` on `merge_requests` and `mr_field_url` / `dev_has_pr` / `dev_has_commits` on
`issues` are all nullable and ALTER-added. `_needs_backfill` forces one full GitLab re-crawl for the
branch; the Jira fields refill on the next issue sync. Until both run, the linkage strip will
correctly show every link coming from titles.

### Not attempted

A **page-wide prod/nonprod filter**. Even with every route, ~68% of requests cannot be classified by
environment, because about half link to no merged MR at all. The filter stays on the MR turnaround
panel, where environment is intrinsic to the merge request and nothing is lost. If a request-level
split is ever needed the answer is an environment field on the issue, not inference.

## Detecting a self-service request

Three independent signals, unioned — any one is enough, because each alone misses a slice:

| Signal | Where | Note |
|---|---|---|
| `pe-*` / `ai-generated` / `self-service` label | Jira issue | the `pe-*` labels only began 2026-05-27 |
| `pe:<skill>` label | linked GitLab MR | 302 of 2477 crawled MRs |
| `Generated with Claude Code` footer | linked GitLab MR description | 522 of 2477 — the widest signal, and it predates the labels by two months |

The MR is matched to the issue by the `DEVOPS-<n>` key in its title. On the crawled corpus the
footer alone catches **254 MRs the label misses**, and **127 issues** carrying no `pe-*` label at
all — against 135 found by labels alone. The footer usually also names the originating skill
(`via /iac-request`), which buckets the request when no `pe:` label is present.

Bucketing takes the MR's `pe:` label first, then the footer's skill, then the Jira label. Note the
Jira fallback cannot separate k8s from iac (`pe-tf-module` issues also carry `pe-iac-request`), and
15 crawled MRs carry the label as the literal string `["pe:iac-request"]` — a quoting bug in
whatever sets it, tolerated in `_MR_BUCKET_BY_LABEL` but still worth fixing at the source.

MR turnaround needs `merge_requests.opened_at` and `description` on every in-window row. Because
the GitLab crawl is incremental, rows written before those columns existed — and MRs by an author added to `MR_AUTHORS`
later — cannot be repaired by an incremental pull, so `gitlab_ingest._needs_backfill` forces **one**
full-window re-crawl while any in-window row is missing either, then returns to incremental.
Descriptions are stored verbatim (~1.3 MiB for the whole corpus) so the footer heuristics can be
retuned without another crawl.

## Architecture

- **Store** (`store.py`) — one DuckDB file (on a PVC in prod). Tables: `issues`,
  `transitions` (append-only status changes from changelogs), `sync_meta`, and — for the SME
  matrix — `merge_requests` + `mr_files`. All timestamps are naive UTC.
- **Jira poller** (`ingest.py`) — a one-time full crawl on the first run, then **incremental only**
  (`updated >= watermark`, no periodic full reconcile) plus a per-changed-issue changelog;
  completion is measured as the earliest transition to `Done` (resolutiondate is null on ~85% of
  issues), attributed to the business month (`metrics.BUSINESS_TZ`).
- **GitLab ingest** (`gitlab_ingest.py`) — a one-time full **6-month** crawl on the first run, then
  **incremental** pulls of only the MRs updated since the last sync (watermark in `gitlab_sync_meta`,
  minus a small margin), from the PE groups `audacy-inc/devops` + `audacy-inc/gcp`, plus a few
  tracked repos that live outside those groups (`_PE_PROJECT_IDS`, e.g. `tf-org`/`tf-org-v2` under
  secops). Each MR is
  attributed to a tracked author (`roster.MR_AUTHORS` = the PE roster plus `TRACKED_MR_AUTHORS`,
  non-roster contributors whose MR turnaround is measured but who must stay out of the roster-gated
  velocity/capacity/SME views) and its changed file paths stored;
  `gitlab_domains.py` tags each MR to expertise domains from its **repo + changed file paths**
  (not the diff contents or the MR description) — a far denser signal than Jira titles.
- **App** (`app.py`) — read-only `/api/*` endpoints, `/health`, and the one write path,
  `POST /api/overrides` (shared SME overrides, see below).

## How intake works

The intake queue recommends **who should pick up each unassigned ticket** by combining two
independent per-engineer signals and routing on skill first, availability second.

### Availability — spare capacity

`spare = max(0, velocity - done_this_month) - WIP`

- **velocity** is each engineer's typical monthly output — the recency-weighted average of their
  completed Jira tickets over the last three complete months (`velocity._forecast`'s `baseline`).
  The velocity dashboard additionally blends this toward the current month's pace for its own
  display, but intake intentionally uses the unblended baseline so this term does not double-count
  `done_this_month`.
- **done_this_month** (completions so far) and **WIP** (active in-progress tickets) are subtracted,
  so the number is month-to-date headroom rather than a static monthly figure.
- The capacity gauge plots `done` + WIP against the velocity tick; the 1–4 signal bars (red→green)
  shown on each suggestion encode this spare capacity.

### Expertise — the GitLab-derived domain signal (the bespoke part)

This is what makes the routing accurate. Jira ticket titles are terse and inconsistent, so tagging
them recognizes a domain in only **~63%** of the work. The primary expertise signal instead comes
from **what engineers actually build**, read from GitLab:

- For every merged MR by a tracked author (a 6-month baseline on the first sync, then kept current
  by incremental syncs, from the PE groups plus a few tracked repos), we fetch the MR's **changed
  file paths** — not the diff contents, and not the MR title/description — capped at 60 paths per MR.
- `gitlab_domains.py` tags each MR to expertise domains with regex over the **repo name + those
  file paths**. For the IaC / GitOps / config work that is most of PE's output, the directory
  layout *is* the taxonomy: `.../eks-nodegroups/.../terragrunt.hcl` → EKS + Terraform;
  `clusters/.../helmrelease.yaml` → Kubernetes/GitOps; the `tf-sharedservices` repo → Route53;
  `tf-org` → IAM/RBAC.
- Each domain is counted **once per MR** (so a 300-file refactor can't dominate), and those counts
  are added to the Jira-title counts to form each engineer's per-domain `+N` skill score.

**Why it's accurate:** file paths are a dense, standardized signal, so **98.4%** of MRs tag to at
least one domain (~3.3 domains per MR) — versus ~63% from Jira titles. The expertise picture is
near-complete and reflects hands-on authorship, not merely who a ticket was assigned to.
**Tradeoff:** a path tells you *where* a change lives, not *what* it did — a one-line fix in an EKS
file still counts as EKS work.

### Domains and overrides

Domains are grouped **AWS / GCP / Other** (alpha-sorted within each, group-collapsible), with
specialized services (EKS, GKE, ECS, OpenSearch, MSK, Route53, VertexAI, Kubeflow Pipelines,
Bedrock Agents, AI Plugins) pulled out of the coarse `AWS Core` / `GCP Core` buckets, plus
3rd-party tools called out on their own (e.g. **Fastly**, a CDN, grouped under Other). Per domain
the top scorer is the **SME** and the next are **runners-up**. The lead can override the
SME/runner-up per domain from the panel above the matrix; overrides persist to a shared JSON on the
PVC (`overrides.py`) so they are team-wide.

### Putting it together

For an unassigned ticket, its summary is tagged to a **primary domain**; the suggestion is that
domain's **SME**, plus a **runner-up** and a **stretch** pick, ranked by domain skill (`+N`) first
and spare capacity second. A generic ticket that matches no domain falls back to availability alone.

## Run locally

```bash
pip install -r darkstar/requirements.txt

# read-only (dashboards) — points at an existing store
export DARKSTAR_DB_PATH=/path/to/darkstar.duckdb
uvicorn darkstar.app:app --port 8080

# Jira poll (populates issues/transitions) — needs Jira creds
export JIRA_SERVER=... JIRA_EMAIL=... JIRA_API_TOKEN=...
python -m darkstar.ingest              # or call ingest.run_sync

# GitLab MR crawl (populates merge_requests/mr_files) — needs a GitLab token
export GITLAB_TOKEN=...                 # locally: $(glab config get token -h gitlab.com)
python -c "from darkstar import store, gitlab_ingest; from datetime import datetime, timezone; \
c=store.connect('$DARKSTAR_DB_PATH'); store.initialize_schema(c); \
gitlab_ingest.run_gitlab_sync(c, datetime.now(timezone.utc).replace(tzinfo=None), 180)"
```

Env: `DARKSTAR_DB_PATH` (store path), `DARKSTAR_OVERRIDES_PATH` (SME overrides JSON; defaults
alongside the store), `DARKSTAR_POLL_INTERVAL_SECONDS` (Jira poll cadence),
`DARKSTAR_GITLAB_INTERVAL_SECONDS` (GitLab poll cadence).

## Deploy

darkstar ships **inside the pe-reports image**, not a separate one. The root `Dockerfile` installs
Streamlit into the system environment (pe-reports, unchanged) and darkstar into an isolated
`/opt/darkstar-venv`; `docker-entrypoint.sh` runs Streamlit by default and uvicorn/darkstar when
`APP_ENTRYPOINT=darkstar`. The two dependency sets can't co-resolve in one environment (pe-reports
pins `starlette==1.0.0`, darkstar's `fastapi` needs `starlette<0.42`), which is what forces the
venv split. So the existing `publish:pe-reports` job builds one image for both apps — there is no
separate darkstar image or pipeline. The **in-process Jira + GitLab pollers** launch from app
startup, share the store connection under a write-lock, and are each skipped if their secret is
absent; `/health` is independent of the store so probes pass during the first crawl.

darkstar's workload is a HelmRelease in `gitops-k8s-team-a2` (dev namespace) — a **1-replica
StatefulSet + gp3 PVC** at `DARKSTAR_DB_PATH`, `/health` probes, ingress, `APP_ENTRYPOINT=darkstar`,
and `image.repository` pointed at the shared `pe-reports` repo (its ImagePolicy resolves the same
tag — it's the same image). The a2 statefulSet allows a single secret, so darkstar reuses the
`pe-reports` secret for Jira creds; `GITLAB_TOKEN` is added to that same secret to enable the
GitLab poller. Because both apps share one image + tag stream, a rebuild redeploys both.
