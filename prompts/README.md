# How to run these prompts

Each prompt is pasted into Claude Code at the repo root. Each one finishes by committing.

## Order

1. **`00_setup.md`.** Venv, deps, skeleton, devcontainer. About 5 minutes.
2. **Add keys** to `.env`: `AI_GATEWAY_API_KEY` (Vercel) and `FIREWORKS_API_KEY`. Fill in the Fireworks prices too, so the cost meter is right.
3. **`01_core.md`.** The engine every tool depends on. Run it alone and wait for green tests plus `python -m foreman.dev ping`.
4. **Tools.** Each tool only touches its own files, and the registry auto-discovers it. So you can run up to three or four Claude Code sessions in parallel (separate terminals or worktrees). Pull or rebase between batches.

| Batch | Tools | Why first |
|---|---|---|
| A | 01 filter, 02 classify, 05 score, 08 calculate | Simplest; most of the hero demo |
| B | 03 group, 04 dedupe, 06 rank, 07 verify | The rest of the hero demo |
| C (if time) | 09 extract, 10 find, 11 guard | Extras for the pitch and Q&A |

## After each tool

Check its results on real data, not just with `--fake`:

```bash
python -m foreman.dev tool <name> --items data/samples/reviews_small.json --params '<json>'
```

If you see a decision that looks wrong, run it again at a higher threshold to confirm the fallback catches it.

## After the tools

The next prompts are the planner and executor (`foreman/graph/`), then the API with SSE, then the frontend. Write them once the tools are green.
