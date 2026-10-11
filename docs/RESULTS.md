# Live run results

Real numbers from live runs on the hero dataset (Medium app reviews, see [`data/hero/README.md`](../data/hero/README.md)), recorded Sat Oct 10, 2026, 23:30 PDT – Sun Oct 11, 00:06 PDT. Use these on the site and in the video; don't round them up.

## Overnight summary (for Nathan and Minh)

- **All of section 4 ran.** Three 1k runs (hand-written plan, planner, API) and the 10k hero run all finished with every node done and no failed or skipped nodes.
- **Headline numbers for the site (10k run):** 10,000 reviews in 27.6 minutes for **$2.25**: 64,893 decisions, 72.6% made by Jev, 17,801 by the LLM. At 1k: about 2 minutes and **11 cents**, with 76–79% by Jev.
- **What eats the time and money at 10k is dedupe:** 18.3 of the 27.6 minutes and 62% of all decisions. It hit its 20,000-pair cap (57,264 candidate pairs), and its clusters chain into whole-theme groups (see "Known quality issues"). Fixing dedupe is the biggest lever for both cost and the "reports" column.
- **The replay bundle on the site is the API run (C, `reviews-1k`).** The 10k run went through the CLI, so it has no `events.jsonl` and no bundle. Its results.json (12 MB) and decisions (24 MB) are too big to ship to a static site anyway; use its numbers, not its files.
- **Choices I made without asking:** I left the tools and the hero plan unchanged (they're outside this task's files) and wrote the issues down instead. No re-runs were done to improve the numbers.

All runs: threshold 0.7, Jev via Vercel AI Gateway, LLM fallback DeepSeek V4 Flash on Fireworks ($0.22 / $0.66 per M tokens), plan `filter → classify → group → dedupe → score → rank → calculate`.

**Jev %** is Jev's share of model decisions, Jev / (Jev + LLM). Code operations (ranking, counting) are listed separately. The confidence curve in each results.json shows how the split moves with the threshold.

## Summary

| Run | Items | Time | Decisions (Jev / LLM / code) | Jev % | Jev cost | LLM cost | Total cost |
|---|---|---|---|---|---|---|---|
| A. 1k, hand-written plan (CLI) | 1,000 | 112.8 s | 3,895 (3,074 / 816 / 5) | 79.0% | $0.0570 | $0.0575 | **$0.1144** |
| B. 1k, planner (CLI) | 1,000 | 122.4 s | 3,507 (2,686 / 816 / 5) | 76.7% | $0.0549 | $0.0608 | **$0.1158** |
| C. 1k, hand-written plan (API, replay `reviews-1k`) | 1,000 | 117.1 s | 3,349 (2,559 / 785 / 5) | 76.5% | $0.0512 | $0.0557 | **$0.1069** |
| D. 10k, hand-written plan (CLI) | 10,000 | 1,655.2 s (27.6 min) | 64,893 (47,087 / 17,801 / 5) | 72.6% | $0.8331 | $1.4125 | **$2.2456** |

Run B's time includes planning (one LLM call). For scale: 3,000–3,900 decisions for about 11 cents is roughly $0.03 per 1,000 decisions, and Jev's half of that is about $0.016. At 10k it is $0.035 per 1,000 decisions; Jev's own decisions cost $0.018 per 1,000.

10k vs 1k: 10× the items took 14.7× the time and 19.6× the cost, because dedupe compares pairs (its work grows faster than the item count) and sends 31% of its checks to the LLM. Every other step scaled about linearly.

Where the LLM is used: the filter step sends about 29% of its decisions to the LLM (the "is this about the app?" question is often borderline on short reviews), and dedupe sends about 30% (pairwise "same problem?" checks). Classify (13%), score (12%) and rank (0%) mostly stay with Jev.

## A. 1k reviews, hand-written plan, CLI

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

## B. 1k reviews, planner, CLI

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

## C. 1k reviews through the API (replay `reviews-1k`)

`r_8e62399f` · `POST /runs` with `reviews_1k.json`, `plan=reviews_backlog` · 1,000 in → 917 kept (83 dropped) · bundled at `web/public/replays/reviews-1k/`

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

## D. 10k reviews, hand-written plan, CLI

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

- **Dedupe chains whole themes together at 10k.** Dedupe runs within each theme (`block_by: theme`), and its clusters merge transitively (A≈B and B≈C put A, B and C in one group). At 10k the four largest clusters have 439, 321, 202 and 158 members, each one a whole theme block (the 439 is every unthemed bug). So r-70's "158 reports" means 158 crash/freeze reviews, not 158 copies of one report, and "1,775 bugs → 450 unique" overstates the merging. It also hit `max_pairs` (20,000 of 57,264 pairs checked) and took 66% of the run time. Possible fixes in `foreman/tools/dedupe.py`: link only to the cluster's canonical item instead of transitively, or require a minimum similarity before a pair counts as linked. At 1k the effect is small (largest cluster 24, in run C).
- **Too many bugs get no theme at 10k.** 1,061 of 2,596 bugs and feature requests (41%) are `none_of_these`, against 6–27% in the 1k runs. The 8+3 themes, proposed from a sample, don't cover 10k reviews. Raising `n_themes` in the plan (e.g. 12–15) would help.
- **Filter drops real reviews.** 77–103 of 1,000 genuine reviews are dropped as "not about the app". Most are short praise of Medium's *articles* ("great articles on machine learning and python."), which the filter's wording ("written by a user about the app") reads literally; a few are real complaints ("arab countries out of illegible country list"). The bug backlog barely changes, but the type counts undercount praise. Fix in the plan: make `true_means` include "comments about the articles or content on the app".
- **Filter lets some off-topic reviews through.** At 10k, rank 8 is a crypto-exchange KYC complaint (r-9562) that has nothing to do with Medium.
- **Theme runs differ.** Group proposes its themes from a sample with the LLM, so theme names and counts change between runs (runs A and C used the same plan). Compare runs by type counts and the ranked list, not theme names.
