---
title: RefCheck AI
colorFrom: green
colorTo: yellow
sdk: streamlit
sdk_version: 1.32.0
app_file: app.py
pinned: false
license: mit
---

# RefCheck AI

AI-powered tennis line-call review. Upload a short clip — the app sends it to Google's Gemini, which watches the play and returns a structured verdict (`fair_call` / `bad_call` / `inconclusive`) with reasoning grounded in the official rulebook.

Built as a hackathon project. Single-page Streamlit app, free to run on the Gemini API free tier, deployable to Hugging Face Spaces in a few minutes.

## Features

- **Upload a tennis clip → get a verdict.** The model identifies the play (serve vs groundstroke, singles vs doubles), applies the relevant rule section, and returns its decision plus a 2–3 sentence explanation.
- **Final-play focused.** The prompt explicitly tells Gemini to base the verdict on the *last decisive bounce* in the clip (the one that ends the point), not the first.
- **Self-consistency check.** If the model's reasoning contradicts itself (e.g. "the ball was outside the line" while also "it touched the line"), the app re-asks at temperature 0 and degrades to `inconclusive` if the contradiction persists.
- **Rule-grounded.** The full ITF-style line-call rules ship in `src/rules/tennis.md` and are passed to the model with every request.
- **Cached results.** Same clip + same on-court call replays from disk instantly with no API cost.
- **Per-IP and global daily rate limits** keep public deploys under the Gemini free tier.
- **Bring-your-own-key** input lets power users use their own Gemini quota.

## Quickstart

```bash
# 1. System deps
brew install ffmpeg          # macOS
# sudo apt install ffmpeg    # Linux

# 2. Python deps
pip install -r requirements.txt

# 3. Get a free Gemini API key
#    https://aistudio.google.com/apikey
cp .env.example .env
# then edit .env and paste your key after GEMINI_API_KEY=

# 4. Run
streamlit run app.py
```

Streamlit opens `http://localhost:8501` in your browser. Upload a clip, pick the original on-court call, hit Analyze.

## Configuration

All knobs live in `src/config.py` and can be overridden with environment variables in `.env`:

| Variable | Default | What it does |
|---|---|---|
| `GEMINI_API_KEY` | _(required)_ | Your key from [Google AI Studio](https://aistudio.google.com/apikey). |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Model name. Use `gemini-2.5-pro` for harder calls (slower, smaller free quota). |
| `PER_IP_PER_HOUR` | `3` | How many analyses a single IP can run per hour. |
| `GLOBAL_PER_DAY` | `500` | Global ceiling so a public deploy can't blow through the free tier. |
| `DISABLE_RATE_LIMIT` | `0` | Set to `1` during a live demo to remove caps. |

Other tunables in `src/config.py` (clip length, fps, encoding quality) default to accuracy-first settings — the app re-encodes uploads at 1080p / 24fps with a slow x264 preset and a mild unsharp mask so the line is crisp on the upload Gemini sees.

## Deploy to Hugging Face Spaces (free)

1. Create a new Space with SDK = **Streamlit**.
2. Push this repo to the Space's git remote.
3. In the Space settings, add `GEMINI_API_KEY` as a **Variable / Secret**.
4. The included `packages.txt` tells the Space to apt-install ffmpeg.

That's it. The Space will install requirements on first boot and serve `app.py`.

## How it works

```
upload → ffmpeg trim/downsample → Gemini 2.5 Flash → JSON verdict → render
```

1. **Preprocessing** (`src/video/preprocess.py`). Trims the clip to ≤12s, scales to 1080p, encodes with x264 + mild unsharp. Reduces upload size while keeping line detail.
2. **Gemini call** (`src/verdict/video_analyzer.py`). The trimmed video is uploaded via the `google-genai` Files API, then sent to the model alongside a structured prompt that includes the sport's rules and an output schema. Response MIME type is forced to `application/json`.
3. **Validation + repair** (same file). The response is JSON-parsed, required fields are enforced, and a contradiction check runs over the reasoning text. If contradictory, a single repair pass at temperature 0 runs, and we degrade to `inconclusive` if it still disagrees with itself.
4. **Caching** (`src/cache.py`). Result + trimmed video are saved keyed on `md5(file_bytes + cache_version + sport + mode + original_call)`. Re-uploads of the same file replay instantly.
5. **Rate limiting** (`src/rate_limit.py`). Per-IP and global daily counters prune themselves continuously.

## Repository layout

```
app.py                          # Streamlit entry point
LICENSE                         # MIT
README.md
requirements.txt                # streamlit, google-genai, python-dotenv
packages.txt                    # ffmpeg (for HF Spaces)
.env.example
.gitignore
src/
  config.py                     # Tunables: model, rate limits, encoding
  cache.py                      # Result cache (verdict + trimmed clip)
  rate_limit.py                 # Per-IP + global daily limiter
  rules/
    registry.py                 # Sport definitions
    loader.py
    tennis.md                   # Rulebook excerpt the model reasons against
  verdict/
    video_analyzer.py           # Prompt + Gemini call + validation
  video/
    preprocess.py               # ffmpeg trim/downsample
scripts/
  warm_cache.py                 # Pre-populate the cache for demo clips
assets/
  demo_clips/                   # (Empty in repo. Drop your own .mp4 + sidecar .json here.)
  cached_results/               # (gitignored. Generated by the app or warm_cache.py.)
```

## Pre-warming demo clips (optional)

If you want a guaranteed-instant demo, drop one or more clips into `assets/demo_clips/` with a sidecar JSON declaring the original on-court call:

```
assets/demo_clips/myclip.mp4
assets/demo_clips/myclip.json   →  {"sport": "tennis", "original_call": "OUT"}
```

Then warm the cache:

```bash
python3 scripts/warm_cache.py --all-from assets/demo_clips/
```

Re-analyzing the same file in the app then replays from disk with no Gemini call.

## API notes

- Uses Google's [Gemini API](https://ai.google.dev/gemini-api/docs) via the `google-genai` Python SDK.
- Default model is `gemini-2.5-flash`. As of writing, the free tier covers thousands of requests per day comfortably for a hackathon-scale deploy. Limits move; check the current quotas at [ai.google.dev/pricing](https://ai.google.dev/pricing).
- Each analysis uploads the trimmed video (≤12s, 1080p) to Google's Files API. The app deletes the uploaded file after the response returns.
- Response is forced to `application/json` via the Gemini structured-output config; the app additionally validates required fields.
- A repair pass (one extra call at temperature 0) only fires when contradiction is detected.

## Limitations

- **Tennis only.** The repo is currently scoped to tennis line calls. A sport registry exists at `src/rules/registry.py` and additional sports can be added by dropping a markdown rulebook and registering an entry, but the only sport in the registry today is tennis.
- **Line calls only.** We do **not** judge: foot faults, lets / net touches, hindrance, time violations, code violations, or whether the player reached the ball before the second bounce. The prompt tells the model to return `inconclusive` with `headline: "Not a line-call play"` for non-line-call clips, but it can still misclassify edge cases.
- **Camera angle matters.** Line-call calls are easier to judge from a camera that shows the bounce directly. Overhead, oblique, or motion-blurred footage degrades the verdict's reliability — the model is instructed to return `inconclusive` when visual evidence is insufficient, but it doesn't always.
- **Final-play heuristic.** When a clip contains multiple bounces, the model is instructed to judge the *last decisive bounce* (the one that ends the point). Clips that don't clearly show point-end may legitimately return `inconclusive`.
- **Confidence is the model's self-report.** It's a useful tiebreaker (we force `inconclusive` when confidence < 0.5) but isn't independently validated.
- **No persistent storage.** The result cache is on-disk under `assets/cached_results/`; on platforms with ephemeral disks (some HF Spaces tiers), the cache resets on container restart.
- **Free-tier dependency.** A public deploy is bounded by the Gemini free tier and the in-app rate limits (`PER_IP_PER_HOUR`, `GLOBAL_PER_DAY`). Power users can paste their own key to bypass per-IP limits.

## Assumptions

- The user provides a clip that is, in fact, a tennis play with a visible line-call event.
- The user knows the original on-court call. The app uses that to determine `fair_call` (model agrees with the call) vs `bad_call` (model disagrees).
- The clip is short — uploads are trimmed to 12s. Longer clips are silently truncated.
- Audio is irrelevant; the preprocess step strips it.
- Gemini's behavior on these prompts is good-enough for hackathon-scope demos. We don't ship an evaluation suite or accuracy benchmark — manual spot-checking is the QA process.

## Privacy

Uploaded clips are sent to Google for analysis. Don't upload anything you wouldn't be comfortable with Google processing. The app caches results locally on the host running it; nothing else is persisted.

## License

[MIT](LICENSE)
