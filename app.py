"""
audit_intel_app.py
------------------
Audit Intelligence — global banking risk & controls briefing.

Run with:      streamlit run audit_intel_app.py

Requires `news_providers.py` in the same folder.
API keys go in config.py (rename config_template.py), environment
variables, Streamlit secrets, or the in-page fields.
"""

import os
import re
from html import escape
from collections import Counter
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from textwrap import dedent

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

import news_providers as npv

DEPLOYMENT_BUILD = "2026-09-22-newsdata-fix-v3"

# Internal repository uses NewsData.io. The credential is kept server-side.
# Configure NEWSDATA_API_KEY / NEWSDATA_KEY in Streamlit Secrets/environment.
# A hardcoded fallback is retained for this internal deployment.
NEWSDATA_API_KEY_HARDCODED = "pub_cb85f4550d47494e98426daa602dd2bf"

def get_newsdata_api_key():
    for name in ("NEWSDATA_API_KEY", "NEWSDATA_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    try:
        for name in ("NEWSDATA_API_KEY", "NEWSDATA_KEY"):
            value = str(st.secrets.get(name, "")).strip()
            if value:
                return value
    except Exception:
        pass
    return NEWSDATA_API_KEY_HARDCODED.strip()

def get_api_keys():
    return {"newsdata": get_newsdata_api_key()}

# ---------------------------------------------------------
# 1. APP CONFIGURATION & LIGHT EDITORIAL PALETTE
# ---------------------------------------------------------

st.set_page_config(
    page_title="Audit Intelligence | Global Banking Briefing",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;600&display=swap');

    :root {
        --bg: #F7F8FA;
        --card: #FFFFFF;
        --border: #E5E7EB;
        --text-primary: #111827;
        --text-secondary: #4B5563;
        --text-muted: #6B7280;
        --accent-blue: #2563EB;
        --accent-blue-dark: #1D4ED8;
        --up: #16A34A;
        --down: #DC2626;
        --amber: #B45309;
    }

    .stApp {
        background: var(--bg);
        color: var(--text-primary);
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    }

    #MainMenu, header[data-testid="stHeader"] { background: transparent; }

    /* Pull the entire application upward and reclaim Streamlit's default top whitespace. */
    [data-testid="stAppViewContainer"] .main .block-container {
        padding-top: 1.15rem !important;
        padding-bottom: 2rem !important;
    }
    [data-testid="stAppViewContainer"] .main {
        padding-top: 0 !important;
    }
    header[data-testid="stHeader"] {
        height: 2.6rem !important;
        min-height: 2.6rem !important;
    }

    /* ---------------- Top navigation ---------------- */
    .topnav {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 6px 2px 18px 2px;
        border-bottom: 1px solid var(--border);
        margin-bottom: 8px;
    }
    .topnav-left { display: flex; align-items: center; gap: 18px; }

    /* ---- Emblem: deep navy tile, inner bevel, blue rim glow ---- */
    .logo-icon {
        position: relative;
        width: 62px; height: 62px;
        border-radius: 18px;
        background:
            radial-gradient(120% 120% at 28% 18%, #3B82F6 0%, #1D4ED8 42%, #14264F 100%);
        display: flex; align-items: center; justify-content: center;
        box-shadow:
            0 10px 26px rgba(29, 78, 216, 0.38),
            0 2px 5px rgba(11, 18, 32, 0.22),
            inset 0 1px 0 rgba(255, 255, 255, 0.42),
            inset 0 -2px 6px rgba(3, 10, 26, 0.45);
        flex-shrink: 0;
    }
    .logo-icon::after {
        content: "";
        position: absolute; inset: 0;
        border-radius: 18px;
        border: 1px solid rgba(255, 255, 255, 0.20);
        pointer-events: none;
    }
    .logo-icon svg { width: 34px; height: 34px; display: block; }

    /* ---- Wordmark ---- */
    .logo-lockup { display: flex; flex-direction: column; }
    .logo-textrow { display: flex; align-items: center; gap: 12px; }
    .logo-text {
        font-weight: 900;
        font-size: 38px;
        letter-spacing: -1.5px;
        line-height: 1.02;
        color: #0B1220;
        white-space: nowrap;
    }
    /* Two-tone: "Audit" in ink, "Intelligence" in a blue gradient */
    .logo-text .lt-accent {
        background: linear-gradient(92deg, #2563EB 0%, #4F46E5 55%, #7C3AED 100%);
        -webkit-background-clip: text;
        background-clip: text;
        -webkit-text-fill-color: transparent;
        color: #2563EB;
    }

    /* Live status pill */
    .live-pill {
        display: inline-flex; align-items: center; gap: 6px;
        background: rgba(22, 163, 74, 0.10);
        border: 1px solid rgba(22, 163, 74, 0.35);
        color: #15803D;
        font-size: 10px; font-weight: 800;
        letter-spacing: 1.2px; text-transform: uppercase;
        padding: 4px 10px; border-radius: 999px;
        white-space: nowrap;
    }
    .live-dot {
        width: 7px; height: 7px; border-radius: 50%;
        background: #16A34A;
        box-shadow: 0 0 0 0 rgba(22, 163, 74, 0.65);
        animation: livepulse 2s infinite;
    }
    @keyframes livepulse {
        0%   { box-shadow: 0 0 0 0 rgba(22, 163, 74, 0.60); }
        70%  { box-shadow: 0 0 0 7px rgba(22, 163, 74, 0); }
        100% { box-shadow: 0 0 0 0 rgba(22, 163, 74, 0); }
    }

    .logo-sub {
        display: flex; align-items: center; gap: 9px;
        font-size: 11px; font-weight: 700; color: var(--text-muted);
        letter-spacing: 2.1px; text-transform: uppercase; margin-top: 7px;
    }
    .logo-rule {
        width: 30px; height: 3px; border-radius: 2px;
        background: linear-gradient(90deg, #2563EB, #7C3AED);
        flex-shrink: 0;
    }

    /* ---- Top-right principal block ---- */
    .topnav-user {
        display: flex; align-items: center; gap: 16px;
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: 16px;
        padding: 12px 18px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    .topnav-user-meta { text-align: right; line-height: 1.3; }
    .topnav-user-label {
        font-size: 10px; font-weight: 700; color: var(--accent-blue);
        letter-spacing: 1px; text-transform: uppercase; margin-bottom: 3px;
    }
    .topnav-user-name { font-weight: 800; font-size: 20px; color: #0B1220; letter-spacing: -0.3px; }
    .topnav-user-title { font-size: 13.5px; color: var(--text-secondary); font-weight: 500; }
    .topnav-user-stamp { font-size: 11px; color: var(--text-muted); margin-top: 4px; font-family: 'JetBrains Mono', monospace; }
    .avatar-photo {
        width: 68px; height: 68px; border-radius: 50%;
        object-fit: cover; flex-shrink: 0;
        border: 3px solid #fff;
        box-shadow: 0 0 0 2px var(--accent-blue);
    }
    .avatar-circle-lg {
        width: 68px; height: 68px; border-radius: 50%;
        background: linear-gradient(135deg, #2563EB, #7C3AED);
        display: flex; align-items: center; justify-content: center;
        font-weight: 800; font-size: 26px; color: #fff; flex-shrink: 0;
        box-shadow: 0 0 0 2px var(--accent-blue);
    }

    .action-caption {
        font-size: 11.5px; color: var(--text-muted);
        font-family: 'JetBrains Mono', monospace; padding-top: 10px;
    }

    /* ---------------- Section headings ---------------- */
    .section-heading {
        font-size: 15px;
        font-weight: 700;
        color: #0B1220;
        margin: 4px 0 14px 0;
    }
    .page-title {
        font-size: 26px;
        font-weight: 800;
        color: #0B1220;
        letter-spacing: -0.5px;
        margin-bottom: 2px;
    }
    .page-subtitle {
        font-size: 13px;
        color: var(--text-secondary);
        margin-bottom: 20px;
    }

    /* ================= TAB VISIBILITY FIX =================
       Streamlit nests each tab label inside <p>/<div> nodes and applies its
       own theme colour + a red/pink highlight bar. Colour therefore has to be
       forced on the INNER nodes (and via -webkit-text-fill-color) or the
       inactive tabs render almost invisible against the light background. */
    div[data-testid="stTabs"] { margin-top: 4px; margin-bottom: 20px; }
    div[data-testid="stTabs"] [role="tablist"] {
        gap: 4px;
        border-bottom: 1px solid var(--border);
        background: transparent !important;
    }
    div[data-testid="stTabs"] button[role="tab"] {
        border-radius: 0 !important;
        padding: 9px 18px !important;
        letter-spacing: 0.3px;
        text-transform: uppercase;
        background: transparent !important;
        border: none !important;
        border-bottom: 3px solid transparent !important;
        opacity: 1 !important;
    }
    div[data-testid="stTabs"] button[role="tab"],
    div[data-testid="stTabs"] button[role="tab"] *,
    div[data-testid="stTabs"] button[role="tab"] p {
        color: #0B1220 !important;
        -webkit-text-fill-color: #0B1220 !important;
        font-size: 13px !important;
        font-weight: 700 !important;
        opacity: 1 !important;
    }
    div[data-testid="stTabs"] button[role="tab"]:hover,
    div[data-testid="stTabs"] button[role="tab"]:hover *,
    div[data-testid="stTabs"] button[role="tab"]:hover p {
        color: #000000 !important;
        -webkit-text-fill-color: #000000 !important;
    }
    div[data-testid="stTabs"] button[role="tab"][aria-selected="true"],
    div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] *,
    div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] p {
        color: var(--accent-blue) !important;
        -webkit-text-fill-color: var(--accent-blue) !important;
        font-weight: 800 !important;
    }
    div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
        border-bottom: 3px solid var(--accent-blue) !important;
    }
    div[data-testid="stTabs"] [data-baseweb="tab-highlight"],
    div[data-testid="stTabs"] [data-baseweb="tab-border"] {
        background-color: transparent !important;
        display: none !important;
    }
    div[data-testid="stTabs"] button[role="tab"]:focus,
    div[data-testid="stTabs"] button[role="tab"]:focus-visible {
        outline: none !important;
        box-shadow: none !important;
    }
    /* ======================================================= */

    /* ---------------- Inputs ---------------- */
    .stTextInput>div>div>input {
        background-color: #fff !important;
        border: 1px solid var(--border) !important;
        color: var(--text-primary) !important;
        border-radius: 10px !important;
        padding: 11px 16px !important;
        font-size: 14px !important;
    }
    .stTextInput>div>div>input:focus {
        border-color: var(--accent-blue) !important;
        box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12) !important;
    }

    /* ---------------- Buttons ---------------- */
    .stButton>button {
        background: var(--accent-blue);
        color: #fff !important;
        border: none;
        border-radius: 8px;
        font-weight: 600;
        font-size: 13.5px;
        padding: 9px 18px;
    }
    .stButton>button * { color: #fff !important; -webkit-text-fill-color: #fff !important; }
    .stButton>button:hover { background: var(--accent-blue-dark); }
    [data-testid="stDownloadButton"]>button {
        background: #fff;
        color: var(--accent-blue) !important;
        border: 1px solid var(--accent-blue);
        border-radius: 8px;
        font-weight: 600;
    }
    [data-testid="stDownloadButton"]>button:hover { background: #EFF6FF; }

    /* ---------------- Legible light theme inside controls ---------------- */
    [data-testid="stExpander"] {
        background: #fff !important;
        border: 1px solid var(--border) !important;
        border-radius: 12px !important;
    }
    [data-testid="stExpander"] summary {
        background: #fff !important;
        color: var(--text-primary) !important;
    }
    [data-testid="stExpander"] summary:hover { color: var(--accent-blue) !important; }
    [data-testid="stExpanderDetails"] { background: #fff !important; }
    [data-testid="stExpander"] label,
    [data-testid="stExpander"] p,
    [data-testid="stExpander"] span,
    [data-testid="stExpander"] div { color: var(--text-primary) !important; }
    [data-testid="stSlider"] [data-testid="stTickBarMin"],
    [data-testid="stSlider"] [data-testid="stTickBarMax"] { color: var(--text-secondary) !important; }
    [data-testid="stSlider"] div[data-baseweb="slider"] > div { background: #E5E7EB !important; }
    [data-testid="stSlider"] div[role="slider"] {
        background-color: var(--accent-blue) !important;
        border-color: var(--accent-blue) !important;
    }
    div[data-baseweb="tag"] {
        background-color: rgba(37, 99, 235, 0.10) !important;
        border: 1px solid rgba(37, 99, 235, 0.35) !important;
        color: var(--accent-blue) !important;
    }
    div[data-baseweb="tag"] span { color: var(--accent-blue) !important; }
    div[data-baseweb="tag"] svg { fill: var(--accent-blue) !important; }

    /* ---------------- Top stories carousel ---------------- */
    .top-stories {
        background:linear-gradient(135deg,#0B1220 0%,#111C36 55%,#1B2B61 100%);
        border-radius:18px; padding:18px 18px 16px; margin:2px 0 22px;
        box-shadow:0 12px 32px rgba(11,18,32,.14);
        color:#fff;
    }
    .top-stories-head {
        display:flex; justify-content:space-between; align-items:center;
        gap:14px; margin-bottom:13px;
    }
    .top-stories-kicker {
        display:flex; align-items:center; gap:8px;
        font-size:10px; font-weight:850; letter-spacing:1.6px;
        text-transform:uppercase; color:#93C5FD;
    }
    .top-stories-kicker .live-dot { width:6px;height:6px;box-shadow:none;animation:none;background:#22C55E; }
    .top-stories-title { font-size:22px;font-weight:900;letter-spacing:-.5px;margin-top:3px; }
    .top-stories-sub { font-size:11.5px;color:rgba(255,255,255,.65);margin-top:3px; }
    .top-stories-progress {
        display:flex; align-items:center; gap:7px;
        font-size:9.5px;font-family:'JetBrains Mono',monospace;color:rgba(255,255,255,.62);
        white-space:nowrap;
    }
    .top-stories-dots { display:flex; gap:5px; }
    .top-stories-dot { width:5px;height:5px;border-radius:50%;background:rgba(255,255,255,.25); }
    .top-story-slide { display:none; animation:topStoryFade .55s ease; }
    .top-story-slide.active { display:grid; grid-template-columns:36% 64%; }
    .top-story-image-wrap { min-height:250px; overflow:hidden; border-radius:13px 0 0 13px; background:#1E293B; }
    .top-story-image { width:100%;height:100%;min-height:250px;display:block;object-fit:cover; }
    .top-story-body {
        background:rgba(255,255,255,.06); backdrop-filter:blur(8px);
        padding:23px 25px; border:1px solid rgba(255,255,255,.08);
        border-left:0; border-radius:0 13px 13px 0;
        display:flex; flex-direction:column; justify-content:center;
    }
    .top-story-label { font-size:9px;font-weight:850;letter-spacing:1px;text-transform:uppercase;margin-bottom:8px; }
    .top-story-number {
        display:inline-flex;align-items:center;justify-content:center;
        width:26px;height:26px;border-radius:8px;background:rgba(255,255,255,.10);
        color:#fff;font-size:11px;font-weight:900;margin-bottom:12px;
    }
    .top-story-title {
        color:#fff;text-decoration:none;font-size:25px;line-height:1.18;
        font-weight:900;letter-spacing:-.5px;
    }
    .top-story-title:hover { color:#BFDBFE; }
    .top-story-desc {
        color:rgba(255,255,255,.72);font-size:12.5px;line-height:1.55;
        margin-top:10px;display:-webkit-box;-webkit-line-clamp:3;
        -webkit-box-orient:vertical;overflow:hidden;
    }
    .top-story-meta {
        display:flex;justify-content:space-between;gap:10px;
        padding-top:15px;margin-top:16px;border-top:1px solid rgba(255,255,255,.10);
        color:rgba(255,255,255,.58);font-size:10.5px;font-weight:650;
    }
    @keyframes topStoryFade {
        from { opacity:0; transform:translateY(5px); }
        to { opacity:1; transform:translateY(0); }
    }

    /* ---------------- News-first newsroom layout ---------------- */
    .news-masthead {
        display:flex; justify-content:space-between; align-items:flex-end; gap:20px;
        padding:8px 2px 18px; margin-bottom:16px;
        border-bottom:1px solid var(--border);
    }
    .news-kicker {
        display:flex; align-items:center; gap:8px;
        color:var(--accent-blue); font-size:10px; font-weight:850;
        letter-spacing:1.5px; text-transform:uppercase; margin-bottom:6px;
    }
    .news-kicker .live-dot { width:6px; height:6px; box-shadow:none; animation:none; }
    .news-title {
        font-size:30px; font-weight:900; color:#0B1220;
        letter-spacing:-1px; line-height:1.05;
    }
    .news-subtitle {
        font-size:12.5px; color:var(--text-secondary);
        margin-top:7px; max-width:760px;
    }
    .news-metrics { display:flex; gap:8px; flex-shrink:0; }
    .news-metrics div {
        min-width:72px; padding:9px 12px; text-align:center;
        border:1px solid var(--border); border-radius:10px; background:#fff;
    }
    .news-metrics b { display:block; font-size:18px; color:#0B1220; line-height:1; }
    .news-metrics span {
        display:block; margin-top:4px; font-size:8.5px; font-weight:800;
        letter-spacing:.7px; text-transform:uppercase; color:var(--text-muted);
    }

    .featured-news-card {
        display:grid; grid-template-columns:42% 58%;
        background:#fff; border:1px solid var(--border); border-radius:16px;
        overflow:hidden; margin-bottom:22px;
        box-shadow:0 5px 18px rgba(11,18,32,.06);
    }
    .featured-image-wrap {
        display:block; min-height:280px; background:#EEF2F7; overflow:hidden;
    }
    .featured-image {
        width:100%; height:100%; min-height:280px; display:block;
        object-fit:cover; transition:transform .35s ease;
    }
    .featured-image-wrap:hover .featured-image { transform:scale(1.025); }
    .featured-news-body {
        display:flex; flex-direction:column; justify-content:center;
        padding:28px 30px;
    }
    .featured-news-label {
        font-size:9.5px; font-weight:850; letter-spacing:1px;
        text-transform:uppercase; margin-bottom:10px;
    }
    .featured-news-title {
        color:#0B1220; text-decoration:none;
        font-size:25px; line-height:1.18; font-weight:900;
        letter-spacing:-.6px;
    }
    .featured-news-title:hover { color:var(--accent-blue); }
    .featured-news-desc {
        color:var(--text-secondary); font-size:13px; line-height:1.55;
        margin-top:11px; display:-webkit-box; -webkit-line-clamp:4;
        -webkit-box-orient:vertical; overflow:hidden;
    }
    .featured-news-meta {
        display:flex; justify-content:space-between; gap:12px;
        padding-top:17px; margin-top:18px; border-top:1px solid #F1F5F9;
        color:var(--text-muted); font-size:10.5px; font-weight:650;
    }
    .feed-section-title {
        display:flex; justify-content:space-between; align-items:center;
        margin:4px 0 12px; padding-bottom:8px;
        border-bottom:1px solid var(--border);
    }
    .feed-section-title span:first-child {
        font-size:16px; font-weight:850; color:#0B1220;
    }
    .feed-section-title span:last-child {
        font-size:10px; color:var(--text-muted); text-transform:uppercase;
        letter-spacing:.8px; font-weight:700;
    }

    /* Sidebar — compact news-reader navigation */
    section[data-testid="stSidebar"] {
        background:#FFFFFF !important;
        border-right:1px solid #E5E7EB;
    }
    section[data-testid="stSidebar"] > div {
        padding:0.8rem 0.65rem 1.2rem;
    }
    section[data-testid="stSidebar"] .stButton {
        margin:0 !important;
    }
    section[data-testid="stSidebar"] .stButton > button {
        min-height:39px;
        height:39px;
        border-radius:9px;
        border:1px solid transparent !important;
        background:transparent !important;
        color:#596579 !important;
        box-shadow:none !important;
        text-align:left !important;
        justify-content:flex-start !important;
        padding:0 11px !important;
        font-size:12px !important;
        font-weight:600 !important;
    }
    section[data-testid="stSidebar"] .stButton > button:hover {
        background:#F4F7FC !important;
        color:#2563EB !important;
    }
    section[data-testid="stSidebar"] .stButton > button * {
        color:inherit !important;
        -webkit-text-fill-color:currentColor !important;
    }
    section[data-testid="stSidebar"] .sidebar-active > button {
        background:#EAF1FF !important;
        color:#2563EB !important;
        font-weight:800 !important;
    }
    section[data-testid="stSidebar"] .sidebar-refresh > button {
        background:#2563EB !important;
        color:#fff !important;
        box-shadow:0 5px 14px rgba(37,99,235,.20) !important;
    }
    section[data-testid="stSidebar"] .sidebar-refresh > button:hover {
        background:#1D4ED8 !important;
        color:#fff !important;
    }
    .sidebar-brand {
        display:flex;align-items:center;gap:9px;
        padding:6px 7px 14px;border-bottom:1px solid #EEF1F5;
        margin-bottom:8px;
    }
    .sidebar-brand-icon {
        width:30px;height:30px;border-radius:9px;background:#2563EB;
        color:#fff;display:flex;align-items:center;justify-content:center;
        font-size:14px;box-shadow:0 5px 12px rgba(37,99,235,.20);
    }
    .sidebar-brand-title {font-size:13px;font-weight:850;color:#0B1220;}
    .sidebar-brand-sub {font-size:9px;color:#8A94A6;margin-top:2px;}
    .sidebar-section-label {
        font-size:9px;font-weight:800;letter-spacing:1.2px;text-transform:uppercase;
        color:#8A94A6;padding:12px 9px 5px;
    }
    .sidebar-live-card {
        padding:11px 9px 12px;border-top:1px solid #EEF1F5;border-bottom:1px solid #EEF1F5;
        margin:7px 0 9px;
    }
    .sidebar-live-kicker {
        display:flex;align-items:center;gap:6px;font-size:9px;font-weight:800;
        letter-spacing:1px;color:#8A94A6;text-transform:uppercase;margin-bottom:8px;
    }
    .sidebar-live-dot {width:7px;height:7px;border-radius:50%;background:#16A34A;}
    .sidebar-provider-row {display:flex;align-items:center;justify-content:space-between;}
    .sidebar-provider-name {font-size:12px;font-weight:800;color:#273247;}
    .sidebar-active-pill {
        padding:4px 9px;border-radius:999px;background:#DDF7E8;color:#159447;
        font-size:9px;font-weight:800;
    }
    .sidebar-stamp {font-size:9px;color:#8993A5;margin-top:6px;line-height:1.4;}
    .sidebar-quick-title {
        font-size:9px;font-weight:800;letter-spacing:1px;color:#8A94A6;
        text-transform:uppercase;padding:9px 9px 5px;
    }
    .sidebar-divider {height:1px;background:#EEF1F5;margin:7px 4px;}


    /* ---------------- Featured Analysis hero ---------------- */
    .featured-hero {
        position: relative;
        height: 360px;
        border-radius: 16px;
        background-size: cover;
        background-position: center;
        /* Fallback tint if the hero image fails to load */
        background-color: #1E3A8A;
        overflow: hidden;
        margin-bottom: 30px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.06);
    }
    .featured-badge {
        position: absolute; top: 20px; left: 20px;
        color: #fff; font-size: 11px; font-weight: 700;
        padding: 5px 12px; border-radius: 6px;
        text-transform: uppercase; letter-spacing: 0.5px;
        z-index: 2;
    }
    .featured-text { position: absolute; bottom: 24px; left: 28px; right: 28px; z-index: 2; }
    .featured-title {
        font-size: 27px; font-weight: 800; color: #fff; line-height: 1.28;
        margin-bottom: 8px; text-shadow: 0 2px 10px rgba(0,0,0,0.35);
    }
    .featured-meta { font-size: 13px; color: rgba(255,255,255,0.85); font-weight: 500; }
    .featured-link-overlay { position: absolute; inset: 0; z-index: 3; }

    /* ---------------- Category newsroom grid ---------------- */
    .briefing-hero {
        position: relative;
        overflow: hidden;
        border-radius: 20px;
        padding: 26px 30px;
        margin: 4px 0 22px 0;
        background:
            radial-gradient(circle at 88% 20%, rgba(124,58,237,.32), transparent 30%),
            radial-gradient(circle at 68% 100%, rgba(37,99,235,.28), transparent 34%),
            linear-gradient(135deg, #0B1220 0%, #111C36 52%, #182A55 100%);
        color: #fff;
        box-shadow: 0 16px 40px rgba(11,18,32,.16);
    }
    .briefing-kicker {
        display:flex; align-items:center; gap:9px;
        font-size:10px; font-weight:800; letter-spacing:1.7px;
        text-transform:uppercase; color:#93C5FD; margin-bottom:7px;
    }
    .briefing-kicker-dot {
        width:7px; height:7px; border-radius:50%; background:#22C55E;
        box-shadow:0 0 0 5px rgba(34,197,94,.12);
    }
    .briefing-title {
        font-size:29px; line-height:1.08; font-weight:900;
        letter-spacing:-1px; margin:0 0 7px 0;
    }
    .briefing-subtitle {
        color:rgba(255,255,255,.70); font-size:12.5px;
        max-width:700px; line-height:1.5;
    }
    .briefing-stats {
        position:absolute; right:28px; top:24px;
        display:flex; gap:10px;
    }
    .brief-stat {
        min-width:86px; padding:10px 13px; text-align:center;
        border:1px solid rgba(255,255,255,.13); border-radius:12px;
        background:rgba(255,255,255,.07); backdrop-filter:blur(8px);
    }
    .brief-stat-number { font-size:19px; font-weight:900; line-height:1; }
    .brief-stat-label { margin-top:5px; font-size:9px; text-transform:uppercase;
        letter-spacing:.7px; color:rgba(255,255,255,.58); font-weight:700; }

    .category-filter-status {
        display:flex;align-items:center;gap:8px;margin:-3px 0 14px;
        font-size:9px;color:#8A94A6;letter-spacing:1px;text-transform:uppercase;
    }
    .category-filter-status b {color:#2563EB;font-size:10px;letter-spacing:.4px;}

    .category-section { margin: 0 0 28px 0; }
    .category-heading {
        display:flex; align-items:center; justify-content:space-between;
        margin:0 0 11px 0; padding-bottom:9px;
        border-bottom:1px solid var(--border);
    }
    .category-heading-left { display:flex; align-items:center; gap:10px; }
    .category-accent {
        width:4px; height:24px; border-radius:99px; flex-shrink:0;
    }
    .category-name { font-size:17px; font-weight:850; color:#0B1220; letter-spacing:-.3px; }
    .category-count {
        font-family:'JetBrains Mono',monospace; font-size:10.5px; font-weight:700;
        color:var(--text-muted); background:#F3F4F6; border-radius:999px; padding:4px 8px;
    }
    .category-grid {
        display:grid;
        grid-template-columns:repeat(2,minmax(0,1fr));
        gap:14px;
    }
    .category-card {
        position:relative; min-width:0; overflow:hidden;
        display:flex; flex-direction:column;
        background:#fff; border:1px solid var(--border); border-radius:15px;
        box-shadow:0 2px 7px rgba(11,18,32,.035);
        transition:transform .16s ease, box-shadow .16s ease, border-color .16s ease;
    }
    .category-card:hover {
        transform:translateY(-2px);
        border-color:#CBD5E1;
        box-shadow:0 12px 28px rgba(11,18,32,.09);
    }
    .category-card-image-wrap {
        position:relative; width:100%; height:180px; min-height:180px; overflow:hidden; background:#EEF2F7;
    }
    .category-card-image {
        width:100%; height:100%; display:block; object-fit:cover;
        transition:transform .35s ease;
    }
    .category-card:hover .category-card-image { transform:scale(1.035); }
    .category-card-image-wrap::after {
        content:""; position:absolute; inset:0;
        background:linear-gradient(180deg,rgba(0,0,0,0) 55%,rgba(0,0,0,.20));
        pointer-events:none;
    }
    .category-card-body { padding:16px 18px 14px; display:flex; flex-direction:column; min-height:180px; }
    .category-card-meta { display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:8px; }
    .badge {
        display:inline-block; color:#fff; font-size:9.5px; font-weight:800;
        padding:4px 9px; border-radius:5px; text-transform:uppercase; letter-spacing:.5px;
    }
    .insight-date { font-size:10.5px; color:var(--text-muted); font-weight:600; }
    .category-card-title {
        font-size:16px; font-weight:800; color:#0B1220; line-height:1.3;
        letter-spacing:-.2px; margin:0 0 7px;
    }
    .category-card-title-link { text-decoration:none; }
    .category-card-title-link:hover .category-card-title { color:var(--accent-blue); }
    .category-card-desc {
        font-size:12.5px; color:var(--text-secondary); line-height:1.5;
        display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden;
    }
    .category-card-footer {
        display:flex; justify-content:space-between; align-items:center;
        margin-top:auto; padding-top:11px;
    }
    .source-chip { font-size:10px; color:var(--text-muted); font-weight:650; overflow:hidden;
        text-overflow:ellipsis; white-space:nowrap; max-width:55%; }
    .read-link { font-size:11.5px; font-weight:800; color:var(--accent-blue); text-decoration:none; }
    .read-link:hover { text-decoration:underline; }

    @media (max-width: 900px) {
        .briefing-stats { position:static; margin-top:18px; }
        .category-grid { grid-template-columns:1fr; }
        .category-card { grid-template-columns:1fr; }
        .category-card-image-wrap { height:190px; min-height:190px; }
        .featured-news-card { grid-template-columns:1fr; }
        .featured-image-wrap, .featured-image { min-height:230px; height:230px; }
        .news-masthead { align-items:flex-start; flex-direction:column; }
        .news-title { font-size:25px; }
    }

    /* ---------------- Right sidebar panels ---------------- */
    .side-panel {
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: 14px;
        padding: 18px 20px;
        margin-bottom: 16px;
    }
    .side-panel-title {
        font-size: 11px; font-weight: 700; color: var(--text-secondary);
        text-transform: uppercase; letter-spacing: 0.8px;
        margin-bottom: 14px; display: flex; align-items: center; gap: 6px;
    }
    .filter-row, .pulse-row {
        display: flex; justify-content: space-between; align-items: center;
        padding: 8px 0; border-bottom: 1px solid #F3F4F6; font-size: 13px; color: #374151;
    }
    .filter-row:last-child, .pulse-row:last-child { border-bottom: none; }
    .filter-value { font-weight: 600; color: var(--text-primary); }
    .pulse-value { font-weight: 700; color: var(--text-primary); font-family: 'JetBrains Mono', monospace; }

    /* ---------------- Market panel ---------------- */
    .mkt-row {
        display: flex; justify-content: space-between; align-items: center;
        padding: 9px 0; border-bottom: 1px solid #F3F4F6;
    }
    .mkt-row:last-child { border-bottom: none; }
    .mkt-name { font-size: 13px; font-weight: 600; color: #374151; }
    .mkt-sub { font-size: 10.5px; color: var(--text-muted); font-weight: 500; }
    .mkt-right { text-align: right; }
    .mkt-price { font-size: 13.5px; font-weight: 700; color: var(--text-primary); font-family: 'JetBrains Mono', monospace; }
    .mkt-chg { font-size: 11.5px; font-weight: 700; font-family: 'JetBrains Mono', monospace; }
    .mkt-up { color: var(--up); }
    .mkt-down { color: var(--down); }
    .mkt-stamp { font-size: 10.5px; color: var(--text-muted); margin-top: 12px; }

    /* ---------------- Risk radar ---------------- */
    .risk-row { padding: 8px 0; border-bottom: 1px solid #F3F4F6; }
    .risk-row:last-child { border-bottom: none; }
    .risk-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
    .risk-name { font-size: 12.5px; font-weight: 600; color: #374151; }
    .risk-count { font-size: 11px; font-weight: 700; color: var(--text-secondary); font-family: 'JetBrains Mono', monospace; }
    .risk-track { height: 6px; background: #F3F4F6; border-radius: 3px; overflow: hidden; }
    .risk-fill { height: 100%; border-radius: 3px; }

    .alert-item {
        display: block; text-decoration: none;
        padding: 9px 0; border-bottom: 1px solid #F3F4F6;
    }
    .alert-item:last-child { border-bottom: none; }
    .alert-tag {
        font-size: 9.5px; font-weight: 800; color: var(--amber);
        letter-spacing: 0.6px; text-transform: uppercase;
    }
    .alert-text {
        font-size: 12.5px; color: #0B1220; font-weight: 600; line-height: 1.4; margin-top: 3px;
        display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
    }
    .alert-item:hover .alert-text { color: var(--accent-blue); }

    .cta-panel {
        background: linear-gradient(135deg, #2563EB, #1D4ED8);
        border-radius: 14px;
        padding: 20px 22px 6px 22px;
        color: #fff;
        margin-bottom: -4px;
    }
    .cta-title { font-size: 15px; font-weight: 700; margin-bottom: 6px; }
    .cta-desc { font-size: 12.5px; color: rgba(255,255,255,0.85); line-height: 1.5; margin-bottom: 14px; }

    .empty-state-panel {
        text-align: center; padding: 48px;
        background: var(--card); border-radius: 16px; border: 1px dashed var(--border);
        margin-top: 14px;
    }

    /* ---------------- Footer ---------------- */
    .app-footer {
        display: flex; justify-content: space-between; align-items: flex-start;
        padding-top: 22px; margin-top: 8px;
    }
    .footer-brand { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-weight: 800; font-size: 14.5px; color: #0B1220; }
    .footer-tagline { font-size: 12px; color: var(--text-muted); max-width: 320px; line-height: 1.5; }
    .footer-links { display: flex; gap: 22px; font-size: 12.5px; color: var(--text-secondary); font-weight: 600; }
    .footer-copyright { font-size: 11.5px; color: var(--text-muted); margin-top: 18px; }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------
# 2. CATEGORIES, SCORING VOCABULARY & BRANDING
# ---------------------------------------------------------

CATEGORIES = {k: {} for k in ("Transformation", "Regulation", "People", "Cyber & Tech", "Global Banks")}

CATEGORY_DISPLAY = {
    "Transformation": "Transformation",
    "Regulation": "Regulation",
    "People": "People",
    "Cyber & Tech": "Cyber & Technology",
    "Global Banks": "Global Banking",
}
CATEGORY_COLORS = {
    "Transformation": "#2563EB",
    "Regulation": "#16A34A",
    "People": "#6B7280",
    "Cyber & Tech": "#0891B2",
    "Global Banks": "#7C3AED",
}

# Head of Internal Audit Department — shown top-right
PRAGATI_NAME = "Pragati"
PRAGATI_TITLE = "Head of Internal Audit"
# NOTE: paste your base64 JPEG string here to show the photo.
PRAGATI_PHOTO_B64 = "PASTE_YOUR_EXISTING_BASE64_STRING_HERE"

AUDIT_TERMS = [
    "internal audit", "external audit", "audit committee", "auditor",
    "audit finding", "audit findings", "internal control", "internal controls",
    "control weakness", "control weaknesses", "control deficiency",
    "control deficiencies", "governance", "risk management", "operational risk",
    "model risk", "compliance", "regulatory", "regulation", "supervision",
    "supervisory", "enforcement", "aml", "anti-money laundering", "kyc",
    "sanctions", "fraud", "misconduct", "financial crime",
    "bank", "banking", "lender", "rbi", "central bank", "basel", "npa",
    "asset quality", "provisioning", "capital adequacy", "credit risk",
    "liquidity", "penalty", "fined", "probe", "investigation", "whistleblower",
    "disclosure", "restatement", "irregularities", "lapses",
]

ALERT_TERMS = [
    "enforcement", "penalty", "fined", "fine", "fraud", "misconduct",
    "investigation", "probe", "money laundering", "aml", "sanctions",
    "irregularities", "lapses", "control deficiency", "restatement",
    "whistleblower", "settlement",
]

BANKING_CONTEXT_TERMS = [
    "banking", "banker", "bankers", "banking industry", "commercial bank",
    "retail bank", "investment bank", "central bank", "private bank", "public sector bank",
    "financial institution", "financial institutions", "financial services",
    "lender", "lenders", "nbfc", "non-bank financial company", "credit union",
    "deposit", "deposits", "loan", "loans", "mortgage", "payment bank",
    "rbi", "basel", "capital adequacy", "credit risk", "liquidity", "asset quality",
    "financial crime", "aml", "kyc", "money laundering", "sanctions",
    "banking regulator", "bank regulator", "chief risk officer",
    "chief audit executive", "internal audit", "audit committee",
    "bank of america", "jpmorgan", "jpmorgan chase", "citigroup", "citi",
    "hsbc", "barclays", "deutsche bank", "ubs", "bnp paribas", "santander",
    "standard chartered", "goldman sachs", "morgan stanley", "wells fargo",
    "icbc", "mufg", "mizuho",
]

CATEGORY_TERMS = {
    "Transformation": [
        "digital transformation", "modernization", "modernisation", "core banking",
        "automation", "artificial intelligence", "generative ai", "genai",
        "machine learning", "cloud", "digital banking", "technology transformation",
        "operating model",
    ],
    "Regulation": [
        "regulation", "regulatory", "rbi", "basel", "prudential", "supervision",
        "supervisory", "enforcement", "aml", "anti-money laundering", "kyc",
        "sanctions", "capital requirements", "regulatory capital", "compliance",
    ],
    "People": [
        "appointed", "appointment", "ceo", "cfo", "cro", "ciso", "chief audit",
        "internal audit", "audit committee", "board", "director", "chairman",
        "chairwoman", "leadership", "executive",
    ],
    "Cyber & Tech": [
        "cybersecurity", "cyber security", "cyber attack", "ransomware",
        "data breach", "malware", "phishing", "technology", "artificial intelligence",
        "generative ai", "machine learning", "cloud", "automation", "digital fraud",
    ],
    "Global Banks": [
        "hsbc", "jpmorgan", "jpmorgan chase", "citi", "citigroup", "barclays",
        "deutsche bank", "ubs", "bnpparibas", "bnp paribas", "santander",
        "standard chartered", "bank of america", "goldman sachs", "morgan stanley",
        "wells fargo", "ing", "icbc", "mufg", "mizuho",
    ],
}


def _term_present(text, term):
    """Match a term as a phrase/word, not as an arbitrary substring."""
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])", text))


def classify_category(title, description, hint=None):
    """Classify banking/audit news and reject generic consumer/technology stories."""
    text = f"{title} {description}".lower()

    # Do NOT treat the word 'bank' alone as banking context.
    # This prevents 'power bank', 'powerbank', etc. from entering the feed.
    banking_hits = sum(
        1 for term in BANKING_CONTEXT_TERMS
        if _term_present(text, term)
    )

    if banking_hits == 0:
        return None

    scores = {
        category: sum(
            1 for term in terms
            if _term_present(text, term)
        )
        for category, terms in CATEGORY_TERMS.items()
    }

    best_category = max(scores, key=scores.get)
    if scores[best_category] == 0:
        return None

    return best_category


def placeholder_data_uri(hex_color="#94A3B8"):
    """
    Inline, URL-encoded newspaper SVG used as the thumbnail fallback.
    Returned as a data: URI so it needs no network call and cannot itself fail.
    """
    c = hex_color.replace("#", "%23")
    return (
        "data:image/svg+xml;charset=utf-8,"
        "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' "
        f"stroke='{c}' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'%3E"
        "%3Cpath d='M4 5h13a1 1 0 0 1 1 1v12a2 2 0 0 0 2 2H5a1 1 0 0 1-1-1V5z'/%3E"
        "%3Cpath d='M18 8h2a1 1 0 0 1 1 1v9a2 2 0 0 1-2 2'/%3E"
        "%3Cpath d='M7 8h7'/%3E%3Cpath d='M7 11.5h7'/%3E"
        "%3Cpath d='M7 15h4'/%3E%3Cpath d='M13.5 15h.5'/%3E"
        "%3C/svg%3E"
    )


RISK_THEMES = {
    "Financial crime / AML": (["aml", "anti-money laundering", "money laundering", "kyc", "financial crime", "sanctions"], "#DC2626"),
    "Enforcement / penalties": (["enforcement", "penalty", "fined", "fine", "settlement", "supervisory action"], "#EA580C"),
    "Fraud & misconduct": (["fraud", "misconduct", "irregularities", "lapses", "whistleblower"], "#B45309"),
    "Credit & asset quality": (["npa", "asset quality", "provisioning", "credit risk", "bad loan", "slippage"], "#7C3AED"),
    "Technology & AI risk": (["artificial intelligence", "generative ai", "cloud", "automation", "core banking", "outage"], "#2563EB"),
}


def ist_now_str():
    """Return the current Indian Standard Time for the dashboard header."""
    from datetime import datetime, timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).strftime("%d %b %Y · %H:%M IST")


# ---------------------------------------------------------
# 3. KEY RESOLUTION & SCORING
# ---------------------------------------------------------

def _lookup_secret(*names):
    for name in names:
        val = os.getenv(name, "").strip()
        if val:
            return val
    try:
        for name in names:
            val = st.secrets.get(name, "")
            if val:
                return str(val).strip()
    except Exception:
        pass
    return ""

def format_relative_time(value):
    """Format an article timestamp for the newsroom cards."""
    if not value:
        return "Recent"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        minutes = max(0, int((now - dt.astimezone(timezone.utc)).total_seconds() / 60))
        if minutes < 60:
            return f"{minutes}m ago"
        if minutes < 1440:
            return f"{minutes // 60}h ago"
        if minutes < 2880:
            return "Yesterday"
        return f"{minutes // 1440}d ago"
    except Exception:
        return "Recent"


def classify_category(title, description, hint=None):
    """Classify only banking-relevant articles into the four newsroom categories."""
    text = f"{title} {description}".lower()

    # Hard gate: a generic technology/people/business story must contain
    # explicit banking or financial-institution context before it can enter
    # the audit-news feed. This prevents unrelated stories such as IBM
    # workplace/expansion articles from being labelled Transformation.
    banking_hits = sum(1 for term in BANKING_CONTEXT_TERMS if term in text)
    if banking_hits == 0:
        return None

    scores = {
        category: sum(1 for term in terms if term in text)
        for category, terms in CATEGORY_TERMS.items()
    }

    best_category = max(scores, key=scores.get)
    if scores[best_category] == 0:
        return None

    return best_category


def calculate_audit_relevance(title, description):
    """Return a simple 0-40 audit-relevance signal score."""
    text = f"{title} {description}".lower()
    score = 0
    for term in AUDIT_TERMS:
        if term in text:
            score += 2 if " " in term else 1
    return min(score, 40)


@st.cache_data(ttl=300, show_spinner=False)
def load_news(api_key, lookback_days, min_relevance, fuzzy_threshold, selected_categories):
    """Fetch, classify and filter the NewsData.io briefing."""
    categories = tuple(selected_categories)
    raw, errors, stats = npv.fetch_all(
        {"newsdata": api_key},
        lookback_days=lookback_days,
        categories=list(categories),
        fuzzy_threshold=fuzzy_threshold,
        max_workers=4,
    )

    articles = []
    for row in raw:
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        description = str(row.get("description") or row.get("content") or "").strip()
        category = classify_category(title, description, row.get("category_hint"))
        if category is None or category not in categories:
            continue
        relevance = calculate_audit_relevance(title, description)
        if relevance < min_relevance:
            continue
        articles.append({
            "title": title,
            "description": description,
            "url": str(row.get("url") or "#"),
            "image_url": str(row.get("image_url") or ""),
            "source": str(row.get("source") or "Unknown"),
            "publishedAt": row.get("published_at") or "",
            "category": category,
            "audit_relevance": relevance,
        })

    articles.sort(
        key=lambda item: (item.get("publishedAt") or "", item.get("audit_relevance", 0)),
        reverse=True,
    )
    stats = dict(stats or {})
    stats["dropped_low_relevance"] = max(0, int(stats.get("unique", 0)) - len(articles))
    stats["kept"] = len(articles)
    return articles, errors, stats


# ---------------------------------------------------------
# 4. LIVE MARKET SNAPSHOT
# ---------------------------------------------------------

MARKET_TICKERS = [
    ("^BSESN",   "SENSEX",      "BSE 30"),
    ("^NSEI",    "NIFTY 50",    "NSE"),
    ("^NSEBANK", "BANK NIFTY",  "NSE Banks"),
    ("USDINR=X", "USD / INR",   "Spot FX"),
    ("BZ=F",     "Brent Crude", "USD/bbl"),
    ("GC=F",     "Gold",        "USD/oz"),
]

YF_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YF_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AuditIntel/1.0)"}


def fetch_quote(symbol):
    resp = requests.get(
        YF_CHART_URL.format(symbol=symbol),
        params={"range": "1d", "interval": "5m"},
        headers=YF_HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    meta = resp.json()["chart"]["result"][0]["meta"]

    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")

    if price is None or not prev:
        raise ValueError("No price data returned")

    change = price - prev
    pct = (change / prev) * 100
    return {"price": float(price), "change": float(change), "pct": float(pct)}


@st.cache_data(ttl=180, show_spinner=False)
def load_market_snapshot():
    results = {}

    with ThreadPoolExecutor(max_workers=len(MARKET_TICKERS)) as executor:
        futures = {
            executor.submit(fetch_quote, symbol): symbol
            for symbol, _, _ in MARKET_TICKERS
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                results[symbol] = future.result()
            except Exception:
                results[symbol] = None

    return results, ist_now_str()


def render_market_panel():
    quotes, stamp = load_market_snapshot()

    rows_html = ""
    for symbol, name, sub in MARKET_TICKERS:
        q = quotes.get(symbol)

        if not q:
            rows_html += (
                f'<div class="mkt-row">'
                f'<div><div class="mkt-name">{name}</div><div class="mkt-sub">{sub}</div></div>'
                f'<div class="mkt-right"><div class="mkt-price" style="color:#9CA3AF;">—</div>'
                f'<div class="mkt-chg" style="color:#9CA3AF;">unavailable</div></div>'
                f'</div>'
            )
            continue

        cls = "mkt-up" if q["pct"] >= 0 else "mkt-down"
        arrow = "▲" if q["pct"] >= 0 else "▼"
        rows_html += (
            f'<div class="mkt-row">'
            f'<div><div class="mkt-name">{name}</div><div class="mkt-sub">{sub}</div></div>'
            f'<div class="mkt-right"><div class="mkt-price">{q["price"]:,.2f}</div>'
            f'<div class="mkt-chg {cls}">{arrow} {abs(q["change"]):,.2f} ({abs(q["pct"]):.2f}%)</div></div>'
            f'</div>'
        )

    st.markdown(f"""
    <div class="side-panel">
        <div class="side-panel-title">📈 Live Market Snapshot</div>
        {rows_html}
        <div class="mkt-stamp">Last refreshed {stamp} · delayed data, indicative only</div>
    </div>
    """, unsafe_allow_html=True)


# ---------------------------------------------------------
# 5. RISK RADAR + PRIORITY ALERTS
# ---------------------------------------------------------

def render_risk_radar(rows):
    if not rows:
        return

    counts = {}
    for theme, (terms, color) in RISK_THEMES.items():
        n = 0
        for row in rows:
            text = f'{row["title"]} {row["description"]}'.lower()
            if any(t in text for t in terms):
                n += 1
        counts[theme] = (n, color)

    peak = max((n for n, _ in counts.values()), default=0)
    if peak == 0:
        return

    rows_html = ""
    for theme, (n, color) in sorted(counts.items(), key=lambda x: x[1][0], reverse=True):
        width = int((n / peak) * 100) if peak else 0
        rows_html += (
            f'<div class="risk-row">'
            f'<div class="risk-head"><span class="risk-name">{theme}</span>'
            f'<span class="risk-count">{n}</span></div>'
            f'<div class="risk-track"><div class="risk-fill" style="width:{width}%; background:{color};"></div></div>'
            f'</div>'
        )

    st.markdown(f"""
    <div class="side-panel">
        <div class="side-panel-title">🎯 Risk Radar · Theme Exposure</div>
        {rows_html}
    </div>
    """, unsafe_allow_html=True)


def render_priority_alerts(rows, limit=5):
    flagged = []
    for row in rows:
        text = f'{row["title"]} {row["description"]}'.lower()
        hits = [t for t in ALERT_TERMS if t in text]
        if hits:
            flagged.append((len(hits), row, hits[0]))

    if not flagged:
        return

    flagged.sort(key=lambda x: (x[0], x[1]["audit_relevance"]), reverse=True)

    items_html = ""
    for _, row, tag in flagged[:limit]:
        items_html += (
            f'<a class="alert-item" href="{row["url"]}" target="_blank">'
            f'<div class="alert-tag">⚠ {tag.upper()} · {row["source"]}</div>'
            f'<div class="alert-text">{row["title"]}</div>'
            f'</a>'
        )

    st.markdown(f"""
    <div class="side-panel">
        <div class="side-panel-title">🚨 Priority Alerts ({len(flagged)})</div>
        {items_html}
    </div>
    """, unsafe_allow_html=True)


def render_source_panel(rows, limit=5):
    if not rows:
        return

    counter = Counter(r["source"] for r in rows)
    rows_html = "".join(
        f'<div class="pulse-row"><span>{name}</span><span class="pulse-value">{n}</span></div>'
        for name, n in counter.most_common(limit)
    )

    st.markdown(f"""
    <div class="side-panel">
        <div class="side-panel-title">📰 Top Sources</div>
        {rows_html}
    </div>
    """, unsafe_allow_html=True)


# ---------------------------------------------------------
# 6. TOP NAVIGATION
# ---------------------------------------------------------

if PRAGATI_PHOTO_B64 and not PRAGATI_PHOTO_B64.startswith("PASTE_"):
    avatar_html = (
        f'<img src="data:image/jpeg;base64,{PRAGATI_PHOTO_B64}" '
        f'class="avatar-photo" alt="{PRAGATI_NAME}" />'
    )
else:
    avatar_html = f'<div class="avatar-circle-lg">{PRAGATI_NAME[:1].upper()}</div>'

# Emblem: an audit lens (magnifier) whose glass contains a rising analytics
# bar chart, framed by a scanning arc — "examine + measure + monitor".
LOGO_SVG = """
<svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
  <path d="M26.6 8.4a13 13 0 0 1 .9 13.4" stroke="#FFFFFF" stroke-opacity="0.42"
        stroke-width="2" stroke-linecap="round"/>
  <path d="M5.2 22.6a13 13 0 0 1 .5-13.9" stroke="#FFFFFF" stroke-opacity="0.42"
        stroke-width="2" stroke-linecap="round"/>
  <circle cx="14.6" cy="14.6" r="8.2" stroke="#FFFFFF" stroke-width="2.4"/>
  <circle cx="14.6" cy="14.6" r="8.2" fill="#FFFFFF" fill-opacity="0.14"/>
  <rect x="10.7" y="15.1" width="2.25" height="4.5" rx="1.12" fill="#FFFFFF"/>
  <rect x="13.9" y="12.2" width="2.25" height="7.4" rx="1.12" fill="#FFFFFF"/>
  <rect x="17.1" y="9.6"  width="2.25" height="10"  rx="1.12" fill="#FFFFFF"/>
  <path d="M20.9 20.9 L26.4 26.4" stroke="#FFFFFF" stroke-width="3.1"
        stroke-linecap="round"/>
</svg>
"""

st.markdown(f"""
<div class="topnav">
    <div class="topnav-left">
        <div class="logo-icon">{LOGO_SVG}</div>
        <div class="logo-lockup">
            <div class="logo-textrow">
                <div class="logo-text">Audit<span class="lt-accent">&nbsp;Intelligence</span></div>
                <span class="live-pill"><span class="live-dot"></span>Live</span>
            </div>
            <div class="logo-sub"><span class="logo-rule"></span>Global Banking Risk &amp; Controls Briefing</div>
        </div>
    </div>
    <div class="topnav-user">
        <div class="topnav-user-meta">
            <div class="topnav-user-label">Prepared for</div>
            <div class="topnav-user-name">{PRAGATI_NAME}</div>
            <div class="topnav-user-title">{PRAGATI_TITLE}</div>
            <div class="topnav-user-stamp">{ist_now_str()}</div>
        </div>
        {avatar_html}
    </div>
</div>
""", unsafe_allow_html=True)


# ---------------------------------------------------------
def classify_category(title, description, hint=None):
    """Classify banking/audit news and reject generic consumer/technology stories."""
    text = f"{title} {description}".lower()

    # Hard banking relevance gate.
    banking_hits = sum(
        1 for term in BANKING_CONTEXT_TERMS
        if _term_present(text, term)
    )
    if banking_hits == 0:
        return None

    scores = {
        category: sum(1 for term in terms if _term_present(text, term))
        for category, terms in CATEGORY_TERMS.items()
    }
    best_category = max(scores, key=scores.get)
    if scores[best_category] == 0:
        return None
    return best_category


# 7. SIDEBAR CONTROL CENTER
# ---------------------------------------------------------

api_keys = get_api_keys()
hard_refresh = False

with st.sidebar:
    active_view = st.session_state.get("active_view", "All News")

    st.markdown(
        """
        <div class="sidebar-brand">
            <div class="sidebar-brand-icon">⌂</div>
            <div>
                <div class="sidebar-brand-title">News Intelligence</div>
                <div class="sidebar-brand-sub">Audit &amp; Banking Briefing</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    nav_items = [
        ("⌂", "All News", "All News"),
        ("▦", "Categories", "All News"),
        ("♧", "Global Banks", "Global Banks"),
        ("♡", "Watchlist", "Watchlist"),
        ("▱", "Saved", "Saved"),
        ("⇧", "Export", "Export"),
        ("⚙", "Diagnostics", "Diagnostics"),
    ]

    for icon, label, target in nav_items:
        if st.button(
            f"{icon}   {label}",
            key=f"sidebar_nav_{label.lower().replace(' ', '_')}",
            use_container_width=True,
        ):
            st.session_state.active_view = target
            active_view = target
            st.rerun()

    st.markdown(
        f"""
        <div class="sidebar-live-card">
            <div class="sidebar-live-kicker"><span class="sidebar-live-dot"></span> LIVE DATA</div>
            <div class="sidebar-provider-row">
                <span class="sidebar-provider-name">NewsData.io</span>
                <span class="sidebar-active-pill">Active</span>
            </div>
            <div class="sidebar-stamp">
                Last updated<br>{escape(str(st.session_state.get("last_refresh", "Not loaded yet")))}
            </div>
        </div>
        <div class="sidebar-section-label">Quick Controls</div>
        """,
        unsafe_allow_html=True,
    )

    refresh_clicked = st.button(
        "⟳  Refresh All Data",
        use_container_width=True,
        key="refresh_all",
    )
    if refresh_clicked:
        hard_refresh = True

    st.markdown(
        '<div class="sidebar-quick-title">Filters &amp; Settings</div>',
        unsafe_allow_html=True,
    )

    lookback_days = st.slider(
        "Lookback Window",
        min_value=1,
        max_value=30,
        value=7,
        label_visibility="collapsed",
        help="Controls the requested news lookback window.",
    )

    min_relevance = st.slider(
        "Minimum Audit Relevance",
        min_value=0,
        max_value=40,
        value=0,
        step=5,
        label_visibility="collapsed",
        help="Raise this to keep only higher-signal stories.",
    )

    dedup_mode = st.select_slider(
        "Duplicate Removal",
        options=["Loose", "Balanced", "Aggressive"],
        value="Balanced",
        label_visibility="collapsed",
        help="Controls how aggressively similar headlines are merged.",
    )

    # Keep all five intelligence streams available to the main newsroom navigation.
    selected_categories = list(CATEGORIES.keys())

    fuzzy_threshold = {
        "Loose": 0.85,
        "Balanced": 0.72,
        "Aggressive": 0.58,
    }[dedup_mode]


if hard_refresh:
    load_news.clear()
    load_market_snapshot.clear()
    st.session_state.pop("news_loaded", None)


# ---------------------------------------------------------
# 8. DATA INGESTION & FILTERING
# ---------------------------------------------------------

active_keys = tuple(sorted((p, bool(k)) for p, k in api_keys.items()))
params_key = (
    lookback_days,
    min_relevance,
    fuzzy_threshold,
    tuple(sorted(selected_categories)),
    active_keys,
)

if ("news_loaded" not in st.session_state) or (
    st.session_state.get("params_key") != params_key
):
    with st.spinner("Compiling the audit intelligence briefing..."):
        articles, errors, stats = load_news(
            api_keys["newsdata"],
            lookback_days,
            min_relevance,
            fuzzy_threshold,
            tuple(sorted(selected_categories)),
        )

    st.session_state.news = articles
    st.session_state.news_errors = errors
    st.session_state.news_stats = stats
    st.session_state.news_loaded = True
    st.session_state.params_key = params_key
    st.session_state.last_refresh = ist_now_str()

articles = st.session_state.get("news", [])
errors = st.session_state.get("news_errors", [])
stats = st.session_state.get("news_stats", {})

filtered = (
    [a for a in articles if a["category"] in selected_categories]
    if selected_categories else []
)

# The main category buttons are a display filter, not an ingestion filter.
if st.session_state.get("active_view") not in (None, "All News"):
    if st.session_state.active_view in CATEGORIES:
        filtered = [
            a for a in filtered
            if a.get("category") == st.session_state.active_view
        ]

if not api_keys.get("newsdata"):
    st.error("NewsData.io API key is not configured.")
    st.stop()


# ---------------------------------------------------------
# 9. SIDEBAR DIAGNOSTICS & EXECUTIVE PANELS
# ---------------------------------------------------------

with st.sidebar:
    with st.expander("🔎 Ingestion Diagnostics", expanded=False):
        if stats:
            pp = stats.get("per_provider", {})
            exhausted = set(stats.get("exhausted", []))
            rows = ""

            for pid, meta in npv.PROVIDERS.items():
                s = pp.get(pid, {})

                if not api_keys.get(pid):
                    state, color = "not configured", "#9CA3AF"
                elif pid in exhausted:
                    state, color = "quota reached", "#DC2626"
                elif s.get("used"):
                    state, color = "active", "#16A34A"
                else:
                    state, color = "idle", "#6B7280"

                rows += (
                    f'<tr>'
                    f'<td style="padding:5px 8px 5px 0;font-weight:600;">{meta["label"]}</td>'
                    f'<td style="padding:5px 8px 5px 0;">{s.get("requests",0)} req</td>'
                    f'<td style="padding:5px 8px 5px 0;">{s.get("articles",0)} articles</td>'
                    f'<td style="color:{color};font-weight:600;">{state}</td>'
                    f'</tr>'
                )

            d = stats.get("dedup", {})
            st.markdown(
                f"""
                <table style="width:100%;font-size:11px;color:#111827;border-collapse:collapse;">
                    {rows}
                </table>
                <div style="font-size:11px;color:#4B5563;line-height:1.8;margin-top:8px;">
                    <b>{stats.get('raw',0)}</b> raw ·
                    <b>{stats.get('unique',0)}</b> unique ·
                    <b>{stats.get('kept',0)}</b> retained<br>
                    URL dupes <b>{d.get('by_url',0)}</b> ·
                    title dupes <b>{d.get('by_title',0)}</b> ·
                    fuzzy dupes <b>{d.get('by_fuzzy',0)}</b>
                </div>
                """,
                unsafe_allow_html=True,
            )

            if stats.get("failover"):
                st.warning("NewsData.io reported a quota or request limit.")

        for err in errors:
            st.markdown(
                f"<div style='font-size:11px;color:#B45309;margin-top:5px;'>• {escape(str(err))}</div>",
                unsafe_allow_html=True,
            )

    st.markdown(
        '<div style="font-size:10px;font-weight:800;letter-spacing:1px;text-transform:uppercase;'
        'color:#6B7280;margin:16px 0 8px;">Feed Pulse</div>',
        unsafe_allow_html=True,
    )

    today_count = (
        sum(1 for a in filtered if format_relative_time(a["publishedAt"]) == "Today")
        if filtered else 0
    )
    unique_sources = len(set(a["source"] for a in filtered)) if filtered else 0

    st.markdown(
        f"""
        <div class="side-panel" style="padding:12px 14px;">
            <div class="pulse-row"><span>Total Stories</span><span class="pulse-value">{len(filtered)}</span></div>
            <div class="pulse-row"><span>Published Today</span><span class="pulse-value">{today_count}</span></div>
            <div class="pulse-row"><span>Unique Sources</span><span class="pulse-value">{unique_sources}</span></div>
            <div class="pulse-row"><span>Last Pull</span><span class="pulse-value" style="font-size:10px;">{escape(str(st.session_state.get("last_refresh", "not yet")))}</span></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("📊 Risk & Priority Signals", expanded=False):
        render_priority_alerts(filtered, limit=5)
        render_risk_radar(filtered)
        render_source_panel(filtered)

    with st.expander("📈 Market Snapshot", expanded=False):
        render_market_panel()

    if filtered:
        st.markdown(
            '<div style="font-size:10px;font-weight:800;letter-spacing:1px;text-transform:uppercase;'
            'color:#6B7280;margin:16px 0 8px;">Export</div>',
            unsafe_allow_html=True,
        )
        df_export = pd.DataFrame(filtered)
        csv = df_export.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="Download Briefing CSV",
            data=csv,
            file_name=f"audit_intel_briefing_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
            use_container_width=True,
            key="download_csv_sidebar",
        )


# ---------------------------------------------------------

# Main newsroom category switcher. This changes the visible feed without
# triggering a new API request because the fetched dataset remains cached.
if "active_view" not in st.session_state:
    st.session_state.active_view = "All News"

view_options = [
    ("All News", "All News"),
    ("Transformation", "Transformation"),
    ("Regulation", "Regulation"),
    ("People", "People"),
    ("Global Banks", "Global Banks"),
]

st.markdown(
    '<div class="feed-section-title" style="margin-top:0;">'
    '<span>News Feed</span><span>Browse by intelligence stream</span></div>',
    unsafe_allow_html=True,
)

nav_cols = st.columns(len(view_options), gap="small")
for col, (key, label) in zip(nav_cols, view_options):
    with col:
        if st.button(
            label,
            key=f"main_category_{key.lower().replace(' ', '_')}",
            use_container_width=True,
        ):
            st.session_state.active_view = key
            st.rerun()

st.markdown(
    '<div class="category-filter-status">'
    '<span>ACTIVE STREAM</span><b>' + escape(st.session_state.active_view) + '</b>'
    '</div>',
    unsafe_allow_html=True,
)

# 10. NEWS-FIRST LANDING PAGE
# ---------------------------------------------------------

if filtered:
    st.markdown(
        f"""
        <div class="news-masthead">
            <div>
                <div class="news-kicker"><span class="live-dot"></span> LIVE BANKING NEWS</div>
                <div class="news-title">Latest Audit Intelligence</div>
                <div class="news-subtitle">
                    Global banking stories across transformation, regulation, people and major banks.
                    Updated {escape(str(st.session_state.get("last_refresh", "just now")))}.
                </div>
            </div>
            <div class="news-metrics">
                <div><b>{len(filtered)}</b><span>stories</span></div>
                <div><b>{len(set(a["source"] for a in filtered))}</b><span>sources</span></div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        """
        <div class="news-masthead">
            <div>
                <div class="news-kicker"><span class="live-dot"></span> LIVE BANKING NEWS</div>
                <div class="news-title">Latest Audit Intelligence</div>
                <div class="news-subtitle">No stories matched the current filters.</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# 11. RENDER HELPERS
# ---------------------------------------------------------

def render_featured(article):
    color = CATEGORY_COLORS.get(article["category"], "#2563EB")
    label = CATEGORY_DISPLAY.get(article["category"], article["category"])
    rel_time = format_relative_time(article["publishedAt"])
    title = escape(str(article.get("title") or "Untitled story"))
    description = escape(
        str(article.get("description") or "Independent institutional briefing coverage.")
    )
    source = escape(str(article.get("source") or "Unknown source"))
    url = escape(str(article.get("url") or "#"), quote=True)
    image_url = escape(str(article.get("image_url") or ""), quote=True)
    fallback = placeholder_data_uri(color)

    image = (
        f'<img class="featured-image" src="{image_url}" alt="" '
        f'onerror="this.onerror=null;this.src=\'{fallback}\';" />'
        if image_url else
        f'<img class="featured-image" src="{fallback}" alt="" />'
    )

    st.markdown(
        f"""
        <article class="featured-news-card">
            <a href="{url}" target="_blank" rel="noopener noreferrer" class="featured-image-wrap">
                {image}
            </a>
            <div class="featured-news-body">
                <div class="featured-news-label" style="color:{color};">{escape(label)} · FEATURED</div>
                <a href="{url}" target="_blank" rel="noopener noreferrer" class="featured-news-title">
                    {title}
                </a>
                <div class="featured-news-desc">{description}</div>
                <div class="featured-news-meta">
                    <span>{source}</span>
                    <span>{rel_time}</span>
                </div>
            </div>
        </article>
        """,
        unsafe_allow_html=True,
    )


def render_top_stories(rows, rotation_seconds=5):
    """Render four priority stories in a compact rotating featured panel."""
    if not rows:
        return

    top = sorted(
        rows,
        key=lambda item: (
            float(item.get("audit_relevance", 0) or 0),
            item.get("publishedAt") or "",
        ),
        reverse=True,
    )[:4]

    slides = []
    for idx, article in enumerate(top, 1):
        color = CATEGORY_COLORS.get(article["category"], "#2563EB")
        label = CATEGORY_DISPLAY.get(article["category"], article["category"])
        title = escape(str(article.get("title") or "Untitled story"))
        description = escape(
            str(article.get("description") or "Independent institutional briefing coverage.")
        )
        source = escape(str(article.get("source") or "Unknown source"))
        url = escape(str(article.get("url") or "#"), quote=True)
        rel_time = escape(str(format_relative_time(article.get("publishedAt", ""))))
        image_url = escape(str(article.get("image_url") or ""), quote=True)
        fallback = placeholder_data_uri(color)

        if image_url:
            image = (
                f'<img class="top-story-image" src="{image_url}" alt="" '
                f'onerror="this.onerror=null;this.src=\'{fallback}\';">'
            )
        else:
            image = f'<img class="top-story-image" src="{fallback}" alt="">'

        slides.append(f"""
<div class="top-story-slide" data-index="{idx}">
  <a href="{url}" target="_blank" rel="noopener noreferrer" class="top-story-image-wrap">{image}</a>
  <div class="top-story-body">
    <div class="top-story-number">{idx}</div>
    <div class="top-story-label" style="color:{color};">{escape(label)} · TOP STORY</div>
    <a href="{url}" target="_blank" rel="noopener noreferrer" class="top-story-title">{title}</a>
    <div class="top-story-desc">{description}</div>
    <div class="top-story-meta"><span>{source}</span><span>{rel_time}</span></div>
  </div>
</div>
""".strip())

    dots = "".join(
        f'<span class="top-story-dot {"active" if i == 1 else ""}"></span>'
        for i in range(1, len(top) + 1)
    )

    html = f"""
<!doctype html>
<html>
<head>
<style>
*{{box-sizing:border-box}}
html,body{{margin:0;padding:0;background:transparent;font-family:Inter,Arial,sans-serif}}
.top-carousel{{background:linear-gradient(135deg,#0B1220 0%,#111C36 55%,#1B2B61 100%);border-radius:16px;padding:14px;color:#fff;box-shadow:0 8px 22px rgba(11,18,32,.12)}}
.top-carousel-head{{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:10px}}
.top-carousel-kicker{{font-size:9px;font-weight:800;letter-spacing:1.4px;text-transform:uppercase;color:#93C5FD}}
.top-carousel-title{{font-size:19px;font-weight:900;letter-spacing:-.4px;margin-top:2px}}
.top-carousel-sub{{font-size:10px;color:rgba(255,255,255,.62);margin-top:2px}}
.top-carousel-counter{{font-size:9px;font-family:monospace;color:rgba(255,255,255,.62);white-space:nowrap}}
.top-carousel-dots{{display:inline-flex;gap:4px;margin-right:7px;vertical-align:middle}}
.top-carousel-dot{{width:5px;height:5px;border-radius:50%;background:rgba(255,255,255,.25);display:inline-block}}
.top-carousel-dot.active{{background:#93C5FD}}
.top-story-slide{{display:none;grid-template-columns:34% 66%;height:225px}}
.top-story-slide.active{{display:grid;animation:topFade .35s ease}}
.top-story-image-wrap{{display:block;height:225px;overflow:hidden;border-radius:11px 0 0 11px;background:#1E293B}}
.top-story-image{{width:100%;height:225px;object-fit:cover;display:block}}
.top-story-body{{height:225px;background:rgba(255,255,255,.055);padding:18px 21px;border:1px solid rgba(255,255,255,.08);border-left:0;border-radius:0 11px 11px 0;display:flex;flex-direction:column;justify-content:center}}
.top-story-number{{width:23px;height:23px;border-radius:6px;background:rgba(255,255,255,.10);display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:900;margin-bottom:8px}}
.top-story-label{{font-size:8px;font-weight:850;letter-spacing:.9px;text-transform:uppercase;margin-bottom:6px}}
.top-story-title{{color:#fff;text-decoration:none;font-size:20px;line-height:1.18;font-weight:900;letter-spacing:-.35px}}
.top-story-title:hover{{color:#BFDBFE}}
.top-story-desc{{color:rgba(255,255,255,.70);font-size:11px;line-height:1.45;margin-top:7px;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}}
.top-story-meta{{display:flex;justify-content:space-between;gap:10px;padding-top:10px;margin-top:10px;border-top:1px solid rgba(255,255,255,.09);color:rgba(255,255,255,.55);font-size:9px;font-weight:650}}
@keyframes topFade{{from{{opacity:.3}}to{{opacity:1}}}}
@media(max-width:800px){{
.top-story-slide.active{{grid-template-columns:1fr;height:auto}}
.top-story-image-wrap,.top-story-image{{height:145px}}
.top-story-body{{height:170px;border-left:1px solid rgba(255,255,255,.08);border-radius:0 0 11px 11px}}
.top-story-title{{font-size:18px}}
}}
</style>
</head>
<body>
<section class="top-carousel">
  <div class="top-carousel-head">
    <div>
      <div class="top-carousel-kicker">● PRIORITY NEWS</div>
      <div class="top-carousel-title">Top Stories Today</div>
      <div class="top-carousel-sub">Automatically rotating high-priority intelligence</div>
    </div>
    <div class="top-carousel-counter">
      <span class="top-carousel-dots">{dots.replace('top-story-dot','top-carousel-dot')}</span>
      <span id="top-counter">1 / {len(top)}</span>
    </div>
  </div>
  {''.join(slides)}
</section>
<script>
(function(){{
  const slides=[...document.querySelectorAll('.top-story-slide')];
  const dots=[...document.querySelectorAll('.top-carousel-dot')];
  const counter=document.getElementById('top-counter');
  let current=0;
  function show(i){{
    slides.forEach((s,n)=>s.classList.toggle('active',n===i));
    dots.forEach((d,n)=>d.classList.toggle('active',n===i));
    if(counter) counter.textContent=(i+1)+' / '+slides.length;
  }}
  show(0);
  if(slides.length>1) setInterval(()=>{{current=(current+1)%slides.length;show(current)}},{int(rotation_seconds*1000)});
}})();
</script>
</body>
</html>
"""
    components.html(html, height=300, scrolling=False)


def render_category_grid(category, rows):
    """Render category stories in a two-column newsroom grid with feedback controls."""
    if not rows:
        return

    color = CATEGORY_COLORS.get(category, "#2563EB")
    label = CATEGORY_DISPLAY.get(category, category)

    st.markdown(
        dedent(f"""
        <section class="category-section">
            <div class="category-heading">
                <div class="category-heading-left">
                    <span class="category-accent" style="background:{color};"></span>
                    <span class="category-name">{escape(label)}</span>
                    <span class="category-count">{len(rows):02d} stories</span>
                </div>
            </div>
        </section>
        """).strip(),
        unsafe_allow_html=True,
    )

    for start in range(0, len(rows), 2):
        pair = rows[start:start + 2]
        cols = st.columns(2, gap="medium")

        for col, article in zip(cols, pair):
            with col:
                title = escape(str(article.get("title") or "Untitled story"))
                description = escape(
                    str(article.get("description") or "Independent institutional briefing coverage.")
                )
                source = escape(str(article.get("source") or "Unknown source"))
                url = escape(str(article.get("url") or "#"), quote=True)
                rel_time = escape(str(format_relative_time(article.get("publishedAt", ""))))
                image_url = escape(str(article.get("image_url") or ""), quote=True)
                fallback = placeholder_data_uri(color)
                preference = article.get("_personal_preference", 0.5)

                if image_url:
                    image = (
                        f'<img class="category-card-image" src="{image_url}" alt="" '
                        f'loading="lazy" referrerpolicy="no-referrer" '
                        f'onerror="this.onerror=null;this.src=\'{fallback}\';" />'
                    )
                else:
                    image = f'<img class="category-card-image" src="{fallback}" alt="" />'

                st.markdown(
                    f"""
                    <article class="category-card">
                        <a href="{url}" target="_blank" rel="noopener noreferrer" class="category-card-image-wrap">
                            {image}
                        </a>
                        <div class="category-card-body">
                            <div class="category-card-meta">
                                <span class="badge" style="background:{color};">{escape(label)}</span>
                                <span class="insight-date">{rel_time}</span>
                            </div>
                            <a href="{url}" target="_blank" rel="noopener noreferrer" class="category-card-title-link">
                                <div class="category-card-title">{title}</div>
                            </a>
                            <div class="category-card-desc">{description}</div>
                            <div class="category-card-footer">
                                <span class="source-chip">{source}</span>
                                <a href="{url}" target="_blank" rel="noopener noreferrer" class="read-link">Read source ↗</a>
                            </div>
                        </div>
                    </article>
                    """,
                    unsafe_allow_html=True,
                )






# ---------------------------------------------------------
# 12. MAIN NEWS FEED
# ---------------------------------------------------------
# News is the primary surface. A rotating 4-story priority strip appears first.

if not filtered:
    st.markdown(
        """
        <div class="empty-state-panel">
            <div style="font-size:18px;font-weight:800;color:#111827;">No briefing stories found</div>
            <div style="font-size:13.5px;color:#4B5563;margin-top:6px;">
                Open the sidebar and broaden the lookback window, lower the relevance floor,
                or enable additional categories.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
else:
    render_top_stories(filtered, rotation_seconds=5)

    st.markdown(
        '<div class="feed-section-title">'
        '<span>Latest Stories</span>'
        '<span>Chronological newsroom feed</span>'
        '</div>',
        unsafe_allow_html=True,
    )

    featured_url = filtered[0].get("url")
    if st.session_state.get("active_view") in CATEGORIES:
        categories_to_render = [st.session_state.active_view]
    else:
        categories_to_render = selected_categories

    for category in categories_to_render:
        category_rows = [
            a for a in filtered
            if a["category"] == category and a.get("url") != featured_url
        ]
        category_rows.sort(
            key=lambda x: x.get("publishedAt", ""),
            reverse=True,
        )
        render_category_grid(category, category_rows)