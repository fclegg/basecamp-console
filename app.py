import os
import re
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import streamlit as st

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas


# =========================
# Configuration
# =========================
APP_TITLE = "New Horizon Investigations — Basecamp Console"
DATA_DIR = Path("data")
SESSIONS_DIR = DATA_DIR / "sessions"
ASSETS_DIR = Path("assets")
DEFAULT_LOGO_PATH = ASSETS_DIR / "logo.png"
DB_PATH = DATA_DIR / "basecamp.sqlite3"
SHUTDOWN_FLAG = DATA_DIR / "shutdown.flag"

DEFAULT_AUTHORS = ["Basecamp", "Lead", "Investigator A", "Investigator B"]
DEFAULT_TAGS = ["voice", "footsteps", "EMF", "provocation", "response", "motion", "temp", "knock", "whisper"]

DEFAULT_EQUIPMENT = [
    {"name": "EMF Meter", "id": "EMF-01"},
    {"name": "EMF Meter", "id": "EMF-02"},
    {"name": "IR Camera", "id": "IR-01"},
    {"name": "Voice Recorder", "id": "VR-01"},
    {"name": "Spirit Box", "id": "SB-01"},
    {"name": "Thermal Camera", "id": "TH-01"},
]

EVIDENCE_TYPES = ["AUTO", "AUDIO", "VIDEO", "PHOTO", "DOC", "OTHER"]

# auto-type detection
EXT_TYPE = {
    ".wav": "AUDIO", ".mp3": "AUDIO", ".m4a": "AUDIO", ".aac": "AUDIO", ".flac": "AUDIO",
    ".mp4": "VIDEO", ".mov": "VIDEO", ".mkv": "VIDEO", ".avi": "VIDEO", ".wmv": "VIDEO",
    ".jpg": "PHOTO", ".jpeg": "PHOTO", ".png": "PHOTO", ".heic": "PHOTO", ".webp": "PHOTO",
    ".pdf": "DOC", ".txt": "DOC", ".doc": "DOC", ".docx": "DOC",
}


# =========================
# Utilities
# =========================
def now_local() -> datetime:
    return datetime.now()

def fmt_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def fmt_time(dt: datetime) -> str:
    return dt.strftime("%H:%M")

def safe_filename(name: str) -> str:
    name = name.strip()
    name = re.sub(r"[^\w\-. ]+", "_", name)
    name = re.sub(r"\s+", "_", name)
    return name[:180] if len(name) > 180 else name

def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

def connect_db() -> sqlite3.Connection:
    ensure_dirs()
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = connect_db()
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        started_at TEXT NOT NULL,
        ended_at TEXT,
        location TEXT,
        notes TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        mode TEXT NOT NULL,
        author TEXT NOT NULL,
        tags TEXT,
        text TEXT NOT NULL,
        linked_event_id INTEGER
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        author TEXT NOT NULL,
        title TEXT NOT NULL,
        severity INTEGER DEFAULT 1,
        tags TEXT,
        room TEXT,
        camera_label TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS evidence (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        evidence_code TEXT NOT NULL,
        original_name TEXT NOT NULL,
        stored_name TEXT NOT NULL,
        stored_path TEXT NOT NULL,
        type TEXT NOT NULL,
        captured_by TEXT,
        device TEXT,
        room TEXT,
        description TEXT,
        linked_event_id INTEGER
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS equipment (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        gear_id TEXT NOT NULL UNIQUE
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS equipment_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        gear_id TEXT NOT NULL,
        action TEXT NOT NULL,
        at TEXT NOT NULL,
        who TEXT NOT NULL,
        battery INTEGER,
        condition_notes TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS tracker (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        team_label TEXT NOT NULL,
        location TEXT NOT NULL,
        last_radio_call TEXT,
        needs_support INTEGER DEFAULT 0
    )
    """)

    con.commit()

    # seed equipment if empty
    cur.execute("SELECT COUNT(*) as c FROM equipment")
    if cur.fetchone()["c"] == 0:
        for item in DEFAULT_EQUIPMENT:
            cur.execute("INSERT OR IGNORE INTO equipment(name, gear_id) VALUES (?,?)", (item["name"], item["id"]))
        con.commit()

    con.close()

def get_session_folder(session_id: str) -> Path:
    p = SESSIONS_DIR / session_id
    p.mkdir(parents=True, exist_ok=True)
    (p / "evidence").mkdir(parents=True, exist_ok=True)
    (p / "reports").mkdir(parents=True, exist_ok=True)
    return p

def create_session(location: str = "", notes: str = "") -> str:
    con = connect_db()
    started = now_local()
    session_id = started.strftime("%Y%m%d_%H%M%S")
    con.execute(
        "INSERT INTO sessions(session_id, started_at, location, notes) VALUES (?,?,?,?)",
        (session_id, fmt_ts(started), location.strip() or None, notes.strip() or None),
    )
    con.commit()
    con.close()
    get_session_folder(session_id)
    return session_id

def end_session(session_id: str):
    con = connect_db()
    con.execute("UPDATE sessions SET ended_at = ? WHERE session_id = ?", (fmt_ts(now_local()), session_id))
    con.commit()
    con.close()

def fetchone(query: str, params: Tuple = ()) -> Optional[sqlite3.Row]:
    con = connect_db()
    cur = con.execute(query, params)
    row = cur.fetchone()
    con.close()
    return row

def fetchall(query: str, params: Tuple = ()) -> List[sqlite3.Row]:
    con = connect_db()
    cur = con.execute(query, params)
    rows = cur.fetchall()
    con.close()
    return rows

def execute(query: str, params: Tuple = ()):
    con = connect_db()
    con.execute(query, params)
    con.commit()
    con.close()

def next_evidence_counter(session_id: str) -> int:
    row = fetchone("SELECT COUNT(*) as c FROM evidence WHERE session_id = ?", (session_id,))
    return int(row["c"]) + 1

def evidence_code_for(session_id: str, ev_type: str) -> str:
    started = fetchone("SELECT started_at FROM sessions WHERE session_id = ?", (session_id,))
    date_part = datetime.strptime(started["started_at"], "%Y-%m-%d %H:%M:%S").strftime("%Y%m%d")
    counter = next_evidence_counter(session_id)
    return f"{date_part}_{counter:04d}_{ev_type.upper()}"

def detect_type_from_name(name: str) -> str:
    ext = Path(name).suffix.lower()
    return EXT_TYPE.get(ext, "OTHER")

def load_logo_bytes(path: Path) -> Optional[bytes]:
    try:
        if path.exists():
            return path.read_bytes()
    except Exception:
        pass
    return None


# =========================
# PDF Report
# =========================
def generate_pdf_report(session_id: str, logo_path: Optional[Path] = None) -> Path:
    session = fetchone("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
    if not session:
        raise ValueError("Session not found.")

    logs = fetchall("SELECT * FROM logs WHERE session_id = ? ORDER BY created_at ASC", (session_id,))
    events = fetchall("SELECT * FROM events WHERE session_id = ? ORDER BY created_at ASC", (session_id,))
    evidence = fetchall("SELECT * FROM evidence WHERE session_id = ? ORDER BY created_at ASC", (session_id,))
    equip = fetchall("SELECT * FROM equipment_log WHERE session_id = ? ORDER BY at ASC", (session_id,))
    tracker = fetchall("SELECT * FROM tracker WHERE session_id = ? ORDER BY team_label ASC", (session_id,))

    session_folder = get_session_folder(session_id)
    report_path = session_folder / "reports" / f"Report_{session_id}.pdf"

    c = canvas.Canvas(str(report_path), pagesize=letter)
    width, height = letter

    x_margin = 0.75 * inch
    y = height - 0.75 * inch

    if logo_path and logo_path.exists():
        try:
            c.drawImage(str(logo_path), x_margin, y - 0.6 * inch, width=2.0 * inch, height=0.6 * inch,
                        preserveAspectRatio=True, mask='auto')
        except Exception:
            pass

    c.setFont("Helvetica-Bold", 16)
    c.drawString(x_margin, y - 0.85 * inch, "Investigation Report Draft")
    y -= 1.2 * inch

    c.setFont("Helvetica", 11)
    c.drawString(x_margin, y, f"Session ID: {session_id}"); y -= 0.2 * inch
    c.drawString(x_margin, y, f"Started: {session['started_at']}"); y -= 0.2 * inch
    c.drawString(x_margin, y, f"Ended: {session['ended_at'] or '—'}"); y -= 0.2 * inch
    c.drawString(x_margin, y, f"Location: {session['location'] or '—'}"); y -= 0.35 * inch

    def section_title(title: str):
        nonlocal y
        if y < 1.3 * inch:
            c.showPage()
            y = height - 0.75 * inch
        c.setFont("Helvetica-Bold", 13)
        c.drawString(x_margin, y, title)
        y -= 0.3 * inch
        c.setFont("Helvetica", 10)

    def draw_wrapped(text: str, indent: float = 0.0):
        nonlocal y
        max_width = width - (x_margin * 2) - indent
        words = (text or "").split()
        line = ""
        for w in words:
            test = (line + " " + w).strip()
            if c.stringWidth(test, "Helvetica", 10) <= max_width:
                line = test
            else:
                if y < 1.1 * inch:
                    c.showPage()
                    y = height - 0.75 * inch
                    c.setFont("Helvetica", 10)
                c.drawString(x_margin + indent, y, line)
                y -= 0.18 * inch
                line = w
        if line:
            if y < 1.1 * inch:
                c.showPage()
                y = height - 0.75 * inch
                c.setFont("Helvetica", 10)
            c.drawString(x_margin + indent, y, line)
            y -= 0.22 * inch

    section_title("Timeline (Events + Notes)")
    merged = []
    for e in events:
        tags = ", ".join(json.loads(e["tags"]) if e["tags"] else [])
        extra = " — ".join([p for p in [e["room"], e["camera_label"], f"sev {e['severity']}", tags] if p])
        merged.append((e["created_at"], "EVENT", f"{e['title']}" + (f" ({extra})" if extra else "")))
    for l in logs:
        tags = ", ".join(json.loads(l["tags"]) if l["tags"] else [])
        merged.append((l["created_at"], l["mode"], f"{l['author']}: {l['text']}" + (f" [tags: {tags}]" if tags else "")))
    merged.sort(key=lambda t: t[0])
    for ts, kind, text in merged:
        draw_wrapped(f"{ts} — {kind}: {text}")

    section_title("Evidence List")
    if not evidence:
        draw_wrapped("No evidence logged.")
    else:
        for ev in evidence:
            draw_wrapped(f"{ev['evidence_code']} — {ev['type']} — {ev['stored_name']} — Captured by: {ev['captured_by'] or '—'}")
            meta = []
            if ev["device"]: meta.append(f"Device: {ev['device']}")
            if ev["room"]: meta.append(f"Room: {ev['room']}")
            if ev["description"]: meta.append(f"Notes: {ev['description']}")
            if meta:
                draw_wrapped(" | ".join(meta), indent=18)

    section_title("Equipment Usage")
    if not equip:
        draw_wrapped("No equipment activity logged.")
    else:
        for row in equip:
            draw_wrapped(
                f"{row['at']} — {row['action']} — {row['gear_id']} — {row['who']}"
                + (f" — battery {row['battery']}%" if row["battery"] is not None else "")
                + (f" — {row['condition_notes']}" if row["condition_notes"] else "")
            )

    section_title("Investigator Tracker")
    if not tracker:
        draw_wrapped("No tracker entries.")
    else:
        for t in tracker:
            draw_wrapped(
                f"{t['team_label']} — {t['location']} — last radio: {t['last_radio_call'] or '—'} — needs support: {'YES' if t['needs_support'] else 'no'}"
            )

    c.save()
    return report_path


# =========================
# UI
# =========================
def sidebar_config():
    st.sidebar.header("Basecamp Settings")

    st.sidebar.subheader("Authors")
    authors_text = st.sidebar.text_area(
        "One per line",
        value="\n".join(st.session_state.get("authors", DEFAULT_AUTHORS)),
        height=120
    )
    authors = [a.strip() for a in authors_text.splitlines() if a.strip()]
    st.session_state["authors"] = authors if authors else DEFAULT_AUTHORS

    st.sidebar.subheader("Suggested Tags")
    tags_text = st.sidebar.text_area(
        "One per line",
        value="\n".join(st.session_state.get("tags", DEFAULT_TAGS)),
        height=120
    )
    tags = [t.strip() for t in tags_text.splitlines() if t.strip()]
    st.session_state["tags"] = tags if tags else DEFAULT_TAGS

    st.sidebar.divider()
    st.sidebar.subheader("Logo")
    st.sidebar.caption("Default: assets/logo.png")
    upload = st.sidebar.file_uploader("Upload logo (png/jpg)", type=["png", "jpg", "jpeg"])
    if upload:
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        DEFAULT_LOGO_PATH.write_bytes(upload.getbuffer())
        st.sidebar.success("Saved logo to assets/logo.png")

def require_active_session() -> Optional[str]:
    return st.session_state.get("active_session_id")

def session_elapsed_str(started_at_str: str) -> str:
    started = datetime.strptime(started_at_str, "%Y-%m-%d %H:%M:%S")
    delta = now_local() - started
    secs = int(delta.total_seconds())
    if secs < 0:
        secs = 0
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def maybe_autorefresh(ms: int = 1000, key: str = "refresh"):
    """
    Uses st.autorefresh if available. If not, quietly does nothing (older Streamlit).
    """
    # Newer streamlit: st.autorefresh
    if hasattr(st, "autorefresh"):
        try:
            st.autorefresh(interval=ms, key=key)
        except Exception:
            pass
    # Some versions expose it in st.experimental
    elif hasattr(st, "experimental_rerun"):
        # no safe timer loop available without blocking; do nothing
        pass


def screen_startup():
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    sidebar_config()

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        logo_bytes = load_logo_bytes(DEFAULT_LOGO_PATH)
        if logo_bytes:
            st.image(logo_bytes, use_column_width=True)

        st.markdown("<h2 style='text-align:center; margin-top: 0.25rem;'>Basecamp Console</h2>", unsafe_allow_html=True)

        active = st.session_state.get("active_session_id")
        if active:
            st.info(f"Active session: {active}")
            if st.button("Resume Investigation", use_container_width=True):
                st.session_state["screen"] = "dashboard"
                st.rerun()

        with st.expander("Start New Investigation", expanded=True):
            location = st.text_input("Location (optional)", value="")
            notes = st.text_area("Session notes (optional)", value="", height=80)

            if st.button("Start Investigation", type="primary", use_container_width=True):
                session_id = create_session(location=location, notes=notes)
                st.session_state["active_session_id"] = session_id
                st.session_state["screen"] = "dashboard"
                st.rerun()

        st.caption("Tip: Put this window on Monitor 2. Cameras (mirror/web) can live on Monitor 1.")


def ingest_paths(
    session_id: str,
    file_paths: List[Path],
    ev_type_choice: str,
    captured_by_val: Optional[str],
    device: Optional[str],
    room: Optional[str],
    desc: Optional[str],
    linked_event_id: Optional[int],
) -> int:
    session_folder = get_session_folder(session_id)
    evidence_folder = session_folder / "evidence"

    ingested = 0
    for p in file_paths:
        if not p.exists() or not p.is_file():
            continue

        original_name = p.name
        ext = "".join(p.suffixes) or ""

        ev_type = ev_type_choice
        if ev_type_choice == "AUTO":
            ev_type = detect_type_from_name(original_name)

        code = evidence_code_for(session_id, ev_type)
        stored_name = safe_filename(f"{code}{ext}")
        stored_path = evidence_folder / stored_name

        # Copy file
        try:
            stored_path.write_bytes(p.read_bytes())
        except Exception:
            # If read_bytes fails for large files, stream copy
            try:
                import shutil
                shutil.copy2(str(p), str(stored_path))
            except Exception:
                continue

        execute(
            """
            INSERT INTO evidence(session_id, created_at, evidence_code, original_name, stored_name, stored_path, type,
                                 captured_by, device, room, description, linked_event_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                session_id,
                fmt_ts(now_local()),
                code,
                original_name,
                stored_name,
                str(stored_path),
                ev_type,
                captured_by_val,
                (device or "").strip() or None,
                (room or "").strip() or None,
                (desc or "").strip() or None,
                linked_event_id,
            ),
        )
        ingested += 1

    return ingested


def screen_dashboard():
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    sidebar_config()

    session_id = require_active_session()
    if not session_id:
        st.session_state["screen"] = "startup"
        st.rerun()

    session = fetchone("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
    if not session:
        st.session_state.pop("active_session_id", None)
        st.session_state["screen"] = "startup"
        st.rerun()

    # live session timer (best-effort)
    maybe_autorefresh(ms=1000, key="timer_refresh")

    header_left, header_mid, header_right = st.columns([1, 3, 1])

    with header_left:
        logo_bytes = load_logo_bytes(DEFAULT_LOGO_PATH)
        if logo_bytes:
            st.image(logo_bytes, use_column_width=True)

    with header_mid:
        elapsed = session_elapsed_str(session["started_at"])
        st.markdown("## Session Dashboard")
        st.caption(
            f"Session ID: {session_id} • Started: {session['started_at']} • "
            f"Elapsed: {elapsed} • Location: {session['location'] or '—'}"
        )

    with header_right:
        if st.button("Wrap Session", type="primary", use_container_width=True):
            st.session_state["screen"] = "wrap"
            st.rerun()
        if st.button("End Session (no report)", use_container_width=True):
            end_session(session_id)
            st.session_state.pop("active_session_id", None)
            st.session_state["screen"] = "startup"
            st.rerun()

    st.divider()

    tabs = st.tabs(["Logs & Events", "Evidence Intake", "Equipment", "Investigator Tracker"])

    # -------------------------
    # Logs & Events
    # -------------------------
    with tabs[0]:
        left, right = st.columns([1.2, 1])

        with left:
            st.subheader("Create Event Marker")
            authors = st.session_state.get("authors", DEFAULT_AUTHORS)
            tags = st.session_state.get("tags", DEFAULT_TAGS)

            ev_author = st.selectbox("Author", options=authors, key="ev_author")
            ev_title = st.text_input("Event title (short)", placeholder="Shadow movement / loud knock / voice captured…")
            ev_room = st.text_input("Room (optional)", placeholder="Basement / Hallway / Kitchen…")
            ev_cam = st.text_input("Camera label (optional)", placeholder="Cam 2 / Hallway Cam / DVR 3…")
            ev_sev = st.slider("Severity", 1, 5, 2)
            ev_tags = st.multiselect("Tags (optional)", options=tags, default=[])

            if st.button("Mark Event", type="primary", use_container_width=True):
                if not ev_title.strip():
                    st.warning("Event title is required.")
                else:
                    execute(
                        "INSERT INTO events(session_id, created_at, author, title, severity, tags, room, camera_label) VALUES (?,?,?,?,?,?,?,?)",
                        (session_id, fmt_ts(now_local()), ev_author, ev_title.strip(), ev_sev, json.dumps(ev_tags),
                         ev_room.strip() or None, ev_cam.strip() or None),
                    )
                    st.rerun()

            st.divider()

            st.subheader("Notes")
            mode = st.radio("Mode", ["Quick Log", "Narrative Notes"], horizontal=True)
            note_author = st.selectbox("Author", options=authors, key="note_author")
            note_tags = st.multiselect("Tags (optional)", options=tags, default=[], key="note_tags")

            if mode == "Quick Log":
                note_text = st.text_input("One-liner", placeholder="21:43 — hallway cam 2: shadow movement")
            else:
                note_text = st.text_area("Narrative note", height=140, placeholder="Context, observations, team decisions…")

            recent_events = fetchall(
                "SELECT id, created_at, title FROM events WHERE session_id = ? ORDER BY created_at DESC LIMIT 25",
                (session_id,)
            )
            event_options = [("—", None)] + [(f"#{r['id']} • {r['created_at']} • {r['title']}", r["id"]) for r in recent_events]
            sel_label = st.selectbox("Link to an event (optional)", options=[o[0] for o in event_options])
            linked_event_id = next((eid for label, eid in event_options if label == sel_label), None)

            if st.button("Save Note", use_container_width=True):
                if not note_text or not note_text.strip():
                    st.warning("Note text is required.")
                else:
                    execute(
                        "INSERT INTO logs(session_id, created_at, mode, author, tags, text, linked_event_id) VALUES (?,?,?,?,?,?,?)",
                        (session_id, fmt_ts(now_local()),
                         "QUICK" if mode == "Quick Log" else "NARRATIVE",
                         note_author, json.dumps(note_tags), note_text.strip(), linked_event_id),
                    )
                    st.rerun()

        with right:
            st.subheader("Timeline (Newest first)")
            merged = []
            events = fetchall("SELECT * FROM events WHERE session_id = ? ORDER BY created_at DESC LIMIT 100", (session_id,))
            logs = fetchall("SELECT * FROM logs WHERE session_id = ? ORDER BY created_at DESC LIMIT 200", (session_id,))

            for e in events:
                merged.append(("EVENT", e["created_at"], e))
            for l in logs:
                merged.append((l["mode"], l["created_at"], l))

            merged.sort(key=lambda t: t[1], reverse=True)

            for kind, ts, row in merged[:150]:
                if kind == "EVENT":
                    tag_str = ", ".join(json.loads(row["tags"]) if row["tags"] else [])
                    meta = " • ".join([p for p in [row["room"], row["camera_label"], f"sev {row['severity']}",
                                                  (f"tags: {tag_str}" if tag_str else None)] if p])
                    st.markdown(f"**{fmt_time(datetime.strptime(ts, '%Y-%m-%d %H:%M:%S'))} — EVENT #{row['id']}**  \n{row['title']}  \n_{meta}_")
                    st.divider()
                else:
                    tag_str = ", ".join(json.loads(row["tags"]) if row["tags"] else [])
                    mode_label = "Quick" if kind == "QUICK" else "Narrative"
                    st.markdown(
                        f"**{fmt_time(datetime.strptime(ts, '%Y-%m-%d %H:%M:%S'))} — {mode_label}** ({row['author']})  \n{row['text']}"
                        + (f"  \n_tags: {tag_str}_" if tag_str else "")
                    )
                    st.divider()

    # -------------------------
    # Evidence Intake (adds Folder Import + Watch)
    # -------------------------
    with tabs[1]:
        st.subheader("Evidence Intake")

        authors = st.session_state.get("authors", DEFAULT_AUTHORS)

        top = st.columns([1, 1, 1, 1])
        with top[0]:
            ev_type_choice = st.selectbox("Evidence Type", EVIDENCE_TYPES, help="AUTO detects type from file extension")
        with top[1]:
            captured_by = st.selectbox("Captured by", options=["—"] + authors)
            captured_by_val = None if captured_by == "—" else captured_by
        with top[2]:
            device = st.text_input("Device (optional)", placeholder="VR-01 / iPhone / IR-01 / Cam 2…")
        with top[3]:
            room = st.text_input("Room (optional)", placeholder="Basement / Hallway…")

        desc = st.text_area("Description / what was happening (optional)", height=90)

        recent_events = fetchall(
            "SELECT id, created_at, title FROM events WHERE session_id = ? ORDER BY created_at DESC LIMIT 50",
            (session_id,)
        )
        event_options = [("—", None)] + [(f"#{r['id']} • {r['created_at']} • {r['title']}", r["id"]) for r in recent_events]
        sel_label = st.selectbox("Link to an event marker (optional)", options=[o[0] for o in event_options], key="evidence_link_event")
        linked_event_id = next((eid for label, eid in event_options if label == sel_label), None)

        st.markdown("### A) Quick Upload (manual)")
        uploads = st.file_uploader("Evidence files", accept_multiple_files=True)
        if uploads and st.button("Ingest Uploaded Files", type="primary"):
            # write uploads to temp then ingest paths
            tmp_dir = DATA_DIR / "_tmp_uploads"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            paths = []
            for up in uploads:
                p = tmp_dir / safe_filename(up.name)
                p.write_bytes(up.getbuffer())
                paths.append(p)
            ing = ingest_paths(session_id, paths, ev_type_choice, captured_by_val, device, room, desc, linked_event_id)
            st.success(f"Ingested {ing} file(s).")
            # cleanup
            try:
                for p in paths:
                    p.unlink(missing_ok=True)
            except Exception:
                pass
            st.rerun()

        st.divider()

        st.markdown("### B) Import SD Card / Folder (recommended)")
        folder_cols = st.columns([2, 1, 1])
        with folder_cols[0]:
            folder_path = st.text_input("Folder path", placeholder=r"E:\DCIM  or  C:\Users\Admin\Desktop\CaseFiles\SDCardDump")
        with folder_cols[1]:
            recursive = st.checkbox("Include subfolders", value=True)
        with folder_cols[2]:
            ignore_ext = st.text_input("Ignore extensions (comma)", value=".db,.ini,.tmp")

        ignore_set = {e.strip().lower() for e in ignore_ext.split(",") if e.strip()}

        def scan_folder(path_str: str) -> List[Path]:
            p = Path(path_str)
            if not p.exists() or not p.is_dir():
                return []
            if recursive:
                all_files = [x for x in p.rglob("*") if x.is_file()]
            else:
                all_files = [x for x in p.glob("*") if x.is_file()]
            if ignore_set:
                all_files = [x for x in all_files if x.suffix.lower() not in ignore_set]
            return all_files

        if st.button("Scan Folder"):
            files = scan_folder(folder_path)
            st.session_state["import_scan"] = [str(x) for x in files]
            st.success(f"Found {len(files)} file(s).")

        files_scanned = [Path(x) for x in st.session_state.get("import_scan", [])]
        if files_scanned:
            st.caption(f"Ready to import: {len(files_scanned)} file(s).")
            if st.button("Import Scanned Files", type="primary"):
                ing = ingest_paths(session_id, files_scanned, ev_type_choice, captured_by_val, device, room, desc, linked_event_id)
                st.success(f"Ingested {ing} file(s).")
                st.session_state["import_scan"] = []
                st.rerun()

        st.divider()
        st.markdown("### C) Watch a Folder (auto-import new files)")
        watch_cols = st.columns([2, 1, 1])
        with watch_cols[0]:
            watch_path = st.text_input("Watch path", placeholder=r"E:\  (top of SD card)  or  C:\Temp\DropZone")
        with watch_cols[1]:
            watch_on = st.checkbox("Enable Watch", value=st.session_state.get("watch_on", False))
        with watch_cols[2]:
            watch_every = st.selectbox("Scan every", ["2s", "5s", "10s"], index=1)

        st.session_state["watch_on"] = watch_on

        interval_ms = {"2s": 2000, "5s": 5000, "10s": 10000}[watch_every]
        if watch_on:
            maybe_autorefresh(ms=interval_ms, key="watch_refresh")

            seen = set(st.session_state.get("watch_seen", []))
            new_files = []
            for f in scan_folder(watch_path):
                key = str(f.resolve())
                if key not in seen:
                    new_files.append(f)
                    seen.add(key)

            if new_files:
                ing = ingest_paths(session_id, new_files, ev_type_choice, captured_by_val, device, room, desc, linked_event_id)
                st.toast(f"Auto-ingested {ing} new file(s) from watch folder.")
            st.session_state["watch_seen"] = list(seen)

        st.divider()
        st.subheader("Evidence Library")
        ev_rows = fetchall("SELECT * FROM evidence WHERE session_id = ? ORDER BY created_at DESC", (session_id,))
        if not ev_rows:
            st.info("No evidence ingested yet.")
        else:
            for ev in ev_rows[:200]:
                st.markdown(f"**{ev['evidence_code']}** — {ev['type']} — `{ev['stored_name']}`")
                meta = []
                if ev["captured_by"]: meta.append(f"captured by: {ev['captured_by']}")
                if ev["device"]: meta.append(f"device: {ev['device']}")
                if ev["room"]: meta.append(f"room: {ev['room']}")
                if ev["linked_event_id"]: meta.append(f"linked event: #{ev['linked_event_id']}")
                if meta:
                    st.caption(" • ".join(meta))
                if ev["description"]:
                    st.write(ev["description"])
                try:
                    p = Path(ev["stored_path"])
                    if p.exists():
                        st.download_button("Download", data=p.read_bytes(), file_name=p.name,
                                           mime="application/octet-stream", key=f"dl_{ev['id']}")
                except Exception:
                    pass
                st.divider()

    # -------------------------
    # Equipment
    # -------------------------
    with tabs[2]:
        st.subheader("Equipment Checkout / Return")
        authors = st.session_state.get("authors", DEFAULT_AUTHORS)
        equipment = fetchall("SELECT * FROM equipment ORDER BY gear_id ASC")
        gear_choices = [f"{e['gear_id']} — {e['name']}" for e in equipment]
        gear_map = {f"{e['gear_id']} — {e['name']}": e["gear_id"] for e in equipment}

        left, right = st.columns([1, 1])
        with left:
            st.markdown("### Checkout (OUT)")
            who = st.selectbox("Who", options=authors, key="eq_out_who")
            gear_label = st.selectbox("Gear", options=gear_choices, key="eq_out_gear")
            battery = st.number_input("Battery % (optional)", min_value=0, max_value=100, value=100, step=1)
            battery_use = st.checkbox("Record battery %", value=False)

            if st.button("Checkout", type="primary", use_container_width=True):
                execute(
                    "INSERT INTO equipment_log(session_id, gear_id, action, at, who, battery, condition_notes) VALUES (?,?,?,?,?,?,?)",
                    (session_id, gear_map[gear_label], "OUT", fmt_ts(now_local()), who, int(battery) if battery_use else None, None),
                )
                st.rerun()

        with right:
            st.markdown("### Return (IN)")
            who_in = st.selectbox("Who", options=authors, key="eq_in_who")
            gear_label_in = st.selectbox("Gear", options=gear_choices, key="eq_in_gear")
            condition = st.text_area("Condition notes (optional)", height=80, placeholder="dead battery / weird behavior / damage…")

            if st.button("Return", type="primary", use_container_width=True):
                execute(
                    "INSERT INTO equipment_log(session_id, gear_id, action, at, who, battery, condition_notes) VALUES (?,?,?,?,?,?,?)",
                    (session_id, gear_map[gear_label_in], "IN", fmt_ts(now_local()), who_in, None, condition.strip() or None),
                )
                st.rerun()

        st.divider()
        st.markdown("### Equipment Activity (Newest first)")
        eq_rows = fetchall("SELECT * FROM equipment_log WHERE session_id = ? ORDER BY at DESC LIMIT 200", (session_id,))
        if not eq_rows:
            st.info("No equipment activity yet.")
        else:
            for r in eq_rows:
                extra = []
                if r["battery"] is not None:
                    extra.append(f"battery {r['battery']}%")
                if r["condition_notes"]:
                    extra.append(r["condition_notes"])
                st.markdown(f"**{r['at']} — {r['action']}** — {r['gear_id']} — {r['who']}" + (f"  \n_{' • '.join(extra)}_" if extra else ""))

    # -------------------------
    # Tracker
    # -------------------------
    with tabs[3]:
        st.subheader("Investigator Tracker")
        left, right = st.columns([1, 1])

        with left:
            st.markdown("### Update / Create Team")
            team_label = st.text_input("Team Label", placeholder="Team A / Team B / Solo…")
            loc = st.text_input("Current Location", placeholder="Basement / Hallway / Upstairs…")
            last_radio = st.text_input("Last radio call (optional)", placeholder="21:43 — all clear")
            needs_support = st.checkbox("Needs support")

            if st.button("Save Team Status", type="primary", use_container_width=True):
                if not team_label.strip() or not loc.strip():
                    st.warning("Team label and location are required.")
                else:
                    existing = fetchone(
                        "SELECT id FROM tracker WHERE session_id = ? AND team_label = ?",
                        (session_id, team_label.strip()),
                    )
                    if existing:
                        execute(
                            "UPDATE tracker SET location=?, last_radio_call=?, needs_support=? WHERE id=?",
                            (loc.strip(), last_radio.strip() or None, 1 if needs_support else 0, existing["id"]),
                        )
                    else:
                        execute(
                            "INSERT INTO tracker(session_id, team_label, location, last_radio_call, needs_support) VALUES (?,?,?,?,?)",
                            (session_id, team_label.strip(), loc.strip(), last_radio.strip() or None, 1 if needs_support else 0),
                        )
                    st.rerun()

        with right:
            st.markdown("### Current Teams")
            rows = fetchall("SELECT * FROM tracker WHERE session_id = ? ORDER BY team_label ASC", (session_id,))
            if not rows:
                st.info("No teams yet.")
            else:
                for r in rows:
                    flag = "🟥 NEEDS SUPPORT" if r["needs_support"] else "🟩 OK"
                    st.markdown(f"**{r['team_label']}** — {r['location']}  \n{flag}  \n_last radio: {r['last_radio_call'] or '—'}_")
                    if st.button(f"Remove {r['team_label']}", key=f"rm_team_{r['id']}"):
                        execute("DELETE FROM tracker WHERE id = ?", (r["id"],))
                        st.rerun()
                    st.divider()


def screen_wrap():
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    sidebar_config()

    session_id = require_active_session()
    if not session_id:
        st.session_state["screen"] = "startup"
        st.rerun()

    st.markdown("## Wrap Session & Generate Report")
    st.caption("Generates a draft PDF + optional clean shutdown.")

    cols = st.columns([1, 1, 1])
    with cols[0]:
        if st.button("Back to Dashboard", use_container_width=True):
            st.session_state["screen"] = "dashboard"
            st.rerun()

    with cols[1]:
        if st.button("End Session Now", type="primary", use_container_width=True):
            end_session(session_id)
            st.success("Session ended. Now generate the report.")

    with cols[2]:
        if st.button("Generate PDF Report", type="primary", use_container_width=True):
            try:
                pdf_path = generate_pdf_report(session_id, logo_path=DEFAULT_LOGO_PATH)
                st.session_state["last_report_path"] = str(pdf_path)
                st.success("Report generated.")
            except Exception as e:
                st.error(f"Failed to generate report: {e}")

    st.divider()

    last_report = st.session_state.get("last_report_path")
    if last_report and Path(last_report).exists():
        p = Path(last_report)
        st.download_button("Download Report PDF", data=p.read_bytes(), file_name=p.name, mime="application/pdf")

    st.divider()
    st.markdown("### Close Out")
    shut_cols = st.columns([1, 1])
    with shut_cols[0]:
        if st.button("Return to Start Screen", use_container_width=True):
            st.session_state.pop("active_session_id", None)
            st.session_state["screen"] = "startup"
            st.rerun()

    with shut_cols[1]:
        if st.button("Shutdown Basecamp Console", type="primary", use_container_width=True):
            # signal launcher to terminate streamlit server
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            SHUTDOWN_FLAG.write_text("shutdown", encoding="utf-8")
            st.warning("Shutting down… you can close this browser tab.")
            st.stop()


def main():
    init_db()

    if "screen" not in st.session_state:
        st.session_state["screen"] = "startup"

    screen = st.session_state["screen"]
    if screen == "startup":
        screen_startup()
    elif screen == "dashboard":
        screen_dashboard()
    elif screen == "wrap":
        screen_wrap()
    else:
        st.session_state["screen"] = "startup"
        st.rerun()

if __name__ == "__main__":
    main()
