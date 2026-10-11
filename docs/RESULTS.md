# Live run results

Real numbers from live runs on the hero dataset (Medium app reviews, see [`data/hero/README.md`](../data/hero/README.md)), recorded Sat Oct 10, 2026, 23:30–23:40 PDT. Use these on the site and in the video; don't round them up.

All runs: threshold 0.7, Jev via Vercel AI Gateway, LLM fallback DeepSeek V4 Flash on Fireworks ($0.22 / $0.66 per M tokens), plan `filter → classify → group → dedupe → score → rank → calculate`.

**Jev %** is Jev's share of model decisions, Jev / (Jev + LLM). Code operations (ranking, counting) are listed separately. The confidence curve in each results.json shows how the split moves with the threshold.

## Summary

| Run | Items | Time | Decisions (Jev / LLM / code) | Jev % | Jev cost | LLM cost | Total cost |
|---|---|---|---|---|---|---|---|
| A. 1k, hand-written plan (CLI) | 1,000 | 112.8 s | 3,895 (3,074 / 816 / 5) | 79.0% | $0.0570 | $0.0575 | **$0.1144** |
| B. 1k, planner (CLI) | 1,000 | 122.4 s | 3,507 (2,686 / 816 / 5) | 76.7% | $0.0549 | $0.0608 | **$0.1158** |
| C. 1k, hand-written plan (API, replay `reviews-1k`) | 1,000 | 117.1 s | 3,349 (2,559 / 785 / 5) | 76.5% | $0.0512 | $0.0557 | **$0.1069** |
| D. 10k, hand-written plan (CLI) | 10,000 | *running* | | | | | |

Run B's time includes planning (one LLM call). For scale: 3,000–3,900 decisions for about 11 cents is roughly $0.03 per 1,000 decisions, and Jev's half of that is about $0.016.

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

Started Sat Oct 10, 23:38 PDT in the background (`runs/hero10k.log`). Numbers go here when it finishes.

## Known quality issues

- **Filter drops real reviews.** 77–103 of 1,000 genuine reviews are dropped as "not about the app". Most are short praise of Medium's *articles* ("great articles on machine learning and python."), which the filter's wording ("written by a user about the app") reads literally; a few are real complaints ("arab countries out of illegible country list"). The bug backlog barely changes, but the type counts undercount praise. Fix in the plan: make `true_means` include "comments about the articles or content on the app".
- **Theme runs differ.** Group proposes its themes from a sample with the LLM, so theme names and counts change between runs (runs A and C used the same plan). Compare runs by type counts and the ranked list, not theme names.
