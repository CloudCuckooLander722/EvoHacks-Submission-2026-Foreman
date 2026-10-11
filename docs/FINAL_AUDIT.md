# Final audit — Sun Oct 11, 2026, 10:30 PDT

Start of the final build pass: 10:28 PDT. Checks: `python -m pytest -q` → **358 passed**; `cd web && npm install && npm run build` → **builds** (one chunk-size warning, harmless).

Minh's `web/` (Vite 8 + React 19 + TS, commit `5eb40f3`) is complete enough to build on, so nothing was replaced. `foreman/extract_files/` (csv/xlsx/json/txt/pdf/docx → items) and `foreman/format/` (summary + xlsx/pdf/docx export) are in and wired to the API.

## Before this pass

| Piece | Status | Evidence |
|---|---|---|
| Homepage hero with video | partly works | `web/src/sections/Hero/Hero.tsx`: headline, CTAs, animated placeholder; `DEMO_VIDEO = null` until the video exists |
| "How it works" section | works | `web/src/sections/HowItWorks/HowItWorks.tsx` |
| Live chat demo (job, file, progress, results) | partly works | `LiveDemo.tsx`, `JobComposer.tsx`, `PlanSteps.tsx`, `Meter.tsx`, real SSE client in `api/client.ts`. Real vs mock is a build-time env switch (`VITE_USE_MOCKS`, default **mock**), so the default page never calls the API; no `plan` sent; default job text differs |
| Confidence slider | works | Pre-run threshold slider in `JobComposer.tsx`; post-run slider over `aggregates.escalation_curve` in `ConfidenceCurve.tsx` |
| "Why" panel | works | `WhyPanel.tsx` calls `GET /runs/{id}/decisions?item_id=` |
| Exports | partly works | `Downloads.tsx` links xlsx/pdf/docx; no 501 handling |
| Replay mode | partly works | `api/mock.ts` + `api/replay.ts` replay `public/replays/reviews-1k/`, but only when built in mock mode; no button |
| API | works | `foreman/api/app.py`: `POST /runs` (file, job, threshold, plan), SSE events, results, decisions, export, health |
| README setup instructions | partly works | README has backend commands; nothing on the web app, demo script or replay |

Missing for the video: a ranked **bug backlog** view with "reported by N" (the generic table shows every row), `none_of_these` shown as "Other", a runtime API/replay switch with a clear "API not reachable" message.

## After this pass (10:39 PDT)

Verified with a headless browser (Playwright) against the real API: `demo_300.csv` + "Turn these app reviews into a ranked bug backlog" + plan `reviews_backlog` → plan visible at 2 s, results at 66 s (849 decisions, 85.4% Jev, $0.02, 64 s). Also verified: replay mode, the "API not reachable" message, and the static replay-only build. `pytest -q` 358 passed; web `vitest` 17 passed; `npm run build` OK.

| Piece | Status | Evidence |
|---|---|---|
| Homepage hero with video | partly works | Hero + stat tiles (real numbers, `content/numbers.ts`); video is a placeholder until recorded: set `DEMO_VIDEO` in `Hero.tsx` and put the mp4 in `web/public/` |
| "How it works" section | works | unchanged from Minh |
| Live chat demo | works | `JobComposer.tsx` (job, file, saved plan or LLM planner, threshold) → `POST /runs` through the Vite proxy (`/api` → :8000) → SSE plan steps, Jev / LLM / code meter, cost, time → results |
| Ranked bug backlog | works (new) | `Results/Backlog.tsx`: `rank_order`, theme pill, "reported by N" (`dup_group_count`), urgency; type and theme charts show `none_of_these` as "Other" |
| Confidence slider | works | pre-run slider (sent as `threshold`) + post-run `ConfidenceCurve.tsx` over `aggregates.escalation_curve` |
| "Why" panel | works | click a backlog row or a table cell; `GET /runs/{id}/decisions?item_id=` |
| Exports | works (xlsx only) | `Downloads.tsx`: one Excel button, shown only when the export answers OK (hidden on 501). PDF/Word buttons removed from the UI for the demo (endpoints still exist) |
| Replay mode | works | "Replay a recorded run" button plays `web/public/replays/reviews-1k/` through the same UI, no API. Mode is now a runtime switch (`api/index.ts`), not a build flag |
| API-down message | works (new) | `GET /health` on load; if it fails, Run is disabled with a clear message and Replay becomes the primary button |
| API | works | no API changes were needed (`plan` was already a form field) |
| README setup instructions | works | "Run it": setup, keys, `scripts/demo.sh`, the demo CSV, replay mode |
| Static deploy | ready, not deployed | `cd web && VITE_USE_MOCKS=true npm run build` → `web/dist/` (tested with `vite preview`). To deploy: `cd web && npx vercel` (needs a login, not run). Without `VITE_USE_MOCKS` the site still falls back to replay when no API answers |

Nothing of Minh's was replaced; all changes extend his files. Known gaps: no recorded video yet; the generic results table below the backlog still lists every row (by design); a pre-existing `vite` chunk-size warning.
