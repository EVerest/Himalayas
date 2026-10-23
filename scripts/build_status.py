#!/usr/bin/env python3
"""Build the status site under status/ from results/, pointers.json and the schemas.

Reads submissions in either format (CTRF or OpenHTF) and attributes each one to a
pointer when its exact commit appears in that pointer's log in pointers.json. The
log also gives the tip, so freshness and "moves behind" need no network.

Output, per pointer (the default pointer without a suffix, others suffixed):
  index.html      overview: setups by integrator, one conformance cell per standard
  detailed.html   regressions, setups table with recent runs, pointer comparison
  coverage.html   capability map of what reported tests show working
  setup/*.html    one page per setup: header, capability map, history, tests, runs
plus summary.json and regressions.json at the root.

Honesty rules the rendering follows, each asserted against the rendered pages by the
`build` suite in scripts/selftest.py:

- Verdict and freshness are orthogonal and get separate slots. A stale result that
  is also failing shows both.
- Freshness is never absent: every overview row carries current, stale or unknown.
- A capability is only claimed as passing when a test carrying its tag passed and
  none failed. A tag whose tests were all skipped reads "none judged".
- Accepted, inconclusive and skipped tests are listed beside the counts, never
  inside them.
- Every committed submission is linked from some page, including results whose
  commit is in no pointer's log.

Staleness: a result is stale once the pointer has moved off the tested commit AND
the result is older than --grace-days.

Usage: build_status.py [--grace-days N] [--out-dir DIR]
"""
import argparse
import collections
import datetime as dt
import html
import json
import os
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
NOW = dt.datetime.now(dt.timezone.utc)
GRACE_DAYS = 14
DEFAULT_POINTER = "testing/main"

SPEC_TAGS = {"x-iso15118-4": "ISO15118-2", "x-iso15118-5": "ISO15118-3", "x-iso15118-21": "ISO15118-20"}
UNMAPPED = {"DIN70121", "IEC61851"}
STANDARDS = [
    ("ISO15118-2", "ISO 15118-2", "via -4"),
    ("ISO15118-3", "ISO 15118-3", "via -5"),
    ("ISO15118-20", "ISO 15118-20", "via -21"),
    ("DIN70121", "DIN 70121", "†"),
    ("IEC61851", "IEC 61851", "†"),
    ("OCPP1.6", "OCPP 1.6", "TestCases"),
    ("OCPP2.0.1", "OCPP 2.0.1", "Part 6"),
    ("OCPP2.1", "OCPP 2.1", "Part 6"),
    ("other", "Other tests", ""),
]
STD_LABEL = {k: n for k, n, _ in STANDARDS}
VERDICTS = ["build-failed", "failing", "passing-partial", "passing", "not-exercised", "build-only", "not-reported"]
DISPLAY_ORDER = ["passing", "passing-partial", "failing", "build-failed", "not-exercised", "build-only", "not-reported"]
VERDICT_LABEL = {
    "build-failed": "✕ build failed", "failing": "✕ failing", "passing-partial": "◐ passing, partial",
    "passing": "✓ passing", "not-exercised": "○ not exercised", "build-only": "□ build only",
    "not-reported": "– not reported",
}

esc = html.escape


def short(sha):
    return sha[:7] if sha else "?"


EVEREST_COMMIT = "https://github.com/EVerest/EVerest/commit/"
# EVerest's own logo, copied from EVerest/EVerest docs/images.
LOGO_SRC = "docs/images/everest_horizontal-color.svg"
LOGO = "everest-logo.svg"
# The header is navy, so the logo's two navy fills turn white, as in the
# project's reversed artwork.
LOGO_REVERSE = {".st4{fill:#0C2351;}": ".st4{fill:#FFFFFF;}", ".st9{fill:#1B3964;}": ".st9{fill:#FFFFFF;}"}


def reversed_logo(svg):
    for old, new in LOGO_REVERSE.items():
        if old not in svg:
            raise ValueError(f"{LOGO_SRC}: fill {old} not found")
        svg = svg.replace(old, new)
    return svg


# Integrators' own logos, one SVG per public id, shown beside their name.
INTEG_LOGO_DIR = "integrators/logos"
INTEG_LOGO_MAX = 32 * 1024
# A published logo is also a document anyone can open on the site's origin, so
# it is held to an allowlist of static SVG and re-serialized from the parsed
# tree: what is published is exactly what was checked.
SVG_NS = "http://www.w3.org/2000/svg"
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
LOGO_ELEMENTS = {"svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "defs",
                 "linearGradient", "radialGradient", "stop", "clipPath", "mask", "use", "style", "title", "desc"}
LOGO_ATTRS = {
    "id", "class", "style", "version", "viewBox", "width", "height", "preserveAspectRatio", "transform",
    "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "fx", "fy", "d", "points", "offset",
    "fill", "fill-rule", "fill-opacity", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin",
    "stroke-miterlimit", "stroke-dasharray", "stroke-opacity", "opacity", "clip-path", "clip-rule", "mask",
    "stop-color", "stop-opacity", "gradientUnits", "gradientTransform", "spreadMethod", "clipPathUnits",
    "maskUnits", "maskContentUnits", "href", "data-name", "type",
}
# CSS that could fetch or run anything: imports, non-fragment url(), escapes
# that could spell either.
UNSAFE_CSS = re.compile(r"@import|url\(\s*['\"]?(?!#)|expression|javascript:|\\", re.I)


def clean_logo(data):
    """(svg, problems): the logo re-serialized, or None and why it cannot be shown."""
    if len(data) > INTEG_LOGO_MAX:
        return None, [f"larger than {INTEG_LOGO_MAX // 1024} KiB"]
    if re.search(rb"<!(?:DOCTYPE|ENTITY)", data, re.I):
        return None, ["contains a DTD"]
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        return None, [f"is not well-formed XML ({e})"]
    if root.tag != f"{{{SVG_NS}}}svg":
        return None, ["is not an SVG document"]
    problems = []
    for el in root.iter():
        ns, _, name = el.tag.rpartition("}")
        if ns[1:] != SVG_NS or name not in LOGO_ELEMENTS:
            problems.append(f"contains <{name}>, which is not static SVG")
            continue
        if name == "style" and UNSAFE_CSS.search(el.text or ""):
            problems.append("has a <style> that imports or links")
        for k, v in el.attrib.items():
            if k == XLINK_HREF:
                k = "href"
            if k not in LOGO_ATTRS:
                problems.append(f"has the attribute {k} on <{name}>")
            elif k == "href" and (name != "use" or not v.startswith("#")):
                problems.append(f"links outside the file from <{name}>")
            elif UNSAFE_CSS.search(v):
                problems.append(f"has a {k} on <{name}> that imports or links")
    if problems:
        return None, sorted(set(problems))
    return ET.tostring(root, encoding="unicode") + "\n", []


def logo_audit(root, allow):
    """(file, problem) for every file under integrators/logos/ that will not be shown."""
    d = root / INTEG_LOGO_DIR
    public = {a["id"] for a in allow if a["name"] != a["id"]}
    ids = {a["id"] for a in allow}
    out = []
    for f in sorted(d.iterdir()) if d.is_dir() else []:
        rel = f"{INTEG_LOGO_DIR}/{f.name}"
        if f.suffix != ".svg":
            out.append((rel, "is not an .svg file"))
        elif f.stem not in ids:
            out.append((rel, "names no allowlisted id"))
        elif f.stem not in public:
            out.append((rel, "belongs to an anonymous id, and a logo would name it"))
        else:
            out += [(rel, p) for p in clean_logo(f.read_bytes())[1]]
    return out


def integ_logo(P, integ, up=""):
    return (f'<img class="ilogo" src="{up}logos/{esc(integ)}.svg" alt="" loading="lazy">'
            if integ in P["logos"] else "")


def commit_link(sha):
    """A short sha, linked to that commit in EVerest."""
    return f'<a href="{EVEREST_COMMIT}{esc(sha)}"><code>{short(sha)}</code></a>' if sha else "<code>?</code>"


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def ms_to_dt(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)


# ---------------------------------------------------------------- loading

def load_pointers(root=ROOT):
    """Declared pointers and each pointer's log of tips, oldest first."""
    try:
        p = json.loads((root / "pointers.json").read_text())
    except (OSError, ValueError):
        return {}, {}
    declared = p.get("pointers") or {}
    logs = collections.OrderedDict((name, []) for name in declared)
    for e in p.get("log") or []:
        logs.setdefault(e["pointer"], []).append({"sha": e["sha"], "at": e.get("moved_at", "")})
    return declared, logs


def load_allowlist(root=ROOT):
    """Allowlisted integrators, in file order, with their display name when public."""
    try:
        text = (root / "integrators" / "allowlist.yaml").read_text()
    except OSError:
        return []
    out, cur = [], None
    for line in text.splitlines():
        m = re.match(r"\s*-\s*id:\s*(\S+)", line)
        if m:
            cur = {"id": m.group(1), "name": m.group(1)}
            out.append(cur)
            continue
        m = re.match(r"\s*display_name:\s*(.+)", line)
        if m and cur:
            cur["name"] = m.group(1).strip()
    bad = {rel for rel, _ in logo_audit(root, out)}
    for a in out:
        f = root / INTEG_LOGO_DIR / f"{a['id']}.svg"
        if f.is_file() and f"{INTEG_LOGO_DIR}/{f.name}" not in bad:
            a["logo"] = clean_logo(f.read_bytes())[0]
    return out


PHASE_STATUS = {"PASS": "passed", "FAIL": "failed", "ERROR": "failed", "TIMEOUT": "failed",
                "ABORTED": "failed", "SKIP": "skipped"}


def outcome(t):
    ex = t.get("extra") or {}
    if ex.get("acceptedDeviation") or "x-accepted-deviation" in t.get("tags", []):
        return "accepted"
    if t["status"] == "other" or ex.get("inconclusive") or t.get("rawStatus") == "INCONCLUSIVE":
        return "skipped" if "x-rig-limitation" in t.get("tags", []) else "inconclusive"
    if t["status"] == "failed":
        return "failed"
    return "passed" if t["status"] == "passed" else "skipped"


VOCAB_TAGS = set()


def untagged(t):
    return not any(x in VOCAB_TAGS or x in SPEC_TAGS for x in t.get("tags", []))


def standard_of(t):
    tags = t.get("tags", [])
    for tag, std in SPEC_TAGS.items():
        if tag in tags:
            return std
    for std in UNMAPPED:
        if std in tags:
            return std
    for tag in tags:
        if tag.startswith("OCPP"):
            return tag.split("-")[0]
    return "other"


def ctrf_tests(r):
    tests = []
    for t in r.get("tests", []):
        ex = t.get("extra") or {}
        tests.append({
            "name": t["name"], "outcome": outcome(t), "std": standard_of(t),
            "tags": t.get("tags", []), "message": t.get("message", ""),
            "file": t.get("filePath", ""), "untagged": untagged(t),
            "key": ex.get("failureKey"), "clause": (ex.get("acceptedDeviation") or {}).get("clause"),
            "rig": "x-rig-limitation" in t.get("tags", []), "raw": t.get("rawStatus", ""),
            "measurements": [],
        })
    return tests


def htf_tests(doc):
    """OpenHTF phases as tests. A measurement outside its limits fails the test."""
    tests = []
    for ph in doc.get("phases") or []:
        tags = ph.get("tags") or []
        meas = [{"name": mv.get("name") or k, "value": mv.get("measured_value"), "units": mv.get("units") or "",
                 "outcome": mv.get("outcome") or "", "limits": ", ".join(str(x) for x in mv.get("validators") or [])}
                for k, mv in (ph.get("measurements") or {}).items()]
        status = PHASE_STATUS.get(ph.get("outcome"), "skipped")
        if any(m["outcome"] not in ("PASS", "") for m in meas):
            status = "failed"
        t = {"name": ph.get("name", "?"), "status": status, "tags": tags}
        details = ph.get("outcome_details") or []
        msg = "\n".join(d.get("description", "") if isinstance(d, dict) else str(d) for d in details)
        tests.append({
            "name": t["name"], "outcome": outcome(t), "std": standard_of(t), "tags": tags,
            "message": msg, "file": "", "untagged": untagged(t), "key": None, "clause": None,
            "rig": "x-rig-limitation" in tags, "raw": ph.get("outcome") or "", "measurements": meas,
        })
    return tests


def load_records(root=ROOT):
    """Every stored submission, CTRF or OpenHTF, in one shape. Unreadable files are skipped."""
    recs = []
    for f in sorted((root / "results").glob("*/*/*/*.json")):
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        rel = f.relative_to(root).as_posix()
        if isinstance(d.get("results"), dict):
            r = d["results"]
            env, x = r.get("environment", {}), r.get("extra", {})
            tests = ctrf_tests(r)
            base = {
                "fmt": "ctrf", "integ": x.get("integrator", {}).get("id", f.parts[-4]),
                "display": x.get("integrator", {}).get("displayName"),
                "config": x.get("configuration") or f.parts[-4], "commit": env.get("commit", ""),
                "start": r.get("summary", {}).get("start", 0), "env": env.get("testEnvironment", "unknown"),
                "os": env.get("osPlatform", ""), "build": x.get("build", {}), "tool": r.get("tool", {}).get("name", ""),
                "test_system": x.get("testSystem"), "report": x.get("reportUrl"),
                "pointer": env.get("appVersion") or "/".join(f.parts[-3:-1]),
            }
        elif isinstance(d.get("phases"), list):
            md = d.get("metadata") or {}
            ev = md.get("everest") or {}
            tests = htf_tests(d)
            base = {
                "fmt": "openhtf", "integ": (md.get("integrator") or {}).get("id", f.parts[-4]),
                "display": (md.get("integrator") or {}).get("displayName"),
                "config": ev.get("configuration") or f.parts[-4], "commit": ev.get("commit", ""),
                "start": d.get("start_time_millis") or d.get("end_time_millis") or 0,
                "env": ev.get("testEnvironment", "unknown"), "os": "", "build": md.get("build") or {},
                "tool": d.get("station_id") or "", "test_system": None, "report": ev.get("reportUrl"),
                "pointer": ev.get("pointer") or "/".join(f.parts[-3:-1]),
            }
        else:
            continue
        names = collections.Counter(t["name"] for t in tests)
        b = base.pop("build")
        recs.append({**base, "path": rel, "build": b.get("status", "passed"), "modules": b.get("modules", []),
                     "targets": [t.get("target", t) if isinstance(t, dict) else t for t in b.get("targets", [])],
                     "tests": tests, "dupes": [n for n, c in names.items() if c > 1]})
    return recs


def verdict(rec):
    if rec["build"] != "passed":
        return "build-failed"
    oc = collections.Counter(t["outcome"] for t in rec["tests"])
    if not rec["tests"]:
        return "build-only"
    if oc["failed"]:
        return "failing"
    if oc["passed"] == 0:
        return "not-exercised"
    if oc["skipped"] or oc["accepted"] or oc["inconclusive"]:
        return "passing-partial"
    return "passing"


def pass_rate(passed, failed):
    """Passed over judged tests (passed plus failed), or None when none were judged."""
    return passed / (passed + failed) if passed + failed else None


def pct(rate, failed):
    """One decimal, trailing .0 dropped. Never reads 100% while anything failed."""
    v = round(rate * 100, 1)
    if failed and v >= 100:
        v = 99.9
    return f"{v:.1f}".removesuffix(".0") + "%"


def pct_html(rate, failed):
    """The rate, colored along the failing-to-passing pill colors by its value."""
    t = pct(rate, failed)
    return f'<span class="pr" style="--p:{t}">{t}</span>'


def pooled(setups):
    """(passed, failed, lowest setup) over the setups that have a rate, or None."""
    rated = [m for m in setups if m["rate"] is not None]
    if not rated:
        return None
    return (sum(m["passed_n"] for m in rated), sum(m["failed_n"] for m in rated),
            min(rated, key=lambda m: (m["rate"], m["config"])))


def pooled_txt(setups):
    """"97.5% passed · lowest 75%", naming the lowest setup on hover; "" with nothing rated."""
    pl = pooled(setups)
    if not pl:
        return ""
    passed, failed, low = pl
    head = f"{pct_html(pass_rate(passed, failed), failed)} passed"
    if len([m for m in setups if m["rate"] is not None]) < 2 or low["rate"] == pass_rate(passed, failed):
        return head
    return (f'{head} · <span title="{esc(low["integ_name"])} / {esc(low["config"])}">'
            f'lowest {pct_html(low["rate"], low["failed_n"])}</span>')


def std_counts(rec):
    out = collections.OrderedDict()
    for t in rec["tests"]:
        c = out.setdefault(t["std"], collections.Counter())
        c[t["outcome"]] += 1
        c["total"] += 1
    return out


def is_stale(row, tips, grace_days, now_ms=None):
    """Stale iff the pointer moved off this commit AND the result is past the grace.

    None when it cannot be known: no tip for the pointer, no commit, or no timestamp.
    """
    tip = tips.get(row["pointer"])
    if not tip or not row["commit"]:
        return None
    if tip == row["commit"]:
        return False
    if not row["ranAt"]:
        return None
    now = now_ms if now_ms is not None else NOW.timestamp() * 1000
    return (now - row["ranAt"]) / 86_400_000 > grace_days


# ---------------------------------------------------------------- per-pointer model

def norm_msg(m, key=None):
    if key:
        return key
    first = (m or "(no message)").strip().splitlines()[0]
    first = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "<uuid>", first)
    first = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?Z?", "<time>", first)
    first = re.sub(r"0x[0-9a-f]+", "0x…", first)
    first = re.sub(r"\d+\.\d+", "<n>", first)
    return first[:220]


def build_pointer(name, log, recs, allow, grace_days=GRACE_DAYS):
    shas = [e["sha"] for e in log]
    index = {}
    for i, s in enumerate(shas):
        index[s] = i
    tip = shas[-1] if shas else None
    mine = [r for r in recs if r["commit"] in index]
    setups = collections.OrderedDict()
    for r in sorted(mine, key=lambda r: r["start"]):
        setups.setdefault((r["integ"], r["config"]), []).append(r)

    model = []
    for (integ, cfg), runs in setups.items():
        latest = runs[-1]
        by_move = collections.OrderedDict((i, []) for i in range(len(shas)))
        for r in runs:
            by_move[index[r["commit"]]].append(r)
        first_tested = min(i for i, rs in by_move.items() if rs)
        tested = [(i, rs) for i, rs in by_move.items() if rs]
        behind = len(shas) - 1 - index[latest["commit"]]
        stale = is_stale({"pointer": name, "commit": latest["commit"], "ranAt": latest["start"]},
                         {name: tip}, grace_days)
        h = phase_history(tested, shas, latest)
        oc = collections.Counter(t["outcome"] for t in latest["tests"])
        model.append({
            "integ": integ, "integ_name": latest["display"] or f"{integ} (anonymous)", "config": cfg,
            "latest": latest, "runs": runs, "by_move": by_move, "first_tested": first_tested,
            "verdict": verdict(latest), "behind": behind,
            "fresh": "unknown" if stale is None else "stale" if stale else "current",
            "counts": std_counts(latest), "regs": h["regs"],
            "passed_n": oc["passed"], "failed_n": oc["failed"], "rate": pass_rate(oc["passed"], oc["failed"]), "flaky": h["flaky"], "long": h["long"],
            "flaky_passing": h["flaky_passing"], "inconclusive": h["inconclusive"],
            "confirmed": [r for r in h["regs"] if r["confirmed"]],
            "slug": slug(f"{integ}-{cfg}"),
            "href": "setup/" + pointer_file(slug(f"{integ}-{cfg}"), name),
        })
    reported = {m["integ"] for m in model}
    silent = [a for a in allow if a["id"] not in reported]
    return {"name": name, "log": log, "shas": shas, "tip": tip, "setups": model, "silent": silent,
            "logos": {a["id"] for a in allow if a.get("logo")},
            "unattributed": [r for r in recs if r["pointer"] == name and r["commit"] not in index]}


def phase_history(tested, shas, latest):
    """Classify every test of one setup from its run-level history.

    Only passed and failed runs form the sequence: inconclusive and skipped runs
    neither break nor extend a streak.
    """
    runs = collections.defaultdict(list)  # name -> [(move, outcome)] in run order
    for i, rs in tested:
        for r in rs:
            for t in r["tests"]:
                runs[t["name"]].append((i, t["outcome"]))
    out = {"regs": [], "flaky": [], "long": [], "flaky_passing": [], "inconclusive": []}
    for t in latest["tests"]:
        n, hist = t["name"], runs[t["name"]]
        if t["outcome"] == "inconclusive":
            k = len(hist)
            while k and hist[k - 1][1] == "inconclusive":
                k -= 1
            out["inconclusive"].append({"name": n, "std": t["std"], "message": t["message"], "since": shas[hist[k][0]]})
            continue
        seq = [(i, o) for i, o in hist if o in ("passed", "failed")]
        window = seq[-10:]
        flips = sum(1 for a, b in zip(window, window[1:]) if a[1] != b[1])
        mixed_moves = {i for i, _ in window if len({o for j, o in window if j == i}) > 1}
        if t["outcome"] == "passed":
            if flips >= 2 or mixed_moves:
                out["flaky_passing"].append({"name": n, "std": t["std"], "flips": flips})
            continue
        if t["outcome"] != "failed":
            continue
        k = len(seq)
        while k and seq[k - 1][1] == "failed":
            k -= 1
        streak = seq[k:]
        moves = sorted({i for i, _ in streak})
        mixed = moves[0] in {i for i, o in seq[:k]}
        before = seq[:k][-10:]
        prev_flaky = sum(1 for a, b in zip(before, before[1:]) if a[1] != b[1]) >= 2
        item = {"name": n, "std": t["std"], "message": t["message"], "key": t.get("key"),
                "since": shas[moves[0]], "runs_failed": len(streak), "previously_flaky": prev_flaky}
        steady = len(moves) >= 2 and not mixed
        if not steady and (mixed or flips >= 2):
            out["flaky"].append(item)
        elif k == 0:
            out["long"].append(item)
        else:
            good = seq[k - 1][0]
            item["range"] = (shas[good], shas[moves[0]])
            item["untested"] = shas[good + 1:moves[0]]
            item["confirmed"] = len(streak) >= 2
            out["regs"].append(item)
    return out


# ---------------------------------------------------------------- html helpers

CSS = """
:root{--ink:#0C2351;--fg:#1E2530;--muted:#556070;--soft:#3A4556;--bg:#F4F6FA;--card:#FFFFFF;--line:#DCE2EC;--line2:#EEF2F8;
--lime:#BAF419;--link:#1F5FA0;--ok:#0B5E41;--okbg:#DDF5EA;--bad:#8A1C12;--badbg:#FDE7E4;--warn:#7A4A00;--warnbg:#FFF1D6;--dash:#8792A4;
--mono:'DM Mono',ui-monospace,monospace;--sans:'DM Sans',system-ui,sans-serif;--disp:'DM Serif Display',Georgia,serif}
:root{--infobg:#E4ECF9;--info:#1F3F7A;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--ink:#CFE0FF;--fg:#E4E8EF;--muted:#9AA6B8;--soft:#B8C2D3;--bg:#0E1420;--card:#161E2D;--line:#2A3547;--line2:#1E2838;
--link:#7FB4F0;--ok:#7FE0B2;--okbg:#123A2A;--bad:#FF9C90;--badbg:#3C1814;--warn:#FFD48A;--warnbg:#3A2C10;--dash:#6B778A;--infobg:#1C2A48;--info:#A9C4F5;color-scheme:dark}}
:root[data-theme=dark]{--ink:#CFE0FF;--fg:#E4E8EF;--muted:#9AA6B8;--soft:#B8C2D3;--bg:#0E1420;--card:#161E2D;--line:#2A3547;--line2:#1E2838;
--link:#7FB4F0;--ok:#7FE0B2;--okbg:#123A2A;--bad:#FF9C90;--badbg:#3C1814;--warn:#FFD48A;--warnbg:#3A2C10;--dash:#6B778A;--infobg:#1C2A48;--info:#A9C4F5;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 var(--sans)}
a{color:var(--link)}code,.mono{font-family:var(--mono);font-size:.92em}
button,select,summary{font:inherit;cursor:pointer;color:inherit}
:focus-visible{outline:3px solid #2F96D0;outline-offset:2px}
header.top{background:#0C2351;color:#fff}header.top .in{max-width:1320px;margin:0 auto;padding:14px max(16px,env(safe-area-inset-left));display:flex;flex-wrap:wrap;gap:12px 24px;align-items:center}
header.top .lockup{display:flex;align-items:center;gap:14px;color:#fff;text-decoration:none;border-radius:8px}header.top .logo{display:flex;padding-right:14px;border-right:1px solid #3A5384}header.top .logo img{display:block;height:34px;width:auto}
svg a:hover text{fill:var(--link);text-decoration:underline}
header.top .brand{display:block;font-family:var(--disp);font-size:28px;line-height:1.1}header.top .sub{display:block;font-size:13px;color:#C9D4E8;max-width:34ch}
header.top nav{display:flex;flex-wrap:wrap;gap:4px;margin-left:auto}header.top nav a{color:#fff;text-decoration:none;padding:10px 12px;border-radius:8px}
header.top nav a[aria-current]{background:#21407A}.bar{height:4px;background:var(--lime)}
main{max-width:1320px;margin:0 auto;padding:20px max(16px,env(safe-area-inset-left)) 48px;display:flex;flex-direction:column;gap:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 20px;min-width:0}
h1{font-family:var(--disp);font-weight:400;color:var(--ink);margin:0;font-size:32px;text-wrap:balance}
h2{margin:0 0 10px;font-size:18px;color:var(--ink)}h3{margin:0 0 6px;font-size:15px;color:var(--ink)}
.muted{color:var(--muted)}.small{font-size:13px}.row{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center}
.pill{display:inline-block;padding:2px 9px;border-radius:12px;font-weight:700;font-size:13px;white-space:nowrap}
.v-passing{background:var(--okbg);color:var(--ok)}.v-failing,.v-build-failed{background:var(--badbg);color:var(--bad)}
.v-passing-partial{background:var(--warnbg);color:var(--warn)}.v-not-exercised,.v-build-only,.v-not-reported{border:1px dashed var(--dash);color:var(--soft);font-weight:500}
.reg{border:1px solid var(--bad);color:var(--bad);padding:1px 7px;border-radius:10px;font-size:12px;white-space:nowrap}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;vertical-align:top;padding:8px 12px;border-top:1px solid var(--line2)}thead th{background:var(--line2);color:var(--soft);font-size:12px;border-top:0;white-space:nowrap}
th[scope=row] a,td>a{white-space:nowrap}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.chips2{display:flex;flex-wrap:wrap;gap:4px;max-width:340px}
.schip{display:inline-flex;flex-direction:column;border-radius:6px;padding:2px 7px;font-size:12px;line-height:1.35;white-space:nowrap}
.schip b{font-weight:700}.schip .n{font-size:11px;opacity:.85}
.schip.ok{background:var(--okbg);color:var(--ok)}.schip.bad{background:var(--badbg);color:var(--bad)}.schip.none{border:1px dashed var(--dash);color:var(--soft)}
td.tested{white-space:nowrap}td.vcol{min-width:9em}td.vcol .reg{display:block;width:fit-content;margin-top:4px}
.pill.v-build-only.b-ok{background:var(--okbg);color:var(--ok);border:0;font-weight:700}.pill.v-build-only.b-bad{background:var(--badbg);color:var(--bad);border:0;font-weight:700}
.cell{font-size:13px;white-space:nowrap}.cell.ok{color:var(--ok)}.cell.bad{color:var(--bad);font-weight:700}.cell.warn{color:var(--warn)}.cell.none{color:var(--dash)}
.ilogo{height:20px;width:auto;max-width:96px;vertical-align:middle;margin-right:8px;padding:2px 4px;background:#fff;border-radius:4px}
.allint td.lcell{width:1%;padding-right:0}.lcell .ilogo{margin-right:0}
.rate{display:block;margin-top:4px;white-space:nowrap}.allint table{margin-top:10px}.allint .pills{display:flex;flex-wrap:wrap;gap:4px}tbody:target>tr>*{background:var(--warnbg)}tbody[id]{scroll-margin-top:96px}
.summarybar{display:flex;flex-wrap:wrap;gap:8px}.summarybar button{min-height:52px;min-width:120px;text-align:left;padding:6px 14px;border-radius:10px;border:1px solid var(--line);background:var(--card)}
.summarybar button b,.rtile b{font-size:20px;display:block;line-height:1.1}
.rtile{min-height:52px;padding:6px 14px;border-radius:10px;border:1px dashed var(--line);display:flex;flex-direction:column;justify-content:center}
.pr{font-weight:600;color:color-mix(in oklch,var(--ok) var(--p),var(--bad))}.summarybar button[aria-pressed=true]{outline:2px solid var(--ink)}
details>summary{min-height:32px}details.group{padding:0;overflow:hidden}details.group>summary{padding:12px 20px;background:var(--line2);list-style-position:inside}
.strip{display:flex;gap:2px;align-items:flex-end;height:18px}.strip span{width:12px;border-radius:2px;display:inline-block}
.s-passing{background:#1E8F64}.s-failing,.s-build-failed{background:#C0392B}.s-passing-partial{background:#D9922B}.s-not-exercised,.s-build-only{background:#8792A4}
.mx td{text-align:center;font-family:var(--mono);font-size:13px}.mx td.untested{color:var(--dash);border:1px dashed var(--dash)}.mx td.z{color:var(--ok)}.mx td.f{color:var(--bad);font-weight:700}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.cta{margin:0;padding:10px 14px;border-radius:10px;background:var(--infobg);color:var(--info);font-size:14px;max-width:80ch}.cta a{color:inherit;font-weight:700}
.md{max-width:100%}.md p,.md li{max-width:80ch}.md h2{margin-top:0}.md h3{margin:22px 0 8px;font-size:17px}.md h4{margin:18px 0 6px;font-size:15px;color:var(--ink)}
.md pre{background:var(--line2);border-radius:8px;padding:10px 12px;overflow-x:auto;font-size:13px}.md li{margin:3px 0}
.md blockquote{margin:10px 0;padding:2px 14px;border-left:4px solid var(--lime);background:var(--bg);border-radius:0 8px 8px 0}
.md hr{border:0;border-top:1px solid var(--line);margin:18px 0}.md td{white-space:normal;vertical-align:top}
.notice{border-left:0;background:var(--warnbg);color:var(--warn);border-radius:10px;padding:10px 14px;font-size:14px}
footer{max-width:1320px;margin:0 auto;padding:0 max(16px,env(safe-area-inset-left)) 32px;font-size:12px;color:var(--muted)}
[hidden]{display:none!important}
.modules{display:flex;flex-wrap:wrap;gap:6px;margin-top:4px}.modules code{background:var(--line2);padding:2px 8px;border-radius:6px}
details.grp{padding:0;overflow:hidden}details.grp>summary{padding:12px 20px}details.grp .items{padding:4px 20px 14px;display:flex;flex-direction:column}
details.tc{border-top:1px solid var(--line2);padding:4px 0}details.tc>summary{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline}
.dom{margin-bottom:14px}.dom h4{margin:0 0 6px;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.cap{border-radius:8px;padding:8px 11px;min-width:130px;border:1px solid transparent;text-align:left;font:inherit;display:flex;flex-direction:column}
.cap b{font-size:14px}.cap span{font-size:12px}button.cap{cursor:pointer}button.cap[aria-expanded=true],button.cap[aria-pressed=true]{outline:2px solid var(--ink);outline-offset:1px}
.l4{background:var(--okbg);color:var(--ok)}.l3{background:var(--warnbg);color:var(--warn)}.l2{background:var(--infobg);color:var(--info)}
.l1{background:var(--card);color:var(--soft);border:1px dashed var(--dash)}
.legend{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12px;color:var(--soft)}.legend i{display:inline-block;width:12px;height:12px;border-radius:3px;vertical-align:-2px;margin-right:4px}
.via{display:flex!important;flex-wrap:wrap;gap:4px;margin-top:5px}.via em{font-style:normal;font-size:11px;border-radius:5px;padding:0 6px;border:1px dashed var(--dash);color:var(--soft);background:var(--card)}
.via em.ok{border:0;background:var(--okbg);color:var(--ok)}.via em.bad{border:0;background:var(--warnbg);color:var(--warn)}
tr.meas-fail td{color:var(--bad);font-weight:700}
td.h4c{color:var(--ok)}td.h3c{color:var(--warn);font-weight:700}
.seg{display:flex;flex-wrap:wrap;gap:6px}.seg button{min-height:40px;padding:4px 14px;border-radius:8px;border:1px solid var(--line);background:var(--card)}.seg button[aria-pressed=true]{background:var(--ink);color:var(--card)}
details.integ{border:1px solid var(--line);border-radius:8px;margin-bottom:6px}details.integ>summary{padding:6px 12px;background:var(--line2);border-radius:8px}
details.integ[open]>summary{border-radius:8px 8px 0 0}
.theme{position:relative;display:grid;grid-template-columns:repeat(2,1fr);border:1px solid #3A5590;border-radius:20px;padding:2px;margin:0;font-size:13px}
.theme legend{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
.theme input{position:absolute;opacity:0;pointer-events:none}
.theme label{position:relative;z-index:1;padding:6px 12px;text-align:center;cursor:pointer;color:#C9D4E8;border-radius:18px;min-height:32px;display:flex;align-items:center;justify-content:center}
.theme input:checked+label{color:#0C2351;font-weight:700}.theme input:focus-visible+label{outline:3px solid #2F96D0}
.theme .thumb{position:absolute;top:2px;bottom:2px;left:2px;width:calc((100% - 4px)/2);border-radius:18px;background:var(--lime);transition:transform .2s ease}
.theme:has(#th-dark:checked) .thumb{transform:translateX(100%)}
@media (prefers-reduced-motion:reduce){.theme .thumb{transition:none}}
.tagfilter{display:flex;gap:10px;align-items:center}
.tcbody{padding:4px 0 8px 18px}.msg{white-space:pre-wrap;font-family:var(--mono);font-size:12px;background:var(--line2);padding:8px 10px;border-radius:6px;margin:4px 0;overflow-x:auto}
"""

JS = """
const th=document.getElementById('theme');
if(th){
  const root=document.documentElement;
  const sys=matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';
  document.getElementById('th-'+(root.dataset.theme||sys)).checked=true;
  th.addEventListener('change',e=>{
    const v=e.target.value;root.dataset.theme=v;
    try{localStorage.setItem('theme',v)}catch(e){}});
}
document.querySelectorAll('.allint a[href^="#integ-"]').forEach(a=>a.addEventListener('click',()=>{
  const d=document.getElementById(a.hash.slice(1));if(d){d.hidden=false;d.open=true}}));
document.querySelectorAll('select[data-nav]').forEach(s=>s.addEventListener('change',()=>{location.href=s.value}));
const bar=document.querySelector('.summarybar');
if(bar){
  const apply=f=>{
    bar.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.f===f)));
    document.querySelectorAll('tr[data-v]').forEach(tr=>{
      const st=tr.dataset.stale==='1';
      const show=!f||(f==='stale'?st:!st&&(tr.dataset.v===f||(f==='regressed'&&tr.dataset.reg==='1')));tr.hidden=!show});
    document.querySelectorAll('details.group').forEach(d=>{
      const vis=[...d.querySelectorAll('tr[data-v]')].some(t=>!t.hidden);d.hidden=!vis;if(f&&vis)d.open=true});
    try{history.replaceState(null,'',f?'#'+f:location.pathname)}catch(e){}
  };
  bar.querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{
    const cur=location.hash.slice(1);apply(cur===b.dataset.f?'':b.dataset.f)}));
  if(location.hash)apply(location.hash.slice(1));
}
const gb=document.getElementById('grpby'),od=document.getElementById('order'),box=document.getElementById('groups');
if(gb&&box){
  const all=[...box.querySelectorAll('details.tc')];
  const ST=['Failed','Inconclusive','Accepted and waived','Skipped','Passed'];
  const keyOf=(el,k)=>k==='status'?el.dataset.statusLabel:k==='std'?el.dataset.std:el.dataset.cluster;
  const regroup=()=>{
    const k=gb.value,o=od.value,open=new Set([...box.querySelectorAll('details.grp[open]')].map(d=>d.dataset.key));
    const g=new Map();
    all.forEach(el=>{const key=keyOf(el,k);if(!g.has(key))g.set(key,[]);g.get(key).push(el)});
    let keys=[...g.keys()];
    if(k==='status')keys.sort((a,b)=>ST.indexOf(a)-ST.indexOf(b));
    else keys.sort((a,b)=>Math.max(...g.get(b).map(e=>+e.dataset.since))-Math.max(...g.get(a).map(e=>+e.dataset.since))||g.get(b).length-g.get(a).length);
    box.replaceChildren(...keys.map(key=>{
      const items=g.get(key).sort((a,b)=>o==='name'?a.dataset.name.localeCompare(b.dataset.name):(+b.dataset.since-+a.dataset.since)||a.dataset.name.localeCompare(b.dataset.name));
      const d=document.createElement('details');d.className='card grp';d.dataset.key=key;if(open.has(key))d.open=true;
      const s=document.createElement('summary');const b=document.createElement('b');b.textContent=key;
      const c=document.createElement('span');c.className='muted';c.textContent=' · '+items.length;s.append(b,c);
      const w=document.createElement('div');w.className='items';w.append(...items);d.append(s,w);return d}));
  };
  gb.addEventListener('change',regroup);od.addEventListener('change',regroup);
  document.getElementById('expand').addEventListener('click',e=>{
    const open=e.target.textContent==='Expand all';box.querySelectorAll('details.grp').forEach(d=>d.open=open);
    e.target.textContent=open?'Collapse all':'Expand all'});
}
document.querySelectorAll('.seg button[data-env]').forEach(b=>b.addEventListener('click',()=>{
  document.querySelectorAll('.seg button[data-env]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));
  document.querySelectorAll('[data-envmap]').forEach(s=>s.hidden=s.dataset.envmap!==b.dataset.env)}));
document.querySelectorAll('button.cap[data-panel]').forEach(b=>b.addEventListener('click',()=>{
  const sec=b.closest('[data-envmap]'),p=document.getElementById(b.dataset.panel),was=!p.hidden;
  sec.querySelectorAll('.panel').forEach(x=>x.hidden=true);sec.querySelectorAll('button.cap[data-panel]').forEach(x=>x.setAttribute('aria-expanded','false'));
  if(!was){p.hidden=false;b.setAttribute('aria-expanded','true');p.scrollIntoView({block:'nearest'})}}));
const tf=document.getElementById('tagfilter');
if(tf&&box){
  const setTag=tag=>{
    document.querySelectorAll('button.cap[data-tag]').forEach(x=>x.setAttribute('aria-pressed',String(x.dataset.tag===tag)));
    box.querySelectorAll('details.tc').forEach(el=>el.hidden=!!tag&&!el.dataset.tags.split(' ').includes(tag));
    box.querySelectorAll('details.grp').forEach(d=>{const n=[...d.querySelectorAll('details.tc')].filter(e=>!e.hidden).length;
      d.hidden=n===0;d.querySelector('summary .muted').textContent=' · '+n});
    tf.hidden=!tag;tf.querySelector('b').textContent=tag||'';
    try{history.replaceState(null,'',tag?'#tag='+tag:location.pathname)}catch(e){}};
  document.querySelectorAll('button.cap[data-tag]').forEach(b=>b.addEventListener('click',()=>{
    setTag(b.getAttribute('aria-pressed')==='true'?'':b.dataset.tag);document.getElementById('results').scrollIntoView()}));
  tf.querySelector('button').addEventListener('click',()=>setTag(''));
  gb.addEventListener('change',()=>setTag(tf.hidden?'':tf.querySelector('b').textContent));
  od.addEventListener('change',()=>setTag(tf.hidden?'':tf.querySelector('b').textContent));
  const h=location.hash.match(/^#tag=(.+)$/);if(h)setTag(decodeURIComponent(h[1]));
}
const cmp=document.getElementById('cmp');
if(cmp){const sync=()=>document.querySelectorAll('[data-cmp]').forEach(s=>s.hidden=s.dataset.cmp!==cmp.value);cmp.addEventListener('change',sync);sync()}
"""


def page(title, active, body, depth=0, pointer_sel=""):
    up = "../" * depth
    nav = "".join(
        f'<a href="{up}{href}"{" aria-current=\"page\"" if key == active else ""}>{label}</a>'
        for key, href, label in [("overview", "index.html", "Overview"), ("detailed", "detailed.html", "Detailed"),
                                 ("coverage", "coverage.html", "Coverage"),
                                 ("contribute", "contribute.html", "Contribute")])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,700&family=DM+Serif+Display&display=swap" rel="stylesheet">
<style>{CSS}</style>
<script>try{{const t=localStorage.getItem('theme');if(t)document.documentElement.dataset.theme=t}}catch(e){{}}</script></head><body>
<header class="top"><div class="in"><a class="lockup" href="{up}index.html"><span class="logo"><img src="{up}{LOGO}" alt="LF Energy EVerest" width="116" height="34"></span>
<span><span class="brand">Himalayas</span>
<span class="sub">EVerest test results, self-reported by the companies that build on it</span></span></a>
{pointer_sel}<nav aria-label="Views">{nav}</nav><fieldset class="theme" id="theme"><legend>Color theme</legend><span class="thumb" aria-hidden="true"></span>
<input type="radio" name="theme" id="th-light" value="light"><label for="th-light">Light</label>
<input type="radio" name="theme" id="th-dark" value="dark"><label for="th-dark">Dark</label></fieldset></div><div class="bar"></div></header>
<main>{body}</main>
<footer>Built {NOW:%Y-%m-%d %H:%M} UTC from <code>results/</code>, <code>pointers.json</code> and
<code>schema/vocabulary.json</code>. Every result is self-reported by its integrator; conformance test cases are self-run, and nothing here is certification.
<a href="{up}contribute.html">How to contribute</a> ·
Data: <a href="{up}summary.json">summary.json</a> · <a href="{up}regressions.json">regressions.json</a></footer>
<script>{JS}</script></body></html>"""


def pointer_file(base, name):
    return f"{base}.html" if name == DEFAULT_POINTER else f"{base}-{slug(name)}.html"


POINTER_KINDS = ("development", "stable")


def pointer_meta(name, spec):
    """How a pointer is named on the site: the EVerest line it follows, its kind, any note."""
    spec = spec or {}
    return {"label": spec.get("label") or name, "kind": spec.get("kind"), "note": spec.get("note"),
            "tracks": spec.get("tracks")}


def pointer_sub(P):
    """"stable · stops moving soon · tracking stable/2026.02"."""
    return " · ".join([esc(x) for x in (P["kind"], P["note"]) if x]
                      + ([f'tracking <code>{esc(P["tracks"])}</code>'] if P["tracks"] else []))


def pointer_select(base, current, pointers, meta):
    def text(p):
        m = meta[p]
        return esc(m["label"] + (f' ({m["note"]})' if m["note"] else ""))
    opts = "".join(
        f'<option value="{pointer_file(base, p)}"{" selected" if p == current else ""}>{text(p)}</option>' for p in pointers)
    return (f'<label class="small" style="display:flex;flex-direction:column;color:#C9D4E8">Pointer'
            f'<select data-nav style="min-height:40px;padding:6px 8px;border-radius:8px;background:#fff;color:#1E2530;border:0">{opts}</select></label>')


def vpill(v):
    return f'<span class="pill v-{v}">{VERDICT_LABEL[v]}</span>'


def std_cell(c, std):
    if not c:
        return '<span class="cell none">–</span>'
    dag = ""
    notes = []
    if c["accepted"]:
        notes.append(f'{c["accepted"]} accepted')
    if c["inconclusive"]:
        notes.append(f'{c["inconclusive"]} inconclusive')
    if c["skipped"]:
        notes.append(f'{c["skipped"]} skipped')
    extra = f'<div class="cell muted">{" · ".join(notes)}</div>' if notes else ""
    ran = c["passed"] + c["failed"]
    if c["failed"]:
        return f'<span class="cell bad">✕ {c["failed"]} failed, {c["passed"]} passed{dag}</span>{extra}'
    if c["passed"] == 0:
        return f'<span class="cell none">○ none judged</span>{extra}'
    return f'<span class="cell ok">✓ {c["passed"]}/{ran}{dag}</span>{extra}'


def commit_txt(m):
    behind = "at tip" if m["behind"] == 0 else f'{m["behind"]} move{"s" * (m["behind"] != 1)} behind'
    return f'{commit_link(m["latest"]["commit"])} · {behind}'


def tested_txt(m):
    pill = {"stale": ' <span class="pill v-not-exercised">stale</span>',
            "unknown": ' <span class="pill v-not-exercised" title="freshness unknown">freshness unknown</span>'}.get(m["fresh"], "")
    when = f'{ms_to_dt(m["latest"]["start"]):%Y-%m-%d}' if m["latest"]["start"] else "date unknown"
    return f'<span data-fresh="{m["fresh"]}">{when}{pill}</span>'


VEHICLE_STDS = ["IEC61851", "ISO15118-2", "ISO15118-3", "ISO15118-20", "DIN70121"]
BACKEND_STDS = ["OCPP1.6", "OCPP2.0.1", "OCPP2.1"]


def std_chip(c, std):
    notes = [f'{c[k]} {k}' for k in ("accepted", "inconclusive", "skipped") if c[k]]
    if c["failed"]:
        cls, txt = "bad", f'✕ {c["failed"]} failed, {c["passed"]} passed'
    elif c["passed"] == 0:
        cls, txt = "none", "○ none judged"
    else:
        cls, txt = "ok", f'✓ {c["passed"]}/{c["passed"] + c["failed"]}'
    n = f'<span class="n">{" · ".join(notes)}</span>' if notes else ""
    return f'<span class="schip {cls}"><span><b>{STD_LABEL[std]}</b> {txt}</span>{n}</span>'


def std_chips(m, stds):
    cs = [std_chip(m["counts"][k], k) for k in stds if k in m["counts"]]
    return f'<div class="chips2">{"".join(cs)}</div>' if cs else '<span class="cell none">–</span>'


def rate_line(m):
    return f'<span class="rate small">{pct_html(m["rate"], m["failed_n"])} passed</span>' if m["rate"] is not None else ""


def verdict_cell(m):
    """The verdict pill, with any regression badge on its own line. Build only takes the build's color."""
    if m["verdict"] == "build-only":
        ok = m["latest"]["build"] == "passed"
        pill = f'<span class="pill v-build-only {"b-ok" if ok else "b-bad"}">{"✓" if ok else "✕"} build only</span>'
    else:
        pill = vpill(m["verdict"])
    reg = f'<span class="reg">▲ {len(m["confirmed"])} regressed</span>' if m["confirmed"] else ""
    return f'<td class="vcol">{pill}{rate_line(m)}{reg}</td>'


# ---------------------------------------------------------------- views

def overview(P, pointers):
    setups = P["setups"]
    cur = current(setups)
    cnt = collections.Counter(m["verdict"] for m in cur)
    nreg = sum(1 for m in cur if m["confirmed"])
    nstale = len(setups) - len(cur)
    btns = "".join(
        f'<button type="button" data-f="{v}" aria-pressed="false"><b>{cnt[v]}</b><span class="small">{VERDICT_LABEL[v][2:]}</span></button>'
        for v in DISPLAY_ORDER if cnt[v])
    btns += f'<button type="button" data-f="regressed" aria-pressed="false"><b>▲ {nreg}</b><span class="small">with confirmed regressions</span></button>'
    rate = pooled_txt(cur)
    if rate:
        head, _, low = rate.partition(" passed")
        btns = f'<div class="rtile"><b>{head}</b><span class="small">of tests passed{low}</span></div>' + btns
    if nstale:
        btns += f'<button type="button" data-f="stale" aria-pressed="false"><b>{nstale}</b><span class="small">stale, left out</span></button>'
    groups = collections.OrderedDict()
    for m in setups:
        groups.setdefault(m["integ"], []).append(m)
    body = []
    for integ, ms in groups.items():
        failing = sum(1 for m in ms if m["verdict"] in ("failing", "build-failed"))
        allpass = all(m["verdict"] == "passing" for m in ms)
        rows = []
        for m in sorted(ms, key=lambda m: (DISPLAY_ORDER.index(m["verdict"]), m["config"])):
            rows.append(
                f'<tr data-v="{m["verdict"]}" data-reg="{int(bool(m["confirmed"]))}" data-stale="{int(m["fresh"] == "stale")}"><th scope="row"><a href="{m["href"]}">{esc(m["config"])}</a></th>'
                f'{verdict_cell(m)}<td class="small tested">{commit_txt(m)}<br>{tested_txt(m)}</td><td>{esc(m["latest"]["env"])}</td>'
                f'<td>{std_chips(m, VEHICLE_STDS)}</td><td>{std_chips(m, BACKEND_STDS)}</td>'
                f'<td>{std_cell(m["counts"].get("other"), "other")}</td></tr>')
        rate = pooled_txt(current(ms))
        summ = (f'{len(ms)} setup{"s" * (len(ms) != 1)}' + (" · all passing" if allpass else f" · {failing} failing")
                + (f" · {rate}" if rate else ""))
        body.append(
            f'<details class="card group" id="integ-{slug(integ)}"{"" if allpass else " open"}><summary>{integ_logo(P, integ)}<b>{esc(ms[0]["integ_name"])}</b> <span class="muted small">{summ}</span></summary>'
            f'<div class="scroll"><table><thead><tr><th scope="col">Setup</th><th scope="col">Verdict</th><th scope="col">Tested</th>'
            f'<th scope="col">Env</th><th scope="col">Vehicle communication</th><th scope="col">Backend</th><th scope="col">Other tests</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div></details>')
    tip = P["tip"]
    tipline = (f'tip {commit_link(tip)}, moved {P["log"][-1]["at"][:10]}'
               if tip else "no moves logged yet")
    empty = "" if setups else (f'<p class="notice">No submission tested a commit in {esc(P["label"])}\'s log yet. '
                                f'Results at other commits are not attributed here. <a href="contribute.html">Report a run</a>.</p>')
    return f"""<section class="row"><h1>{esc(P['label'])}</h1><span class="muted">{pointer_sub(P)} · {tipline}</span></section>
<p class="cta">Run EVerest on your own hardware or CI? <a href="contribute.html">Report your results</a>: register once, then open one pull request per run.</p>
{all_integrators(P) if setups else ""}{empty}<section class="summarybar" aria-label="Filter by verdict">{btns if setups else ""}</section>
{''.join(body)}"""


def current(setups):
    """The setups the overview counts: stale ones are listed, never counted."""
    return [m for m in setups if m["fresh"] != "stale"]


def all_integrators(P):
    """Every allowlisted integrator on this pointer: reporting ones link to their card and their
    rows on the detailed page, silent ones are listed as not reported yet."""
    groups = collections.OrderedDict()
    for m in P["setups"]:
        groups.setdefault(m["integ"], []).append(m)
    detailed = pointer_file("detailed", P["name"])
    # Logos get their own column, as wide as the widest, so names stay aligned.
    slot = (lambda i: f'<td class="lcell">{integ_logo(P, i)}</td>') if P["logos"] else (lambda i: "")
    rows = []
    for integ, ms in groups.items():
        cur = current(ms)
        cnt = collections.Counter(m["verdict"] for m in cur)
        pills = [f'<span class="pill v-{v}">{VERDICT_LABEL[v][0]} {cnt[v]} {VERDICT_LABEL[v][2:]}</span>'
                 for v in DISPLAY_ORDER if cnt[v]]
        if len(ms) > len(cur):
            pills.append(f'<span class="pill v-not-exercised">{len(ms) - len(cur)} stale</span>')
        last = max(m["latest"]["start"] or 0 for m in ms)
        rows.append(f'<tr>{slot(integ)}<th scope="row"><a href="#integ-{slug(integ)}">{esc(ms[0]["integ_name"])}</a></th>'
                    f'<td class="num"><a href="{detailed}#detail-{slug(integ)}">{len(ms)}</a></td><td><div class="pills">{"".join(pills)}</div>'
                    f'{f"<div class=\"small rate\">{rate}</div>" if (rate := pooled_txt(cur)) else ""}</td>'
                    f'<td>{f"{ms_to_dt(last):%Y-%m-%d}" if last else "unknown"}</td></tr>')
    for a in P["silent"]:
        name = a["name"] if a["name"] != a["id"] else f'{a["id"]} (anonymous)'
        rows.append(f'<tr>{slot(a["id"])}<th scope="row">{esc(name)}</th><td class="num">0</td>'
                    f'<td><span class="pill v-not-reported">{VERDICT_LABEL["not-reported"][0]} not reported yet</span></td><td>–</td></tr>')
    n, k = len(groups) + len(P["silent"]), len(groups)
    return (f'<details class="card allint"><summary><h2 style="display:inline">All integrators</h2> '
            f'<span class="muted small">{n} integrator{"s" * (n != 1)}, {k} reporting · {len(P["setups"])} setup{"s" * (len(P["setups"]) != 1)}</span></summary>'
            f'<div class="scroll"><table><thead><tr>{'<th scope="col" aria-label="Logo"></th>' if P["logos"] else ""}<th scope="col">Integrator</th><th scope="col" class="num">Setups</th>'
            f'<th scope="col">Verdicts</th><th scope="col">Last reported</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></details>')


STRIP_H = 18


def run_strip(m, n=14):
    """One bar per run, oldest first. A run with failures is a red bar as tall as its failed
    count, scaled to the worst run shown; a clean run is a short green tick."""
    runs = m["runs"][-n:]
    oc = [collections.Counter(t["outcome"] for t in r["tests"]) for r in runs]
    worst = max([c["failed"] for c in oc] + [1])
    cells = []
    for r, c in zip(runs, oc):
        when = f'{short(r["commit"])} {ms_to_dt(r["start"]):%d-%m %H:%M}'
        rate = pass_rate(c["passed"], c["failed"])
        if r["build"] != "passed":
            cls, h, tip = "s-build-failed", STRIP_H, "build failed"
        elif rate is None:
            cls, h, tip = "s-not-exercised", 4, "no judged tests"
        elif c["failed"]:
            cls, h = "s-failing", 6 + round((STRIP_H - 6) * c["failed"] / worst)
            tip = f'{c["failed"]} failed of {c["passed"] + c["failed"]} judged · {pct(rate, c["failed"])} passed'
        else:
            cls, h, tip = "s-passing", 4, f'none failed of {c["passed"]} judged'
        cells.append(f'<span class="{cls}" style="height:{h}px" title="{when}: {tip}"></span>')
    return f'<div class="strip" role="img" aria-label="last {len(runs)} runs, oldest first">{"".join(cells)}</div>'


def regressions_panel(P):
    states = [("confirmed", "▲ Confirmed", "Failed again after the first failing run."),
              ("suspected", "▵ Suspected", "One failing run so far. No badge until it fails again."),
              ("flaky", "Flaky, failing now", "Disagreeing or flipping runs in the last 10.")]
    buckets = {k: collections.OrderedDict() for k, _, _ in states}
    for m in P["setups"]:
        for r in m["regs"]:
            buckets["confirmed" if r["confirmed"] else "suspected"].setdefault(m["integ"], []).append((m, r))
        for r in m["flaky"]:
            buckets["flaky"].setdefault(m["integ"], []).append((m, r))
    total = sum(len(v) for b in buckets.values() for v in b.values())
    if not total:
        return f'<section class="card"><h2>Open regressions · none</h2>{flaky_passing(P)}</section>'
    secs = []
    for key, title, note in states:
        if not buckets[key]:
            continue
        integs = []
        for integ, items in buckets[key].items():
            name = items[0][0]["integ_name"]
            rows = []
            groups = collections.OrderedDict()
            for m, r in items:
                groups.setdefault((m["slug"], norm_msg(r["message"], r.get("key")), r.get("range")), (m, r, []))[2].append(r)
            for (sl, msg, rng), (m, r, rs) in groups.items():
                if rng:
                    unt = f' ({", ".join(commit_link(s) for s in r["untested"])} untested)' if r["untested"] else ""
                    where = f'{commit_link(rng[0])}..{commit_link(rng[1])}{unt}'
                    if any(x.get("previously_flaky") for x in rs):
                        where += '<br><span class="muted">previously flaky</span>'
                else:
                    where = f'since {commit_link(r["since"])}'
                names = "<br>".join(f"<code>{esc(x['name'])}</code>" for x in rs[:4]) + (f"<br>+{len(rs) - 4} more" if len(rs) > 4 else "")
                rows.append(f'<tr><td><a href="{m["href"]}">{esc(m["config"])}</a></td>'
                            f'<td>{STD_LABEL.get(r["std"], r["std"])} · {len(rs)}</td><td class="small">{names}</td>'
                            f'<td class="small">{esc(msg)}</td><td class="small">{where}</td></tr>')
            integs.append(f'<details class="integ"><summary><b>{esc(name)}</b> <span class="muted small">· {len(items)} test{"s" * (len(items) != 1)}</span></summary>'
                          f'<div class="scroll"><table><thead><tr><th>Setup</th><th>Standard · tests</th><th>Tests</th><th>Message</th><th>Began in</th></tr></thead>'
                          f'<tbody>{"".join(rows)}</tbody></table></div></details>')
        n = sum(len(v) for v in buckets[key].values())
        secs.append(f'<h3 style="margin-top:12px">{title} · {n}</h3><p class="small muted" style="margin:0 0 6px">{note}</p>'
                    f'{"".join(integs)}')
    return (f'<details class="card"><summary><h2 style="display:inline">Open regressions · {total} tests</h2></summary>'
            f'<p class="small muted">Passed at the previous tested commit and fail at the next. Listed until the test passes again.</p>'
            f'{"".join(secs)}{flaky_passing(P)}</details>')


def flaky_passing(P):
    rows = [(m, f) for m in P["setups"] for f in m["flaky_passing"]]
    if not rows:
        return ""
    lis = "".join(f'<li><a href="{m["href"]}">{esc(m["config"])}</a> · <code>{esc(f["name"])}</code> '
                  f'<span class="muted">{f["flips"]} flips in last 10 runs</span></li>' for m, f in rows)
    return f'<details class="small" style="margin-top:10px"><summary>Flaky, passing now · {len(rows)}</summary><ul>{lis}</ul></details>'


def build_mark(status):
    ok = status == "passed"
    return f'<span class="cell {"ok" if ok else "bad"}" title="build {esc(status)}" aria-label="build {esc(status)}">{"✓" if ok else "✕"}</span>'


def results_cell(oc, n):
    rest = [f"{oc[k]} {k}" for k in ("skipped", "inconclusive", "accepted") if oc[k]]
    head = f'<span class="cell ok">{oc["passed"]} passed</span>' + (f' · <span class="cell bad">{oc["failed"]} failed</span>' if oc["failed"] else "")
    return f'<div class="small" style="white-space:nowrap">{head}</div><div class="small muted" style="white-space:nowrap">{" · ".join([f"{n} tests"] + rest)}</div>'


def detail_table(P):
    groups = collections.OrderedDict()
    for m in sorted(P["setups"], key=lambda m: m["integ_name"].lower()):
        groups.setdefault(m["integ"], []).append(m)
    bodies = "".join(f'<tbody id="detail-{slug(integ)}">{"".join(map(detail_row, ms))}</tbody>'
                     for integ, ms in groups.items())
    return detail_shell(P, bodies)


def detail_row(m):
    L = m["latest"]
    oc = collections.Counter(t["outcome"] for t in L["tests"])
    rep = f'<a href="{esc(L["report"])}">report</a>' if L["report"] else '<span class="muted">none</span>'
    return (f'<tr><td>{esc(m["integ_name"])}</td><th scope="row"><a href="{m["href"]}">{esc(m["config"])}</a></th>'
            f'<td>{vpill(m["verdict"])}{rate_line(m)}</td><td>{results_cell(oc, len(L["tests"]))}</td>'
            f'<td class="small">{commit_txt(m)}</td><td class="small">{tested_txt(m)}</td><td>{esc(L["env"])}</td><td>{build_mark(L["build"])}</td>'
            f'<td class="small">{esc(L["tool"]) or "–"}</td><td>{run_strip(m)}</td><td class="small">{rep}</td></tr>')


def detail_shell(P, bodies):
    return f"""<section class="card" style="padding:0"><h2 style="padding:16px 20px 0">Setups on {esc(P['label'])}</h2><div class="scroll"><table>
<thead><tr><th>Integrator</th><th>Setup</th><th>Verdict</th><th>Results</th><th>Tested commit</th><th>Tested on</th><th>Env</th><th>Build</th><th>Harness</th>
<th>Last 14 runs</th><th>Evidence</th></tr></thead>
{bodies or '<tbody><tr><td colspan="11" class="muted">No setups.</td></tr></tbody>'}</table></div>
<p class="small muted" style="padding:0 20px 14px;margin:8px 0 0">Last 14 runs, oldest first: <span class="strip" style="display:inline-flex;vertical-align:-4px"><span class="s-passing" style="height:4px"></span></span> no failed tests · <span class="strip" style="display:inline-flex;vertical-align:-4px"><span class="s-failing" style="height:9px"></span><span class="s-failing" style="height:18px"></span></span> failed tests, taller for more, scaled to the worst run shown · hover a run for its counts</p></section>"""


def comparison(P, others):
    if not others:
        return ""
    opts = "".join(f'<option value="{slug(o["name"])}">{esc(o["label"])}</option>' for o in others)
    secs = []
    for O in others:
        omap = {(m["integ"], m["config"]): m for m in O["setups"]}
        rows = []
        for m in P["setups"]:
            o = omap.get((m["integ"], m["config"]))
            if not o:
                rows.append(f'<tr><td>{esc(m["integ"])} / {esc(m["config"])}</td><td>{vpill(m["verdict"])}{rate_line(m)}</td><td>{vpill("not-reported")}</td><td colspan="3" class="muted small">no result under {esc(O["label"])}</td></tr>')
                continue
            a = {t["name"] for t in m["latest"]["tests"] if t["outcome"] == "failed"}
            b = {t["name"] for t in o["latest"]["tests"] if t["outcome"] == "failed"}
            ran_a = {t["name"] for t in m["latest"]["tests"]}
            ran_b = {t["name"] for t in o["latest"]["tests"]}
            only_a = len((a - b) & ran_b)
            only_b = len((b - a) & ran_a)
            notrun = len((a - ran_b) | (b - ran_a))
            rows.append(f'<tr><td>{esc(m["integ"])} / {esc(m["config"])}</td><td>{vpill(m["verdict"])}{rate_line(m)}</td><td>{vpill(o["verdict"])}{rate_line(o)}</td>'
                        f'<td class="num">{only_a}</td><td class="num">{only_b}</td><td class="num">{len(a & b)}</td><td class="num">{notrun}</td></tr>')
        secs.append(f'<div data-cmp="{slug(O["name"])}" class="scroll"><table><thead><tr><th>Setup</th><th>{esc(P["label"])}</th><th>{esc(O["label"])}</th>'
                    f'<th class="num">Fail only here</th><th class="num">Fail only there</th><th class="num">Both</th><th class="num">Not run on one side</th></tr></thead>'
                    f'<tbody>{"".join(rows)}</tbody></table></div>')
    return f"""<section class="card"><div class="row" style="margin-bottom:8px"><h2 style="margin:0">Compare with</h2>
<select id="cmp" style="min-height:40px;padding:6px 8px;border-radius:8px">{opts}</select></div>{''.join(secs)}</section>"""


def unattributed(P):
    """Results filed under this pointer whose commit is not in its log. Linked, never counted."""
    if not P["unattributed"]:
        return ""
    lis = "".join(f'<li><a href="{esc(r["path"])}">{esc(r["path"])}</a> · {esc(r["integ"])} / {esc(r["config"])} · '
                  f'commit {commit_link(r["commit"])}</li>' for r in P["unattributed"])
    return (f'<details class="card"><summary><h2 style="display:inline">Not attributed · {len(P["unattributed"])}</h2></summary>'
            f'<p class="small muted">Filed under {esc(P["label"])}, but the tested commit is not in its pointer log, '
            f'so these results are not counted anywhere on this site.</p><ul class="small">{lis}</ul></details>')


def detailed(P, others):
    return (f'<section class="row"><h1>Detailed · {esc(P["label"])}</h1></section>'
            + regressions_panel(P) + detail_table(P) + comparison(P, others) + unattributed(P))


CAP_GROUPS = [
    ("Charging", [("AC", "AC"), ("DC", "DC"), ("V2X", "Bidirectional (V2X)")]),
    ("Vehicle communication", [("IEC61851", "IEC 61851 basic"), ("ISO15118-3", "ISO 15118-3 SLAC"),
                               ("ISO15118-2", "ISO 15118-2"), ("ISO15118-20", "ISO 15118-20"),
                               ("DIN70121", "DIN 70121")]),
    ("Backend and energy management", [("OCPP1.6", "OCPP 1.6"), ("OCPP2.0.1", "OCPP 2.0.1"),
                                       ("OCPP2.1", "OCPP 2.1"), ("EEBUS", "EEBUS")]),
    ("Features", [("PnC", "Plug & Charge"), ("TLS", "TLS"), ("SmartCharging", "Smart charging"),
                  ("Reservation", "Reservation"), ("Eichrecht", "Eichrecht")]),
]
CARRIERS = {}  # feature tag -> carrier tags, from vocabulary.json featureCarriers
CARRIER_LABEL = {"AC": "AC", "DC": "DC", "ISO15118-2": "ISO 15118-2", "ISO15118-20": "ISO 15118-20", "OCPP": "OCPP"}
STATE_RANK = ["pass", "own", "fail", "none"]
STATE_CLASS = {"pass": "l4", "own": "l2", "fail": "l3", "none": "l1", None: "l1"}


def has_tag(t, tag):
    return any(x.startswith("OCPP") for x in t["tags"]) if tag == "OCPP" else tag in t["tags"]


def tag_state(tests):
    if not tests:
        return None, collections.Counter()
    oc = collections.Counter(t["outcome"] for t in tests)
    if oc["failed"]:
        return "fail", oc
    if oc["passed"]:
        return ("pass" if any("compliance" in t["tags"] for t in tests) else "own"), oc
    return "none", oc


def tag_counts_txt(oc):
    head = (f'{oc["failed"]} failed, {oc["passed"]} passed' if oc["failed"]
            else f'{oc["passed"]} passed' if oc["passed"] else "none judged")
    rest = [f"{oc[k]} {k}" for k in ("accepted", "inconclusive", "skipped") if oc[k]]
    return " · ".join([head] + rest)


def setup_states(m, pred):
    return tag_state([t for t in m["latest"]["tests"] if pred(t)])


def global_tile(setups, pred, label, pid, tag):
    """Best setup wins; the panel lists every setup's counts for this tag."""
    per = [(m, *setup_states(m, pred)) for m in setups]
    per = [x for x in per if x[1]]
    if not per:
        return "div", "l1", "", f"<b>{esc(label)}</b><span>no reports</span>", ""
    best = min((s for _, s, _ in per), key=STATE_RANK.index)
    npass = sum(1 for _, s, _ in per if s in ("pass", "own"))
    nfail = sum(1 for _, s, _ in per if s == "fail")
    if npass:
        cap = f"passing on {npass}" + (f" · {nfail} failing" if nfail else "")
    elif nfail:
        cap = f"failing on {nfail}"
    else:
        cap = f"{len(per)} setup{'s' * (len(per) != 1)} · none judged"
    rows = "".join(
        f'<tr><td>{esc(m["integ_name"])}</td><td><a href="{m["href"]}#tag={tag}">{esc(m["config"])}</a></td>'
        f'<td class="{"h4c" if s in ("pass", "own") else "h3c" if s == "fail" else ""}">{tag_counts_txt(oc)}</td><td>{esc(m["latest"]["env"])}</td></tr>'
        for m, s, oc in sorted(per, key=lambda x: (STATE_RANK.index(x[1]), x[0]["integ_name"], x[0]["config"])))
    panel = (f'<div class="panel card" id="{pid}" hidden><h3>{esc(label)} · {len(per)} setup{"s" * (len(per) != 1)}</h3><div class="scroll"><table>'
             f'<thead><tr><th>Integrator</th><th>Setup</th><th>{esc(label)} tests</th><th>Env</th></tr></thead><tbody>{rows}</tbody></table></div></div>')
    return "button", STATE_CLASS[best], f' type="button" data-panel="{pid}" aria-expanded="false"', f"<b>{esc(label)}</b><span>{cap}</span>", panel


def setup_tile(m, pred, label, tag):
    s, oc = setup_states(m, pred)
    if not s:
        return "div", "l1", "", f"<b>{esc(label)}</b><span>no reports</span>", ""
    return "button", STATE_CLASS[s], f' type="button" data-tag="{tag}" aria-pressed="false"', f"<b>{esc(label)}</b><span>{tag_counts_txt(oc)}</span>", ""


def carrier_chips(pool, tag, carriers):
    cs = []
    for c in carriers:
        st = [setup_states(m, lambda t: tag in t["tags"] and has_tag(t, c))[0] for m in pool]
        st = [x for x in st if x]
        best = min(st, key=STATE_RANK.index) if st else None
        cls = "ok" if best in ("pass", "own") else "bad" if best == "fail" else ""
        cs.append(f'<em class="{cls}">{CARRIER_LABEL.get(c, c)}</em>')
    return f'<span class="via">{"".join(cs)}</span>'


def capability_map(setups, scope, single=None):
    """Pointer-wide map over `setups`, or one setup's own map when `single` is given."""
    groups, panels = [], []
    for gname, caps in CAP_GROUPS:
        tiles = []
        for tag, label in caps:
            carriers = CARRIERS.get(tag)
            pred = lambda t, tag=tag: tag in t["tags"]
            if single:
                el, cls, attrs, inner, panel = setup_tile(single, pred, label, tag)
            else:
                el, cls, attrs, inner, panel = global_tile(setups, pred, label, f"p-{scope}-{slug(tag)}", tag)
            panels.append(panel)
            chips = carrier_chips([single] if single else setups, tag, carriers) if carriers else ""
            tiles.append(f'<{el} class="cap {cls}"{attrs}>{inner}{chips}</{el}>')
        groups.append(f'<div class="dom"><h4>{gname}</h4><div class="chips">{"".join(tiles)}</div></div>')
    legend = ('<div class="legend"><span><i class="l4"></i>conformance cases pass</span><span><i class="l2"></i>passes, own tests only</span>'
              '<span><i class="l3"></i>failing</span><span><i class="l1"></i>no reports</span>'
              '<span>carrier chips fill once a test carries both tags</span></div>')
    return f'<div class="capmap">{"".join(groups)}{legend}</div>{"".join(panels)}'


def coverage(P, vocab):
    envs = [("all", "All", P["setups"])] + [(e.lower(), e, [m for m in P["setups"] if m["latest"]["env"] == e]) for e in vocab["testEnvironment"]]
    seg = "".join(f'<button type="button" data-env="{k}" aria-pressed="{str(k == "all").lower()}">{lab} · {len(ms)}</button>' for k, lab, ms in envs)
    maps = "".join(f'<section class="card" data-envmap="{k}"{"" if k == "all" else " hidden"}>{capability_map(ms, k)}</section>' for k, _, ms in envs)
    return f"""<section class="row"><h1>Coverage · {esc(P['label'])}</h1><span class="muted">{len(P['setups'])} setups reporting</span></section>
<p class="small muted" style="margin:0;max-width:80ch">What reported tests show working, from each setup's latest run. A tile is green when any setup passes its tests, and the caption keeps the failing count.
Select a tile to see the setups behind it. Loaded modules and EVerest's own feature claims are not counted.</p>
<div class="seg" role="group" aria-label="Test environment">{seg}</div>
{maps}"""


def chart(m, P):
    moves = list(range(m["first_tested"], len(P["shas"])))
    pts = []
    for i in moves:
        rs = m["by_move"][i]
        if rs:
            r = rs[-1]
            pts.append((i, sum(1 for t in r["tests"] if t["outcome"] == "failed"), len(r["tests"])))
        else:
            pts.append((i, None, None))
    vmax = max([v for _, v, _ in pts if v is not None] + [1])
    W, x0, dx, y0, hgt = max(640, 80 + 90 * len(moves)), 60, 90, 170, 140
    X = lambda k: x0 + dx * k
    Y = lambda v: y0 - v / vmax * hgt
    parts = [f'<line x1="40" y1="{y0}" x2="{W - 10}" y2="{y0}" stroke="var(--line)"/>',
             f'<text x="34" y="{y0 + 4}" text-anchor="end" font-size="11" fill="var(--muted)">0</text>',
             f'<line x1="40" y1="{y0 - hgt}" x2="{W - 10}" y2="{y0 - hgt}" stroke="var(--line2)"/>',
             f'<text x="34" y="{y0 - hgt + 4}" text-anchor="end" font-size="11" fill="var(--muted)">{vmax}</text>']
    prev, prev_n = None, None
    for k, (i, v, n) in enumerate(pts):
        parts.append(f'<a href="{EVEREST_COMMIT}{esc(P["shas"][i])}"><text x="{X(k)}" y="192" text-anchor="middle" font-size="11" font-family="DM Mono,monospace" fill="var(--soft)">{short(P["shas"][i])}</text></a>')
        if v is None:
            parts.append(f'<rect x="{X(k) - 6}" y="{y0 - 20}" width="12" height="20" fill="none" stroke="var(--dash)" stroke-dasharray="3 3"/>')
            parts.append(f'<text x="{X(k)}" y="208" text-anchor="middle" font-size="11" fill="var(--muted)">untested</text>')
            continue
        if prev:
            gap = k - prev[0] > 1
            parts.append(f'<line x1="{X(prev[0])}" y1="{Y(prev[1])}" x2="{X(k)}" y2="{Y(v)}" stroke="var(--ink)" stroke-width="2"{" stroke-dasharray=\"5 5\"" if gap else ""}/>')
        if n != prev_n:
            parts.append(f'<text x="{X(k)}" y="208" text-anchor="middle" font-size="11" fill="var(--muted)">{n} tests</text>')
        prev, prev_n = (k, v), n
    for k, (i, v, n) in enumerate(pts):
        if v is not None:
            parts.append(f'<circle cx="{X(k)}" cy="{Y(v)}" r="5" fill="{"#1E8F64" if v == 0 else "#C0392B"}"><title>{short(P["shas"][i])}: {v} failed of {n}</title></circle>')
    return (f'<div class="scroll"><svg role="img" aria-label="failed tests per pointer move" width="{W}" height="220" viewBox="0 0 {W} 220">'
            + "".join(parts) + "</svg></div>")


STATUS_ORDER = ["failed", "inconclusive", "accepted", "skipped", "passed"]
STATUS_LABEL = {"failed": "Failed", "inconclusive": "Inconclusive", "accepted": "Accepted and waived",
                "skipped": "Skipped", "passed": "Passed"}


def test_item(t, m, P, marks):
    since_i, badge = -1, ""
    info = marks.get(t["name"])
    if info:
        kind, r = info
        since_i = P["shas"].index(r["since"])
        if kind == "regs":
            if r["confirmed"]:
                badge = '<span class="reg">▲ regression</span>'
            else:
                badge = '<span class="reg" style="border-style:dashed">▵ suspected</span>'
            unt = f' ({", ".join(commit_link(x) for x in r["untested"])} untested)' if r["untested"] else ""
            when = f'began in {commit_link(r["range"][0])}..{commit_link(r["range"][1])}{unt}'
            if r["previously_flaky"]:
                when += " · previously flaky"
        elif kind == "flaky":
            badge = '<span class="pill v-passing-partial">flaky</span>'
            when = f'failing since {commit_link(r["since"])}'
        elif kind == "long":
            badge = '<span class="pill v-not-exercised">long-standing</span>'
            when = f'never passed; {r["runs_failed"]} failing runs since first appearance at {commit_link(r["since"])}'
        else:
            when = f'inconclusive since {commit_link(r["since"])}'
    else:
        when = ""
    notes = []
    if t["outcome"] == "accepted":
        notes.append(f'clause {esc(t["clause"])}' if t["clause"] else '<span class="pill v-passing-partial">clause not declared</span>')
    if t["rig"]:
        notes.append("rig limitation")
    if t["untagged"]:
        notes.append("<b>untagged</b>")
    cluster = norm_msg(t["message"], t["key"]) if t["message"] else "(no message)"
    msg = f'<pre class="msg">{esc(t["message"].strip())}</pre>' if t["message"] else '<p class="muted small">No message reported.</p>'
    if t["measurements"]:
        mrows = "".join(
            f'<tr class="{"meas-ok" if x["outcome"] == "PASS" else "meas-fail"}"><td>{esc(str(x["name"]))}</td>'
            f'<td class="num">{esc(str(x["value"]))} {esc(x["units"])}</td><td>{esc(x["limits"]) or "–"}</td><td>{esc(x["outcome"]) or "–"}</td></tr>'
            for x in t["measurements"])
        msg += (f'<div class="scroll"><table class="small"><thead><tr><th>Measurement</th><th class="num">Value</th>'
                f'<th>Limits</th><th>Outcome</th></tr></thead><tbody>{mrows}</tbody></table></div>')
    std = STD_LABEL.get(t["std"], t["std"])
    return (f'<details class="tc" data-status="{t["outcome"]}" data-status-label="{STATUS_LABEL[t["outcome"]]}" '
            f'data-std="{esc(std)}" data-tags="{esc(" ".join(t["tags"]))}" data-cluster="{esc(cluster)}" data-since="{since_i}" data-name="{esc(t["name"])}">'
            f'<summary>{badge} <code>{esc(t["name"])}</code> <span class="small muted">{std}{" · " + " · ".join(notes) if notes else ""}</span></summary>'
            f'<div class="tcbody">{f"<p class=small>{when}</p>" if when else ""}{msg}'
            f'<p class="small muted">raw status <code>{esc(t["raw"] or "–")}</code> · tags <code>{esc(", ".join(t["tags"]))}</code>'
            f'{" · <code>" + esc(t["file"]) + "</code>" if t["file"] else ""}</p></div></details>')


def test_results(m, P):
    L = m["latest"]
    marks = {}
    for kind in ("regs", "flaky", "long", "inconclusive"):
        for r in m[kind]:
            marks[r["name"]] = (kind, r)
    by = collections.OrderedDict((s, []) for s in STATUS_ORDER)
    for t in sorted(L["tests"], key=lambda t: t["name"]):
        by[t["outcome"]].append(t)
    groups = []
    for s, ts in by.items():
        if not ts:
            continue
        items = sorted(ts, key=lambda t: (-P["shas"].index(marks[t["name"]][1]["since"]) if t["name"] in marks else 0, t["name"]))
        groups.append(f'<details class="card grp" data-key="{STATUS_LABEL[s]}"><summary><b>{STATUS_LABEL[s]}</b> '
                      f'<span class="muted">· {len(ts)}</span></summary><div class="items">'
                      + "".join(test_item(t, m, P, marks) for t in items) + "</div></details>")
    return f"""<section style="display:flex;flex-direction:column;gap:10px" id="results">
<div class="row"><h2 style="margin:0">Test results · {len(L['tests'])}</h2>
<label class="small">Group by <select id="grpby"><option value="status">Status</option><option value="std">Standard</option><option value="cluster">Message</option></select></label>
<label class="small">Order <select id="order"><option value="since">Newest failure first</option><option value="name">Name</option></select></label>
<button type="button" id="expand" class="small" style="min-height:36px;padding:4px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card)">Expand all</button></div>
<div id="tagfilter" class="tagfilter small" hidden>Showing tests tagged <b></b> <button type="button" style="min-height:32px;padding:2px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card)">Show all</button></div>
<div id="groups" style="display:flex;flex-direction:column;gap:10px">{''.join(groups)}</div></section>"""


def setup_page(m, P):
    L = m["latest"]
    oc = collections.Counter(t["outcome"] for t in L["tests"])
    prov = []
    if L["tool"]:
        prov.append(f"own harness · {esc(L['tool'])}")
    if L["test_system"]:
        prov.append(f"reference test system · {esc(L['test_system'].get('name', ''))} {esc(L['test_system'].get('version', ''))}")
    behind = "at tip" if m["behind"] == 0 else f'{m["behind"]} move{"s" * (m["behind"] != 1)} behind tip {commit_link(P["tip"])}'
    runs = []
    for r in reversed(m["runs"]):
        f = sum(1 for t in r["tests"] if t["outcome"] == "failed")
        s = sum(1 for t in r["tests"] if t["outcome"] in ("skipped", "accepted", "inconclusive"))
        rep = f'<a href="{esc(r["report"])}">report</a> · ' if r["report"] else ""
        runs.append(f'<tr><td>{ms_to_dt(r["start"]):%Y-%m-%d %H:%M}</td><td>{commit_link(r["commit"])}</td><td class="num">{len(r["tests"])}</td>'
                    f'<td class="num">{f}</td><td class="num">{s}</td><td>{vpill(verdict(r))}</td>'
                    f'<td class="small">{rep}<a href="../{r["path"]}">{esc(r["path"].split("/")[-1])}</a></td></tr>')
    dupes = f'<p class="notice">Duplicate test names in the latest run: {", ".join(map(esc, L["dupes"]))}</p>' if L["dupes"] else ""
    reg = f'<span class="reg">▲ {len(m["confirmed"])} regressed</span>' if m["confirmed"] else ""
    nsus = len(m["regs"]) - len(m["confirmed"])
    reg += f' <span class="reg" style="border-style:dashed">▵ {nsus} suspected</span>' if nsus else ""
    evidence = (f'<a href="{esc(L["report"])}">report</a> · linked by the integrator · <span class="muted">unverified link (no hash)</span>'
                if L["report"] else "none")
    return f"""<nav aria-label="Breadcrumb" class="small muted"><a href="../{pointer_file("index", P["name"])}">{esc(P['label'])}</a> › {esc(m['integ_name'])} › {esc(m['config'])}</nav>
<section class="card" style="display:flex;flex-direction:column;gap:14px"><div class="row"><h1>{esc(m['config'])}</h1>{vpill(m['verdict'])}{reg}
<span class="muted">{oc['passed']} passed · {oc['failed']} failed · {oc['inconclusive']} inconclusive · {oc['accepted']} accepted · {oc['skipped']} skipped of {len(L['tests'])}{f" · {pct_html(m['rate'], m['failed_n'])} of judged tests passed" if m['rate'] is not None else ""}</span></div>
<dl class="grid" style="margin:0">
<div><dt class="small muted">Integrator</dt><dd style="margin:0">{integ_logo(P, m['integ'], "../")}{esc(m['integ_name'])}</dd></div>
<div><dt class="small muted">Tested commit</dt><dd style="margin:0">{commit_link(L['commit'])} · {behind}</dd></div>
<div><dt class="small muted">Tested on</dt><dd style="margin:0">{ms_to_dt(L['start']):%Y-%m-%d %H:%M} UTC</dd></div>
<div><dt class="small muted">Environment</dt><dd style="margin:0">{" · ".join(esc(x) for x in (L['env'], L['os']) if x)} · build {build_mark(L['build'])}</dd></div>
<div><dt class="small muted">Provenance</dt><dd style="margin:0">{'<br>'.join(prov) or 'not declared'}</dd></div>
<div><dt class="small muted">Evidence</dt><dd style="margin:0">{evidence}</dd></div>
</dl><div><div class="small muted">Modules · {len(L['modules'])}</div><div class="modules">{''.join(f'<code>{esc(x)}</code>' for x in L['modules'])}</div></div></section>
{dupes}<details class="card" aria-label="Capabilities"><summary><h2 style="display:inline">Capabilities</h2></summary><div style="margin-top:10px">{capability_map([m], "s", single=m)}</div>
<p class="small muted" style="margin:8px 0 0">Select a tile to show only that tag's tests below.</p></details>
<section class="card"><h2>Failed tests per pointer move</h2>{chart(m, P)}
<p class="small muted" style="margin:0">Test count changes are marked, so fewer failures from removed tests do not read as a fix.</p></section>
{test_results(m, P)}
<section class="card" style="padding:0"><h2 style="padding:16px 20px 0">{len(m['runs'])} runs on {esc(P['label'])}</h2><div class="scroll"><table>
<thead><tr><th>Ran (UTC)</th><th>Commit</th><th class="num">Tests</th><th class="num">Failed</th><th class="num">Not judged</th><th>Verdict</th><th>Evidence</th></tr></thead>
<tbody>{''.join(runs)}</tbody></table></div></section>"""


# ---------------------------------------------------------------- contribute page

REPO_BLOB = "https://github.com/EVerest/Himalayas/blob/main/"
GUIDE_ID = "submitting-test-results-to-himalayas"
MD_MARKER = re.compile(r"( {0,3})([-*]|\d+\.)( +)")
MD_FENCE = re.compile(r"\s*(```+)")


def md_slug(text, seen):
    """GitHub's heading anchor, so links written against the repository still work here."""
    s = re.sub(r"[^\w\- ]", "", re.sub(r"[`*]", "", text).lower()).replace(" ", "-")
    n = seen.get(s, 0)
    seen[s] = n + 1
    return f"{s}-{n}" if n else s


def md_href(h):
    if re.match(r"[a-z]+:", h) or h.startswith("#"):
        return h
    path, _, frag = h.partition("#")
    if path in ("CONTRIBUTING.md", "README.md#submit-your-first-test-report-in-5-steps"):
        return "#" + (frag or GUIDE_ID)
    return REPO_BLOB + path + (f"#{frag}" if frag else "")


def md_inline(t):
    code = []
    t = re.sub(r"`([^`]+)`", lambda m: code.append(m.group(1)) or f"\x00{len(code) - 1}\x00", t)
    t = esc(t, quote=False)
    t = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", lambda m: f'<a href="{esc(md_href(m.group(2)))}">{m.group(1)}</a>', t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![\w*])\*([^*\s][^*]*?)\*(?![\w*])", r"<em>\1</em>", t)
    return re.sub("\x00(\\d+)\x00", lambda m: f"<code>{esc(code[int(m.group(1))])}</code>", t)


def md_block_start(line):
    s = line.lstrip()
    return (s.startswith(("#", "```", ">", "|")) or MD_MARKER.match(line) is not None
            or re.fullmatch(r"-{3,}", line.strip()) is not None)


def md_render(text, seen, shift=1):
    """The Markdown the repository's docs use: headings, lists, tables, fences, quotes."""
    lines, out, i = text.split("\n"), [], 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
        elif MD_FENCE.match(line):
            fence, ind = MD_FENCE.match(line).group(1), len(line) - len(line.lstrip())
            body, i = [], i + 1
            while i < len(lines) and not lines[i].strip().startswith(fence):
                body.append(lines[i][ind:] if lines[i][:ind].strip() == "" else lines[i])
                i += 1
            out.append(f'<pre><code>{esc(chr(10).join(body))}</code></pre>')
            i += 1
        elif re.match(r"#{1,6} ", line):
            lvl, title = len(line.split(" ")[0]), line.split(" ", 1)[1].strip()
            h = min(6, lvl + shift)
            out.append(f'<h{h} id="{md_slug(title, seen)}">{md_inline(title)}</h{h}>')
            i += 1
        elif re.fullmatch(r"-{3,}", line.strip()):
            out.append("<hr>")
            i += 1
        elif line.startswith(">"):
            body = []
            while i < len(lines) and lines[i].startswith(">"):
                body.append(re.sub(r"^> ?", "", lines[i]))
                i += 1
            out.append(f"<blockquote>{md_render(chr(10).join(body), seen, shift)}</blockquote>")
        elif line.startswith("|") and i + 1 < len(lines) and re.match(r"\|[\s:|-]+\|$", lines[i + 1].strip()):
            cells = lambda ln: [c.strip() for c in re.split(r"(?<!\\)\|", ln.strip()[1:-1])]
            head, rows, i = cells(line), [], i + 2
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(cells(lines[i]))
                i += 1
            th = "".join(f"<th>{md_inline(c)}</th>" for c in head)
            tb = "".join("<tr>" + "".join(f"<td>{md_inline(c.replace(chr(92) + '|', '|'))}</td>" for c in r) + "</tr>" for r in rows)
            out.append(f'<div class="scroll"><table><thead><tr>{th}</tr></thead><tbody>{tb}</tbody></table></div>')
        elif MD_MARKER.match(line):
            m = MD_MARKER.match(line)
            ordered, base = m.group(2)[0].isdigit(), len(m.group(1))
            items = []
            while i < len(lines):
                m = MD_MARKER.match(lines[i])
                if not m or len(m.group(1)) != base or m.group(2)[0].isdigit() != ordered:
                    break
                ci, body, i = len(m.group(0)), [lines[i][len(m.group(0)):]], i + 1
                while i < len(lines):
                    ln = lines[i]
                    if not ln.strip():
                        j = i
                        while j < len(lines) and not lines[j].strip():
                            j += 1
                        if j < len(lines) and len(lines[j]) - len(lines[j].lstrip()) >= ci:
                            body += [""] * (j - i)
                            i = j
                            continue
                        i = j
                        break
                    if len(ln) - len(ln.lstrip()) >= ci:
                        body.append(ln[ci:])
                    elif md_block_start(ln):
                        break
                    else:
                        body.append(ln.strip())
                    i += 1
                items.append(body)
            lis = []
            for body in items:
                box = re.match(r"\[( |x)\] ", body[0])
                if box:
                    body[0] = body[0][4:]
                inner = md_render("\n".join(body), seen, shift)
                if inner.count("<p>") == 1 and inner.startswith("<p>") and inner.endswith("</p>"):
                    inner = inner[3:-4]
                if box:
                    inner = f'<input type="checkbox" disabled{" checked" if box.group(1) == "x" else ""}> {inner}'
                lis.append(f"<li>{inner}</li>")
            out.append(f'<{"ol" if ordered else "ul"}>{"".join(lis)}</{"ol" if ordered else "ul"}>')
        else:
            body, i = [lines[i].strip()], i + 1
            while i < len(lines) and lines[i].strip() and not md_block_start(lines[i]):
                body.append(lines[i].strip())
                i += 1
            out.append(f"<p>{md_inline(' '.join(body))}</p>")
    return "\n".join(out)


def contribute(readme, guide):
    """How to submit, rendered from the repository's own README and CONTRIBUTING."""
    seen = {}
    quick = re.search(r"^## Submit your first test report.*?$(.*?)(?=^## )", readme, re.M | re.S)
    quick = md_render(quick.group(1), seen) if quick else ""
    return f"""<section class="row"><h1>Contribute results</h1></section>
<p class="small muted" style="margin:0;max-width:80ch">Run EVerest on your own hardware or in your own CI? Report each run by pull request and it appears on these pages.
This page is generated from <a href="{REPO_BLOB}README.md">README.md</a> and <a href="{REPO_BLOB}CONTRIBUTING.md">CONTRIBUTING.md</a>; the repository is the source of truth.</p>
<section class="card md"><h2 id="quick-start">Submit your first test report in 5 steps</h2>{quick}</section>
<section class="card md">{md_render(guide, seen)}</section>"""


def build_site(recs, declared, logs, allow, vocab, grace_days=GRACE_DAYS, docs=None):
    """Every output file of the site, as {relative path: text}. `docs` holds README.md, CONTRIBUTING.md and LOGO_SRC."""
    VOCAB_TAGS.clear()
    for g, vals in vocab.get("testTags", {}).items():
        if g not in ("lifecycle", "escapeHatch") and isinstance(vals, list):
            VOCAB_TAGS.update(vals)
    CARRIERS.clear()
    CARRIERS.update(vocab.get("featureCarriers") or {})
    names = list(declared) or [DEFAULT_POINTER]
    default = DEFAULT_POINTER if DEFAULT_POINTER in names else names[0]
    # A record filed under an undeclared pointer is still linked, from the default pointer.
    for r in recs:
        if r["pointer"] not in names:
            r["pointer"] = default
    models = {p: build_pointer(p, logs.get(p, []), recs, allow, grace_days) for p in names}
    meta = {p: pointer_meta(p, declared.get(p)) for p in names}
    for p, P in models.items():
        P.update(meta[p])
    out = {}
    for p, P in models.items():
        others = [models[o] for o in names if o != p]
        out[pointer_file("index", p)] = page(f"Himalayas · {P['label']}", "overview", overview(P, names),
                                             pointer_sel=pointer_select("index", p, names, meta))
        out[pointer_file("detailed", p)] = page(f"Himalayas detailed · {P['label']}", "detailed", detailed(P, others),
                                                pointer_sel=pointer_select("detailed", p, names, meta))
        out[pointer_file("coverage", p)] = page(f"Himalayas coverage · {P['label']}", "coverage", coverage(P, vocab),
                                                pointer_sel=pointer_select("coverage", p, names, meta))
        for m in P["setups"]:
            out[m["href"]] = page(f"{m['integ_name']} / {m['config']}", "", setup_page(m, P), depth=1)
    summary = {
        "version": 2, "generated": f"{NOW:%Y-%m-%d %H:%M} UTC", "selfReported": True, "graceDays": grace_days,
        "pointers": {p: {
            "tracks": (declared.get(p) or {}).get("tracks"), "tip": M["tip"],
            "unattributed": [r["path"] for r in M["unattributed"]],
            "setups": [{
                "integrator": m["integ"], "displayName": m["latest"]["display"], "configuration": m["config"],
                "verdict": m["verdict"], "passRate": m["rate"], "commit": m["latest"]["commit"], "behind": m["behind"],
                "freshness": m["fresh"], "environment": m["latest"]["env"], "testedAt": m["latest"]["start"],
                "record": m["latest"]["path"], "submissions": len(m["runs"]),
                "counts": {k: dict(v) for k, v in m["counts"].items()},
                "coverage": {tag: setup_states(m, lambda t, tag=tag: tag in t["tags"])[0]
                             for _, caps in CAP_GROUPS for tag, _ in caps
                             if setup_states(m, lambda t, tag=tag: tag in t["tags"])[0]},
                "measurementsFailed": sum(1 for t in m["latest"]["tests"] for x in t["measurements"]
                                          if x["outcome"] not in ("PASS", "")),
            } for m in M["setups"]]} for p, M in models.items()},
    }
    regs = {"version": 2, "pointers": {p: [
        {"integrator": m["integ"], "configuration": m["config"], "test": r["name"], "good": r["range"][0],
         "bad": r["range"][1], "untested": r["untested"], "confirmed": r["confirmed"]}
        for m in M["setups"] for r in m["regs"]] for p, M in models.items()}}
    if docs:
        out[LOGO] = reversed_logo(docs[LOGO_SRC])
        out["contribute.html"] = page("Himalayas · contribute", "contribute", contribute(docs["README.md"], docs["CONTRIBUTING.md"]))
    for a in allow:
        if a.get("logo"):
            out[f"logos/{a['id']}.svg"] = a["logo"]
    out["summary.json"] = json.dumps(summary, indent=1) + "\n"
    out["regressions.json"] = json.dumps(regs, indent=1) + "\n"
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--grace-days", type=int, default=GRACE_DAYS)
    ap.add_argument("--out-dir", default=str(ROOT / "status"))
    args = ap.parse_args()
    declared, logs = load_pointers()
    vocab = json.loads((ROOT / "schema" / "vocabulary.json").read_text())
    recs = load_records()
    docs = {n: (ROOT / n).read_text() for n in ("README.md", "CONTRIBUTING.md", LOGO_SRC)}
    allow = load_allowlist()
    for rel, problem in logo_audit(ROOT, allow):
        print(f"warning: {rel} {problem}; not shown", file=sys.stderr)
    files = build_site(recs, declared, logs, allow, vocab, args.grace_days, docs)
    out = pathlib.Path(args.out_dir)
    for rel, text in files.items():
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_text(text)
    print(f"wrote {out}: {len(recs)} submission(s), {len(files)} file(s) across {len(declared)} pointer(s)")


if __name__ == "__main__":
    sys.exit(main())
