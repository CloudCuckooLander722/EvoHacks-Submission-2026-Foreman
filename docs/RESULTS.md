# Live run results

Real numbers from live runs on the hero dataset (Medium app reviews, see [`data/hero/README.md`](../data/hero/README.md)). Use the **latest** numbers (C3 and D3, top section) on the site and in the video; don't round them up.

All runs: threshold 0.7, Jev via Vercel AI Gateway, LLM fallback DeepSeek V4 Flash on Fireworks ($0.22 / $0.66 per M tokens), plan `filter → classify → group → dedupe → score → rank → calculate` (`data/plans/reviews_backlog.json`).

**Jev %** is Jev's share of model decisions, Jev / (Jev + LLM). Code operations (ranking, counting) are listed separately. The confidence curve in each results.json shows how the split moves with the threshold.

## Latest: group `sample_size` 400 (Sun Oct 11, 09:50–10:17 PDT)

One change to `data/plans/reviews_backlog.json`: group now proposes its themes from 400 reviews instead of the default 60. Then the 1k API run (C3, now the `reviews-1k` replay) and the 10k CLI run (D3) were re-run.

**Unthemed share (`none_of_these` among bugs and feature requests)**

| Run | Before (sample 60) | After (sample 400) |
|---|---|---|
| 10k | 1,062 of 2,596 (**40.9%**, run D2) | 548 of 2,610 (**21.0%**, run D3) |
| 1k via API | 82 of 253 (**32.4%**, run C2) | 84 of 253 (**33.2%**, run C3) |

- At 10k the share halved. Group's outlier pass ran on 732 items instead of 1,334, and the largest themes are now UI/display issues (351), Writing/editing removed (258), Login failures (228) and Paywall restrictions (214).
- At 1k nothing changed. There are only 253 bugs and feature requests, so 60 was already a large share of them. A sample of the 84 unthemed items is a mix: vague complaints ("after the last update, nothing works!"), specific one-offs no theme covers (region access, a missing launcher icon, no "series" button), and some that should have matched an existing theme (stuck on the loading screen).

**Headline numbers, before → after**

| | 1k via API (C2 → C3) | 10k via CLI (D2 → D3) |
|---|---|---|
| Time | 85.6 s → **122.5 s** | 773.3 s (12.9 min) → **1,287.8 s (21.5 min)** |
| Decisions (Jev / LLM / code) | 2,965 (2,353 / 607 / 5) → **2,927 (2,309 / 613 / 5)** | 34,161 (26,104 / 8,052 / 5) → **35,035 (26,759 / 8,271 / 5)** |
| Jev % | 79.5% → **79.0%** | 76.4% → **76.4%** |
| Cost | $0.0942 → **$0.0968** | $1.1590 → **$1.1966** |
| Low-confidence decisions | 2 → 1 | 32 → **104** |

**The slower wall times are the LLM provider, not the plan.** Filter runs before group and is unchanged, but at 10k it took 440.0 s against 226.4 s overnight, and classify took 237.2 s against 166.3 s. The log shows 104 LLM fallback timeouts (8 s each) against 32 overnight; each timeout keeps Jev's answer as low-confidence. Dedupe also did more work (5,185 pairs, 439.9 s, against 4,456 and 263.9 s), because fewer bugs are unthemed and skipped (363 against 546). Costs moved by 3%.

**For the site** (`web/src/content/numbers.ts`, Minh's file; I didn't edit it): the current plan's recorded numbers are 10,000 reviews in **21.5 min** for **$1.20**, **35,035** decisions (35,030 model), **76.4%** by Jev; and for the replayed 1k run (C3, `r_76889711`), **122.5 s**, **$0.0968**, **79.0%** by Jev. If you'd rather keep the faster 12.9 min / $1.16 figures, they are also real (run D2), but from the plan with `sample_size` 60; label them that way.

The new replay bundle includes Minh's exports (`export.xlsx`, `export.pdf`, `export.docx`) and his `aggregates.summary`. This was the first API run through his `extract()`, `summarize()` and `export()`, and all three worked.

### C3. 1k reviews through the API (replay `reviews-1k`)

`r_76889711` · 1,000 in → 956 kept (44 dropped) · SSE: 1 `plan`, 614 `node`, 716 `decision`, 385 `stats`, 1 `done`, no errors · exports xlsx 145 KB, pdf 114 KB, docx 123 KB

| Node | Items | Jev | LLM | Code | Time |
|---|---|---|---|---|---|
| n1 filter | 1000 → 956 | 731 | 269 | 0 | 35.9 s |
| n2 classify | 956 | 831 | 125 | 0 | 24.1 s |
| n3 group | 253 bugs + requests | 305 | 59 | 0 | 25.8 s |
| n4 dedupe | 185 bugs → 144 unique (24 clusters, largest 4); 191 pairs; 63 unthemed skipped | 248 | 134 | 2 | 22.8 s |
| n5 score | 185 bugs | 160 | 25 | 1 | 6.8 s |
| n6 rank | 144 | 34 | 0 | 145 | 4.9 s |
| n7 calculate | 956 | 0 | 0 | 4 | 0.0 s |

Top 5: r-1555 "app always make my phone crush" (100.0, 2 reports) · r-2985 "ram hungry, gets killed" (100.0) · r-1142 "5 seconds into my first article the app crashed" (100.0, 3) · r-6784 "update -> no more black theme, lost all the history of reading and bookmarks" (100.0) · r-1486 "crashing… as early as the login page" (99.7).

Types: praise 405, bug 185, other 174, billing 124, feature_request 68. Average bug urgency 63.0.

Themes: none_of_these 84, Login failures 22, Paywall and free limits 21, Writing feature removed 19, Slow performance 15, App crashes 14, Content Organization 14, Offline reading missing 11, Draft management issues 9, Email verification loops 9, Font size and display 7, Link Handling 7, Scrolling issues 7, Dark mode problems 6, Sign-up problems 6, Screen Rotation 2.

### D3. 10k reviews, CLI

`r_993167a8` · 09:55–10:17 PDT · log `runs/hero10k.log` (previous runs: `runs/hero10k_v2.log`, `runs/hero10k_before.log`) · 10,000 in → 9,501 kept (499 dropped)

| Node | Items | Jev | LLM | Code | Time |
|---|---|---|---|---|---|
| n1 filter | 10000 → 9501 | 7,378 | 2,622 | 0 | 440.0 s |
| n2 classify | 9501 | 8,143 | 1,358 | 0 | 237.2 s |
| n3 group | 2,610 bugs + requests | 2,604 | 740 | 0 | 134.8 s |
| n4 dedupe | 1,777 bugs → 1,080 unique (344 clusters, largest 14, 736 singletons); 5,185 of 23,948 similar pairs; 363 unthemed skipped | 7,067 | 3,303 | 2 | 439.9 s |
| n5 score | 1,777 bugs | 1,529 | 248 | 1 | 31.0 s |
| n6 rank | 1,080 | 38 | 0 | 1,081 | 4.5 s |
| n7 calculate | 9501 | 0 | 0 | 4 | 0.0 s |

Cost: Jev $0.5709 + LLM $0.6256 = **$1.1966** (29,824 Jev calls, 8,271 LLM calls).

Top 5: r-302 "it's crashing on opening!" (100.0, 12 reports) · r-790 "losing credentials on upgrade is not a good practice" (100.0) · r-948 "bookmark is not working, so i lost all articles that i've read…" (100.0, 3) · r-1481 "new updated version is not starting and is crashing during app launch" (100.0, 3) · r-1604 "when i try to save there is an exception. i lost it all" (100.0). The next five are also 100.0: r-120 "the app will freeze up", r-2985, r-3816, r-3980, r-5696 "edit the story it throws 500".

Types: praise 4,149, bug 1,777, other 1,485, billing 1,257, feature_request 833. Average bug urgency 63.3.

Themes (2,610): none_of_these 548, UI/display issues 351, Writing/editing removed 258, Login failures 228, Paywall restrictions 214, Feed/content problems 162, Search/navigation issues 156, App crashes 141, Performance problems 135, Offline reading missing 113, Missing writing features 74, Notification issues 56, Sign-up problems 56, Account verification issues 51, Regional availability issues 43, Commenting requires app 24.

## Overnight summary (for Nathan and Minh)

Sun Oct 11, 00:10–00:36 PDT. Fixes for the "Known quality issues" found in the first overnight runs, then re-runs of the 1k API run and the 10k run.

**What changed**

- **Dedupe** (`foreman/tools/dedupe.py`):
  - No more chaining. An item joins a cluster only if it was matched directly with that cluster's canonical item. Matched pairs are processed strongest first, and the founding pair's more complete record becomes the canonical. `dup_group_canonical` / `dup_group_count` mean the same as before.
  - New `max_neighbors` (default 5): each item is paired only with its 5 most similar candidates in its block.
  - New `skip_blocks` (default `["none_of_these"]`): items in those blocks are not compared. They are written as singletons (canonical, count 1), so the backlog still ranks them.
  - Six new tests.
- **Hero plan:**
  - The filter's `true_means` now also covers "comments about the articles, writers or other content in the app".
  - Group's `n_themes` went from 8 to 12.

**Before → after**

| Run | Time | Decisions (Jev / LLM / code) | Jev % | Total cost | Dropped by filter | Largest duplicate group | Bugs → unique |
|---|---|---|---|---|---|---|---|
| 1k via API (`reviews-1k` replay) | 117.1 s → **85.6 s** | 3,349 (2,559 / 785 / 5) → **2,965 (2,353 / 607 / 5)** | 76.5% → **79.5%** | $0.1069 → **$0.0942** | 83 → **49** | 27 → **5** | 182 → 94 before, 186 → **131** after |
| 10k via CLI | 1,655.2 s (27.6 min) → **773.3 s (12.9 min)** | 64,893 (47,087 / 17,801 / 5) → **34,161 (26,104 / 8,052 / 5)** | 72.6% → **76.4%** | $2.2456 → **$1.1590** | 769 → **497** | 439 → **14** | 1,775 → 450 before, 1,771 → **1,126** after |

- **The 10k run is now twice as fast and half the price.** Dedupe went from 1,099.5 s and 40,002 decisions to 263.9 s and 8,914 decisions. It now checks 4,456 pairs (each bug's 5 closest), against 20,000 before, and no longer hits `max_pairs`.
- **Duplicate counts are now believable.** At 10k the biggest group is 14 crash-on-open reports, and 834 of the 1,126 unique bugs stand alone. Before, whole themes were merged into groups of up to 439.
- **The filter keeps more real reviews:** 49 dropped per 1k (was 77–103) and 497 per 10k (was 769). The type counts now include the article praise it used to drop.
- **Not fixed: unthemed bugs.** `none_of_these` is still 1,062 of 2,596 bugs and feature requests (41%) at 10k, and 82 of 253 (32%) on the new 1k run. Raising `n_themes` to 12 (15 after refinement) did not help. See "Known quality issues".
- **The site's replay was regenerated** with `r_e79c8b43` (since replaced by C3, `r_76889711`).

**Headline numbers at that point** (superseded by the latest section): 10,000 reviews in 12.9 minutes for **$1.16**, 34,161 decisions, **76.4%** made by Jev. 1,000 reviews in 86 seconds for **9.4 cents**, 79.5% by Jev.

**Choices I made without asking**

- `max_neighbors` keeps a pair if it is in the top 5 of *either* of its items, so an item can end up in more than 5 pairs. `min_similarity` still applies as a floor.
- `max_neighbors: null` turns the limit off.
- A cluster's canonical is fixed when the cluster is founded. Before, it was the most complete member of the final cluster; now it is the more complete of the two founding items, since every member must be matched directly with it.
- Skipped-block items are written as singletons, not left without dedupe fields. Otherwise rank's `dup_group_canonical` filter would silently drop every unthemed bug from the backlog.
- Only the API 1k run and the 10k run were redone, as asked. Runs A and B below are from before the fixes; I didn't re-run them.

## After the dedupe fix (sample_size 60)

### C2. 1k reviews through the API (the second `reviews-1k` replay, since replaced by C3)

`r_e79c8b43` · `POST /runs` with `reviews_1k.json`, `plan=reviews_backlog` · 1,000 in → 951 kept (49 dropped) · was bundled at `web/public/replays/reviews-1k/` until C3

The SSE stream sent 1 `plan`, 511 `node`, 601 `decision` (sampled), 281 `stats` and 1 `done` event, and no errors.

| Node | Items | Jev | LLM | Code | Time |
|---|---|---|---|---|---|
| n1 filter | 1000 → 951 | 736 | 264 | 0 | 23.7 s |
| n2 classify | 951 | 823 | 128 | 0 | 23.5 s |
| n3 group | 253 bugs + requests | 348 | 59 | 0 | 16.0 s |
| n4 dedupe | 186 bugs → 131 unique (34 clusters); 192 pairs checked; 58 unthemed bugs skipped | 253 | 131 | 2 | 14.8 s |
| n5 score | 186 bugs | 161 | 25 | 1 | 3.4 s |
| n6 rank | 131 | 32 | 0 | 132 | 4.1 s |
| n7 calculate | 951 | 0 | 0 | 4 | 0.0 s |

Cost: Jev $0.0524 + LLM $0.0419 = **$0.0942**.

Top 5 ranked bugs ("reports" = duplicate group size):

| # | Id | Urgency | Reports | Review |
|---|---|---|---|---|
| 1 | r-1555 | 100.0 | 2 | "good content, but tech team very very bad. app always make my phone crush" |
| 2 | r-2985 | 100.0 | 1 | "terrible app… seems to be very ram hungry so it gets killed off…" |
| 3 | r-1142 | 100.0 | 2 | "5 seconds into my first article the app crashed." |
| 4 | r-291 | 99.3 | 4 | "please don't release updates that cause your app to crash and make it unusable." |
| 5 | r-1341 | 97.0 | 2 | "…installing this app crashes my stock launcher repeatedly…" |

Types: praise 405, bug 186, other 169, billing 124, feature_request 67. Average bug urgency 62.9.

Themes (253 bugs and feature requests): none_of_these 82, Missing App Features 33, Writing and Editing Broken 25, Login Failure 19, App Performance Lag 17, App Crashes 12, Article Limit 12, Offline Mode Missing 10, Email Link Error 8, Recommendation Algorithm Poor 8, Sign-up Failure 8, Reading History Missing 7, Dark Mode Unreadable 4, Social Login Issues 4, App Size Large 2, Data Usage High 2.

### D2. 10k reviews, CLI

`r_34e67484` · Sun Oct 11 00:23–00:36 PDT · log `runs/hero10k_v2.log` · 10,000 in → 9,503 kept (497 dropped) · 32 low-confidence decisions

| Node | Items | Jev | LLM | Code | Time |
|---|---|---|---|---|---|
| n1 filter | 10000 → 9503 | 7,340 | 2,660 | 0 | 226.4 s |
| n2 classify | 9503 | 8,132 | 1,371 | 0 | 166.3 s |
| n3 group | 2,596 bugs + requests | 3,270 | 662 | 0 | 79.1 s |
| n4 dedupe | 1,771 bugs → 1,126 unique (292 clusters); 4,456 of 22,758 similar pairs checked; 546 unthemed bugs skipped | 5,802 | 3,110 | 2 | 263.9 s |
| n5 score | 1,771 bugs | 1,522 | 249 | 1 | 32.4 s |
| n6 rank | 1,126 | 38 | 0 | 1,127 | 4.8 s |
| n7 calculate | 9503 | 0 | 0 | 4 | 0.0 s |

Cost: Jev $0.5719 + LLM $0.5871 = **$1.1590** (29,679 Jev calls, 8,052 LLM calls), about $0.034 per 1,000 decisions. Against the new 1k API run, 10× the items took 9.0× the time and 12.3× the cost. Dedupe is now 34% of the run time (was 66%).

Duplicate groups among the 1,126 unique bugs: 834 singletons; 140 groups of 2, 77 of 3, 31 of 4, 19 of 5; the rest are 6–14 (largest 14).

Top 5 ranked bugs:

| # | Id | Urgency | Reports | Review |
|---|---|---|---|---|
| 1 | r-294 | 100.0 | 5 | "…the app crashes as soon as it loads on my samsung s4." |
| 2 | r-302 | 100.0 | 13 | "it's crashing on opening!" |
| 3 | r-77 | 100.0 | 2 | "after the recent update, i lost the draft i'd saved through the phone…" |
| 4 | r-838 | 100.0 | 3 | "…this app got automatically close while doing scroll up or down…" |
| 5 | r-1604 | 100.0 | 2 | "i spent half an hour writing an article. when i try to save there is an exception. i lost it all…" |

The next five: r-1904 "switched to being a member and it's gotten rid of all my bookmarks", r-2153 "highlights don't remain highlighted", r-790 "losing credentials on upgrade", r-2985 "ram hungry, gets killed", r-3980 "auto refresh erased my work in progress", all 100.0. The order among the 100.0s comes from Jev's tie-break comparisons (19 run, 17 swapped).

Types: praise 4,143, bug 1,771, other 1,490, billing 1,274, feature_request 825. Average bug urgency 63.1.

Themes (2,596 bugs and feature requests): none_of_these 1,062, Login failure 261, Editing limitations 254, Content not loading 197, Free article limit complaints 129, App crashes and freezes 125, Slow loading and lag 106, Scrolling issues 84, Publishing failure 82, Offline reading request 78, Signup failure 69, Commenting issues 42, App won't open 38, Notification behavior issues 33, Image display problems 27, Zoom not working 9.

## Before the fixes (Sat Oct 10, 23:30 – Sun Oct 11, 00:06 PDT)

These runs used the old dedupe (transitive union-find, all pairs above `min_similarity`), the old filter wording and `n_themes: 8`.

| Run | Items | Time | Decisions (Jev / LLM / code) | Jev % | Jev cost | LLM cost | Total cost |
|---|---|---|---|---|---|---|---|
| A. 1k, hand-written plan (CLI) | 1,000 | 112.8 s | 3,895 (3,074 / 816 / 5) | 79.0% | $0.0570 | $0.0575 | $0.1144 |
| B. 1k, planner (CLI) | 1,000 | 122.4 s | 3,507 (2,686 / 816 / 5) | 76.7% | $0.0549 | $0.0608 | $0.1158 |
| C. 1k, hand-written plan (API) | 1,000 | 117.1 s | 3,349 (2,559 / 785 / 5) | 76.5% | $0.0512 | $0.0557 | $0.1069 |
| D. 10k, hand-written plan (CLI) | 10,000 | 1,655.2 s (27.6 min) | 64,893 (47,087 / 17,801 / 5) | 72.6% | $0.8331 | $1.4125 | $2.2456 |

Run B's time includes planning (one LLM call). In these runs the filter sent about 29% of its decisions to the LLM and dedupe about 30%; classify (13%), score (12%) and rank (0%) mostly stayed with Jev.

### A. 1k reviews, hand-written plan, CLI

`r_222e777d` · 1,000 in → 923 kept (77 dropped by the filter)

| Node | Items | Jev | LLM | Code | Time |
|---|---|---|---|---|---|
| n1 filter | 1000 → 923 | 705 | 295 | 0 | 26.0 s |
| n2 classify | 923 | 800 | 123 | 0 | 21.3 s |
| n3 group | 253 matched | 245 | 65 | 0 | 22.6 s |
| n4 dedupe | 185 bugs → 102 unique (25 clusters) | 1,127 | 311 | 2 | 33.5 s |
| n5 score | 185 bugs | 163 | 22 | 1 | 3.6 s |
| n6 rank | 102 | 34 | 0 | 103 | 5.4 s |
| n7 calculate | 923 | 0 | 0 | 4 | 0.0 s |

Top 5 ranked bugs (urgency 0–100; ties within 5 points are broken by Jev pairwise checks; "reports" = duplicate group size):

| # | Id | Urgency | Reports | Review |
|---|---|---|---|---|
| 1 | r-1486 | 99.7 | 1 | "does everything i need, which is crashing, and crashing and crashing as early as the login page…" |
| 2 | r-291 | 99.3 | 3 | "please don't release updates that cause your app to crash and make it unusable." |
| 3 | r-1341 | 96.7 | 1 | "…installing this app crashes my stock launcher repeatedly. i was forced to uninstall it…" |
| 4 | r-1142 | 100.0 | 5 | "5 seconds into my first article the app crashed." |
| 5 | r-1866 | 94.0 | 4 | "often when i open the app the story i was reading is gone and there is no history feature to find it again." |

Types: praise 396, bug 185, other 155, billing 119, feature_request 68. Average bug urgency 62.9.

Themes (bugs and feature requests, 253 items): UI and Feature Limitations 115, none_of_these 37, Login and Signup Failures 31, App Crashes and Freezes 13, Recommendation and Content Filtering 12, Offline Reading and Saving 9, Scrolling and Performance Problems 9, Reading History and Tracking 7, Update-Related Breakage 6, Content Loading Failures 5, Social Media Login Issues 5, VPN and Region Access 4.

### B. 1k reviews, planner, CLI

`r_f5198729` · job: *"Drop spam, sort the feedback into bugs, feature requests, praise and billing, group bugs and requests into themes, merge duplicate bugs, and rank bugs by urgency"* · 1,000 in → 897 kept (103 dropped)

The planner chose the same seven tools as the hand-written plan. Plan saved at `runs/hero1k_planner_plan.json`.

| Node | Items | Jev | LLM | Code | Time |
|---|---|---|---|---|---|
| n1 filter | 1000 → 897 | 709 | 291 | 0 | 34.4 s |
| n2 classify | 897 | 777 | 120 | 0 | 17.0 s |
| n3 group | | 299 | 62 | 0 | 14.1 s |
| n4 dedupe | | 707 | 319 | 2 | 40.4 s |
| n5 score | | 160 | 23 | 1 | 7.5 s |
| n6 rank | | 34 | 0 | 94 | 4.1 s |
| n7 calculate | | 0 | 0 | 4 | 0.0 s |

Top 5 ranked bugs:

| # | Id | Urgency | Reports | Review |
|---|---|---|---|---|
| 1 | r-2985 | 100.0 | 1 | "terrible app… seems to be very ram hungry so it gets killed off…" |
| 2 | r-291 | 99.3 | 1 | "please don't release updates that cause your app to crash and make it unusable." |
| 3 | r-9242 | 99.3 | 1 | "constantly crashes in the middle of reading an article, then you must dig up your reading history…" |
| 4 | r-1341 | 97.3 | 1 | "…installing this app crashes my stock launcher repeatedly…" |
| 5 | r-1142 | 100.0 | 5 | "5 seconds into my first article the app crashed." |

Types: praise 380, bug 183, other 144, billing 123, feature_request 67. Average bug urgency 63.1.

Themes: none_of_these 68, Login and Signup Issues 36, App Performance and Speed 30, Subscription and Premium Features 24, Editing and Draft Issues 23, Missing App Features 21, Content Recommendations and Feed 12, Writing and Publishing on App 12, Offline Reading Problems 10, Bookmark Management 6, Link Handling and External Browser 6, App Size and Storage 2.

### C. 1k reviews through the API (the first `reviews-1k` replay, since replaced by C2)

`r_8e62399f` · `POST /runs` with `reviews_1k.json`, `plan=reviews_backlog` · 1,000 in → 917 kept (83 dropped) · was bundled at `web/public/replays/reviews-1k/` until C2 replaced it

The SSE stream sent 1 `plan`, 688 `node`, 836 `decision` (sampled), 382 `stats` and 1 `done` event, and no errors.

| Node | Items | Jev | LLM | Code | Time |
|---|---|---|---|---|---|
| n1 filter | 1000 → 917 | 710 | 290 | 0 | 37.0 s |
| n2 classify | 917 | 797 | 120 | 0 | 18.9 s |
| n3 group | | 206 | 75 | 0 | 16.2 s |
| n4 dedupe | | 651 | 277 | 2 | 36.1 s |
| n5 score | | 159 | 23 | 1 | 3.7 s |
| n6 rank | | 36 | 0 | 95 | 5.1 s |
| n7 calculate | | 0 | 0 | 4 | 0.0 s |

Top 5 ranked bugs:

| # | Id | Urgency | Reports | Review |
|---|---|---|---|---|
| 1 | r-291 | 99.3 | 24 | "please don't release updates that cause your app to crash and make it unusable." |
| 2 | r-2985 | 100.0 | 1 | "terrible app… seems to be very ram hungry so it gets killed off…" |
| 3 | r-1341 | 97.0 | 2 | "…installing this app crashes my stock launcher repeatedly…" |
| 4 | r-1866 | 93.7 | 2 | "often when i open the app the story i was reading is gone and there is no history feature…" |
| 5 | r-6700 | 72.7 | 1 | "the app doesn't open up. the splash screen appears forever." |

Types: praise 390, bug 182, other 159, billing 119, feature_request 67. Average bug urgency 63.3.

Themes: Feature Requests and Missing Functionality 79, App Performance and Stability 49, Login and Sign-in Issues 28, User Interface and Display Issues 23, none_of_these 14, Article Reading Limits 11, Content Recommendations and Feed 11, Offline Reading and Saving 10, Sign-up and Account Creation Problems 10, Article Content Not Loading 5, Post Creation and Publishing Failures 5, VPN and Region Access Blocks 4.

### D. 10k reviews, hand-written plan, CLI

`r_97f665e2` · started Sat Oct 10 23:38 PDT, finished Sun Oct 11 00:06 PDT · log `runs/hero10k.log` · 10,000 in → 9,231 kept (769 dropped by the filter) · 52 low-confidence decisions (Jev unsure and the LLM fallback failed or timed out)

| Node | Items | Jev | LLM | Code | Time |
|---|---|---|---|---|---|
| n1 filter | 10000 → 9231 | 6,937 | 3,063 | 0 | 271.7 s |
| n2 classify | 9231 | 7,896 | 1,335 | 0 | 164.8 s |
| n3 group | 2,596 bugs + requests | 3,145 | 699 | 0 | 77.9 s |
| n4 dedupe | 1,775 bugs → 450 unique (57 clusters); 20,000 of 57,264 candidate pairs checked | 27,547 | 12,453 | 2 | 1,099.5 s |
| n5 score | 1,775 bugs | 1,524 | 251 | 1 | 34.9 s |
| n6 rank | 450 | 38 | 0 | 451 | 5.4 s |
| n7 calculate | 9231 | 0 | 0 | 4 | 0.0 s |

Top 5 ranked bugs. Read "reports" as "related reviews in the same theme" at this size, not exact duplicates (see "Known quality issues"):

| # | Id | Urgency | Reports | Review |
|---|---|---|---|---|
| 1 | r-70 | 100.0 | 158 | "crashes upon opening and freezes device" |
| 2 | r-3816 | 100.0 | 1 | "app is getting closed frequently. miui 10.3.13" |
| 3 | r-3980 | 100.0 | 1 | "your auto refresh feature just erased my work in progress." |
| 4 | r-8406 | 100.0 | 1 | "…i add new topic but i lost my old [topics]…" |
| 5 | r-8491 | 100.0 | 1 | "…for the past year every time i edit an arti[cle]…" |

The next five: r-8553 "deleted my drafts for no reason" (100.0), r-9293 (100.0), r-9562 (100.0, a crypto-exchange KYC complaint the filter should have dropped), r-8309 "last update causes constant crashes" (99.7), r-790 "losing credentials on upgrade" (100.0). Many bugs hit the top severity level, so the order among the 100.0s comes from Jev's tie-break comparisons (19 run, 15 swapped).

Types: praise 4,017, bug 1,775, other 1,370, billing 1,248, feature_request 821. Average bug urgency 63.5.

Themes (bugs and feature requests, 2,596 items): none_of_these 1,061, Writing and publishing problems 371, Sign-up and login issues 351, Navigation and UI issues 245, App crashes and freezes 177, Offline reading and saving 136, Article access limits 106, Premium-only recommendations 50, Image loading and display 35, Missing reading history 31, Text-to-speech and language 20, Data and battery usage 13.

## Known quality issues

- **Fixed: dedupe chained whole themes together** (run D: groups of 439, 321, 202 and 158, each one a whole theme). Clusters are now anchored on their canonical item; the largest group at 10k is 14. "1793 matched pairs not merged" in the 10k dedupe notes are pairs between two members or two clusters, which the new rule deliberately ignores.
- **Fixed: dedupe cost at 10k** (hit `max_pairs`, 66% of run time). Now 4,456 pairs, 34% of run time.
- **Mostly fixed: the filter dropped real reviews.** Drops fell from 77–103 to 49 per 1k and from 769 to 497 per 10k. What's still dropped is mostly off-topic or contentless text ("my name is anisha yadav", "answers to my questions"); a few real ones remain (a VPN/region complaint, short praise of the articles).
- **Mostly fixed at 10k: too many bugs and feature requests got no theme.** 41% at 10k with group's default 60-review sample, 21% with `sample_size: 400` (run D3). Raising `n_themes` from 8 to 12 alone did not help. At 1k it stays near a third (C3: 33%): vague complaints, one-off problems no theme covers, and some items that should have matched a theme. Next step if needed: a broader "General app problems" theme, or a second assignment pass on the outliers, in `foreman/tools/group.py`. Unthemed bugs are not deduped (`skip_blocks`) but are still scored and ranked.
- **LLM fallback timeouts vary by time of day.** The Sunday-morning 10k run had 104 fallback timeouts (8 s each) against 32 overnight, which nearly doubled the filter and classify times. Wall times are only comparable between runs made at similar times.
- **Urgency saturates.** At 10k the top ten bugs all score 100.0, so their order comes from Jev's tie-breaks. A finer severity scale (e.g. separating "crashes on open" from "crashes sometimes") would spread the top.
- **Filter lets some off-topic reviews through.** In run D, rank 8 was a crypto-exchange KYC complaint (r-9562).
- **Theme runs differ.** Group proposes its themes from a sample with the LLM, so theme names and counts change between runs. Compare runs by type counts and the ranked list, not theme names.
