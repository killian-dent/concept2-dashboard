"""
Overview tab — the new home screen.

Replaces the original section 1 (6-metric strip) and reframes it as:
  1. A 2×2 KPI quadrant — last 30 days, with delta vs previous 30 + sparkline.
  2. A calendar heatmap of activity — last 12 weeks, dow × week.
  3. A compact list of the most recent workouts.

Lifetime totals (which used to occupy half of section 1) move into the Records
tab, where they belong; the home screen is for "how am I doing right now?"
"""
import altair as alt
import pandas as pd
import streamlit as st

import config
import ui
from data import format_pace
from data_extras import (compute_period_kpis, daily_meters,
                          aerobic_efficiency, aerobic_efficiency_summary,
                          next_workout)

# Session type → accent colour for the "Next up" card.
_TYPE_ACCENT = {
    "easy":     ui.ACCENT_PR,    # green
    "interval": ui.ACCENT_WARN,  # amber — the hard day
    "steady":   ui.ACCENT_SEL,   # blue
    "recovery": ui.INK_2,        # muted — take it easy
}


def render(df: pd.DataFrame):
    if df.empty:
        st.info("No workout data available.")
        return

    # ── Aerobic efficiency hero + "Next up" card ─────────────────────────
    _render_hero_row(df)

    # ── KPI quadrant ─────────────────────────────────────────────────────
    k = compute_period_kpis(df, days=30)
    spark = daily_meters(df, days=30)["meters"].tolist()

    def fmt_h(seconds):
        # H:MM, no seconds — KPI cell has no room for them
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}:{m:02d}"

    cells = [
        ui.kpi_cell(
            label="Total meters · 30d",
            value=f"{k['meters'] / 1000:,.1f}k",
            delta=(("+" if k["meters_delta"] >= 0 else "−") +
                   f"{abs(k['meters_delta']) / 1000:,.1f}k"),
            up=k["meters_delta"] >= 0,
            spark=spark,
            color=ui.ACCENT_SEL,
        ),
        ui.kpi_cell(
            label="Avg /500m",
            value=format_pace(k["avg_pace_s"]) if k["avg_pace_s"] else "—",
            delta=(("+" if k["avg_pace_delta"] >= 0 else "−") +
                   f"{abs(k['avg_pace_delta']):.1f}s"),
            up=k["avg_pace_delta"] >= 0,  # sign already flipped in compute_period_kpis
        ),
        ui.kpi_cell(
            label="Sessions · 30d",
            value=str(k["sessions"]),
            delta=(("+" if k["sessions_delta"] >= 0 else "") +
                   str(k["sessions_delta"])),
            up=k["sessions_delta"] >= 0,
        ),
        ui.kpi_cell(
            label="Time on erg · 30d",
            value=fmt_h(k["time_s"]),
            delta=(("+" if k["time_s_delta"] >= 0 else "−") +
                   fmt_h(abs(k["time_s_delta"]))),
            up=k["time_s_delta"] >= 0,
        ),
    ]
    st.html(ui.kpi_grid(cells))

    # ── Heatmap ──────────────────────────────────────────────────────────
    _section_label("Activity · last 12 weeks")
    _render_heatmap(df)

    # ── Recent ───────────────────────────────────────────────────────────
    _section_label("Recent", trailing_link="More in the Workouts tab")
    _render_recent(df.head(6))


# ── Helpers ──────────────────────────────────────────────────────────────

def _render_hero_row(df):
    """Top of the home screen: aerobic-efficiency hero and the 'Next up' card.

    Side by side once there's enough easy data for the hero; before then the
    Next-up card (which is plan-driven, not history-driven) goes full width.
    """
    cap = config.EASY_HR_CAP
    eff = aerobic_efficiency(df, cap=cap)
    has_hero = (not eff.empty) and len(eff) >= 2

    if has_hero:
        c1, c2 = st.columns([1, 1])
        with c1:
            _render_aerobic_hero(eff)
        with c2:
            _render_next_up(df)
    else:
        _render_next_up(df)


def _render_aerobic_hero(eff):
    """Hero card for the plan's success metric: easy pace at a fixed HR."""
    cap = config.EASY_HR_CAP
    s = aerobic_efficiency_summary(eff)
    spark = eff["norm_pace_s"].tolist()

    improved = s["improved_s"]
    faster = improved > 0
    delta_color = ui.ACCENT_PR if faster else ui.ACCENT_WARN
    if abs(improved) >= 0.1:
        arrow = "↓" if faster else "↑"  # lower pace = faster = down
        delta_txt = f"{arrow} {abs(improved):.1f}s {'faster' if faster else 'slower'} since start"
    else:
        delta_txt = "holding steady"
    # Lower pace is better, so a downward sparkline = improvement → green.
    spark_html = ui.sparkline_html(spark, width=120, height=22,
                                   color=delta_color, fill=True)

    st.html(
        f"""
        <div style="padding:14px 16px;background:{ui.BG_1};
                    border:1px solid {ui.LINE};border-radius:10px;
                    display:flex;justify-content:space-between;align-items:center;">
          <div>
            <div style="font-size:10px;color:{delta_color};letter-spacing:0.1em;
                        text-transform:uppercase;font-weight:600;">
              Aerobic efficiency · easy pace @ {cap} bpm</div>
            <div style="font-size:30px;font-weight:600;letter-spacing:-0.02em;
                        margin-top:4px;font-variant-numeric:tabular-nums;
                        color:{ui.INK_0};">{s['latest_norm_pace']}<span
                        style="font-size:13px;color:{ui.INK_2};font-weight:400;">
                        /500</span></div>
            <div style="font-size:11px;color:{delta_color};font-weight:500;
                        margin-top:2px;">{delta_txt}</div>
          </div>
          <div style="text-align:right;">{spark_html}
            <div style="font-size:9px;color:{ui.INK_3};margin-top:4px;">
              {s['count']} easy rows · see Plan tab</div>
          </div>
        </div>
        """
    )


def _uppercase_label(text: str) -> str:
    return (f"<div style='font-size:10px;color:{ui.INK_2};letter-spacing:0.1em;"
            f"text-transform:uppercase;font-weight:600;'>{text}</div>")


def _session_block(sess: dict, compact: bool = False) -> str:
    """HTML for one planned session: dotted title, summary lines, and (when not
    compact) the goal and block label."""
    accent = _TYPE_ACCENT.get(sess["type"], ui.ACCENT_SEL)
    size = "13" if compact else "15"
    out = (
        f"<div style='font-size:{size}px;font-weight:600;color:{ui.INK_0};"
        f"margin-top:{'4' if compact else '6'}px;'>"
        f"<span style='color:{accent};'>●</span> {sess['title']}</div>"
        f"<div style='font-size:11.5px;color:{ui.INK_1};margin-top:4px;"
        f"line-height:1.5;font-variant-numeric:tabular-nums;'>"
        + "<br>".join(sess["lines"]) + "</div>"
    )
    if not compact:
        out += (f"<div style='font-size:11px;color:{ui.INK_2};margin-top:8px;"
                f"line-height:1.45;'>{sess['goal']}</div>")
        if sess.get("block_label"):
            out += (f"<div style='font-size:10px;color:{ui.INK_3};margin-top:8px;"
                    f"letter-spacing:0.04em;'>{sess['block_label']}</div>")
    return out


def _render_next_up(df):
    """Plan-driven 'Next up' card: today's session, or the next one, or — on a
    rest day — the rest/strength note with the upcoming erg session beneath."""
    nu = next_workout(df)
    sess = nu["session"]

    if nu["mode"] == "rest":
        if nu["rest_strength"]:
            note = (f"<div style='font-size:11.5px;color:{ui.INK_1};margin-top:6px;'>"
                    f"Optional strength: "
                    f"<span style='color:{ui.INK_0};'>{nu['rest_strength']}</span></div>")
        else:
            note = (f"<div style='font-size:11.5px;color:{ui.INK_2};margin-top:6px;'>"
                    f"Full rest — let the work absorb.</div>")
        body = (
            _uppercase_label(f"Today · {nu['today_label']} — rest from erg")
            + note
            + f"<div style='border-top:1px solid {ui.LINE};margin-top:11px;'></div>"
            + f"<div style='margin-top:9px;'>"
            + _uppercase_label(f"Next erg · {nu['when_label']}")
            + _session_block(sess, compact=True) + "</div>"
        )
    else:
        head = "Today" if nu["is_today"] else f"Next up · {nu['when_label']}"
        body = _uppercase_label(head)
        if nu["today_done"]:
            body += (f"<div style='font-size:10.5px;color:{ui.ACCENT_PR};"
                     f"margin-top:2px;'>✓ today's session logged</div>")
        body += _session_block(sess, compact=False)

    st.html(
        f"<div style='padding:14px 16px;background:{ui.BG_1};"
        f"border:1px solid {ui.LINE};border-radius:10px;height:100%;'>{body}</div>"
    )


def _section_label(text: str, trailing_link: str = None, tab_name: str = None):
    """Small uppercase section heading with an optional right-aligned hint.

    The trailing item is an informational pointer, not a link: Streamlit has
    no API to switch st.tabs programmatically, and the old window.parent JS
    hack is blocked on Streamlit Community Cloud (the component iframe is
    cross-origin, so it can't reach the parent DOM). The five tabs sit at the
    top of the page, so the hint just names where to go. `tab_name` is kept
    for call-site compatibility but no longer drives any behavior.
    """
    right = ""
    if trailing_link:
        right = (
            f"<span style='font-size:11px;color:{ui.INK_2};"
            f"white-space:nowrap;'>{trailing_link}</span>"
        )
    st.html(
        f"<div style='display:flex;justify-content:space-between;"
        f"align-items:baseline;margin:20px 4px 8px;'>"
        f"<div style='font-size:10px;color:{ui.INK_2};letter-spacing:0.12em;"
        f"text-transform:uppercase;font-weight:600;'>{text}</div>"
        f"{right}</div>"
    )


def _render_heatmap(df: pd.DataFrame):
    daily = daily_meters(df, days=84)
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    daily["dow_name"] = daily["dow"].map(dict(enumerate(dow_names)))
    week_order = ["W" + str(i) for i in sorted(daily["week"].unique())]
    daily["week_label"] = "W" + daily["week"].astype(str)

    chart = (
        alt.Chart(daily)
        .mark_rect()
        .encode(
            x=alt.X("week_label:O", sort=week_order,
                     axis=alt.Axis(title=None, labelAngle=0, labelFontSize=9)),
            y=alt.Y("dow_name:O", sort=dow_names,
                     axis=alt.Axis(title=None, labelFontSize=9)),
            color=alt.Color(
                "meters:Q",
                scale=alt.Scale(range=[ui.BG_2, ui.ACCENT_SEL]),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("dow_name:N", title="Day"),
                alt.Tooltip("week_label:N", title="Week"),
                alt.Tooltip("meters:Q", format=",", title="Meters"),
            ],
        )
        .properties(height=160)
        .configure_axis(
            domainColor="transparent",
            tickColor="transparent",
            gridColor="transparent",
            labelColor=ui.INK_2,
        )
        .configure_view(strokeWidth=0)
    )
    st.altair_chart(chart, use_container_width=True)


def _render_recent(rows: pd.DataFrame):
    """Compact list as styled HTML — no st.dataframe (its column widths
    fight you on phones)."""
    if rows.empty:
        st.caption("No recent workouts.")
        return
    items = []
    for _, r in rows.iterrows():
        items.append(
            f"""
            <div style="display:grid;
                        grid-template-columns:54px 1fr 1fr auto;
                        gap:10px;align-items:center;padding:10px 4px;
                        border-bottom:1px solid {ui.LINE};font-size:12.5px;">
              <div style="color:{ui.INK_2};font-variant-numeric:tabular-nums;">
                {r['date'].strftime('%b %d')}
              </div>
              <div style="font-weight:500;color:{ui.INK_0};">{r['label']}</div>
              <div style="font-variant-numeric:tabular-nums;">
                {int(r['distance_m']):,}m · {r['duration']}
              </div>
              <div style="font-variant-numeric:tabular-nums;color:{ui.INK_1};">
                {r['pace']}/500
              </div>
            </div>
            """
        )
    st.html("<div>" + "".join(items) + "</div>")
