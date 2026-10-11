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

## After this pass

See the table at the bottom (updated at hand-off).
