"""Streamlit entry for RefCheck AI.

Run locally: `streamlit run app.py`
Deploy: push to a Hugging Face Space (SDK = Streamlit). Set GEMINI_API_KEY as a secret.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import streamlit as st

from src import cache, config
from src.rate_limit import RateLimiter
from src.rules import registry
from src.rules.loader import load_rules
from src.verdict.video_analyzer import analyze_video
from src.video.preprocess import preprocess

VERDICT_COLOR = {
    "fair_call": "#16a34a",
    "bad_call": "#dc2626",
    "inconclusive": "#d97706",
}

# ---- Page setup ----
st.set_page_config(
    page_title="RefCheck AI",
    page_icon="R",
    layout="centered",
    initial_sidebar_state="expanded",
)

if "limiter" not in st.session_state:
    st.session_state.limiter = RateLimiter()


# ---- Helpers ----
def _client_ip() -> str:
    try:
        headers = st.context.headers
        return headers.get("x-forwarded-for", headers.get("x-real-ip", "local"))
    except Exception:
        return "local"


def _render_result(
    result: dict,
    video_path: Path | None,
    *,
    cached: bool = False,
):
    verdict = result.get("verdict", "inconclusive")
    badge = " &nbsp; ⚡ _cached, no API call_" if cached else ""
    st.markdown(f"## {result.get('headline', verdict)}{badge}", unsafe_allow_html=True)
    verdict_color = VERDICT_COLOR.get(verdict, "#6b7280")
    st.markdown(
        f"**Verdict:** <span style='color:{verdict_color}; font-weight:600'>{verdict}</span>  "
        f"•  **Confidence:** {result.get('confidence', 0):.2f}",
        unsafe_allow_html=True,
    )
    play_type = result.get("play_type")
    context = result.get("context")
    decision_event = result.get("decision_event")
    visual_final_call = result.get("visual_final_call")
    if play_type or context:
        bits = []
        if play_type:
            bits.append(f"play type `{play_type}`")
        if context:
            bits.append(f"context `{context}`")
        if decision_event:
            bits.append(f"decision event `{decision_event}`")
        if visual_final_call:
            bits.append(f"visual final call `{visual_final_call}`")
        st.caption("Model classified the clip as " + " · ".join(bits))

    if video_path and Path(video_path).exists():
        st.video(str(video_path))

    st.markdown("**Reasoning**")
    st.write(result.get("reasoning", "(no reasoning provided)"))

    if obs := result.get("key_observations"):
        st.markdown("**Key observations**")
        for o in obs:
            st.markdown(f"- {o}")

    st.markdown(f"**Rule cited:** _{result.get('rule_cited', '—')}_")

    with st.expander("Raw model output"):
        st.json(result)


# ---- Sidebar ----
with st.sidebar:
    st.markdown("### Sport")
    sport_options = {s.display_name: s.key for s in registry.list_sports()}
    sport_label = st.selectbox(
        "Pick the sport",
        list(sport_options.keys()),
        index=0,
        help="The rules and call options change based on this selection.",
    )
    sport = registry.get(sport_options[sport_label])
    st.caption(sport.notes)

    st.markdown("---")
    st.markdown("### Settings")
    mode_key = cache.MODE_GEMINI_DIRECT
    st.caption("AI-only mode: Gemini analyzes the full clip and returns the verdict.")

    user_key = st.text_input(
        "Bring your own Gemini key (optional)",
        type="password",
        help="If set, bypasses the demo's per-IP rate limit and uses your free quota.",
    )

    st.markdown("---")
    st.caption("Daily demo usage")
    stats = st.session_state.limiter.stats()
    st.progress(
        min(stats["global_today"] / max(stats["global_cap"], 1), 1.0),
        text=f"{stats['global_today']} / {stats['global_cap']} analyses today",
    )

    cached_entries = cache.list_entries()
    if cached_entries:
        with st.expander(f"Cached results ({len(cached_entries)})"):
            for e in cached_entries[-10:]:
                st.caption(
                    f"`{e.get('source_name','?')}` · {e.get('sport','?')} · "
                    f"{e.get('mode','?')} · {e.get('original_call','?')}"
                )


# ---- Header ----
st.title("RefCheck AI")
st.caption(
    f"Upload a {sport.display_name.lower()} clip. "
    f"Get a verdict on the {sport.call_kind}, an explanation, "
    "and a rule citation."
)


# ---- Inputs ----
uploaded = st.file_uploader(
    f"{sport.display_name} clip (≤6s, single fixed camera works best)",
    type=["mp4", "mov", "m4v", "webm"],
    help="Upload a single clip to analyze.",
)

default_call = sport.call_options[0]

original_call = st.radio(
    "Original on-court call:",
    list(sport.call_options),
    horizontal=True,
    index=sport.call_options.index(default_call),
)

source_ready = uploaded is not None
go = st.button("Analyze", type="primary", disabled=not source_ready)


# ---- Main flow ----
if go and source_ready:
    content = uploaded.read()
    source_name = uploaded.name

    rules_text = load_rules(sport.key)

    # 1. Cache hit? Show instantly, no API call, no rate-limit consumption.
    cached = cache.lookup(content, sport.key, mode_key, original_call)
    if cached is not None:
        _render_result(cached.result, cached.video_path, cached=True)
        st.stop()

    # 2. Cache miss — enforce rate limit unless user brought their own key.
    if not user_key:
        ok, msg = st.session_state.limiter.allow(_client_ip())
        if not ok:
            st.error(msg)
            st.stop()

    work_dir = config.TMP_DIR / uuid.uuid4().hex
    work_dir.mkdir(parents=True, exist_ok=True)
    raw_path = work_dir / "raw.mp4"
    raw_path.write_bytes(content)

    with st.status("Working on it...", expanded=True) as status:
        try:
            st.write("Compressing video...")
            clip_path = preprocess(raw_path, work_dir / "clip.mp4")

            # ---- Always: Gemini makes the verdict from the raw clip. ----
            st.write(f"Asking Gemini to analyze the clip ({sport.display_name})...")
            result = analyze_video(
                clip_path, rules_text, original_call, sport,
                api_key_override=user_key or None,
            )

            video_to_show = clip_path

            # 3. Save to cache for next time.
            try:
                cache.save(
                    content, sport.key, mode_key, original_call, result,
                    video_to_show, evidence=None, source_name=source_name,
                )
            except Exception as cache_err:
                st.write(f"(could not cache result: {cache_err})")

            status.update(label="Done", state="complete")
        except Exception as e:
            status.update(label=f"Failed: {e}", state="error")
            raise

    _render_result(result, video_to_show, cached=False)
