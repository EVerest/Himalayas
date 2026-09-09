#!/usr/bin/env python3
"""Build status/summary.json and status/index.html from results/.

Reads submissions in either format, normalises to OpenHTF, and groups by
POINTER first, then integrator - because the page has to answer "what is the
state of main?" and "what is the state of the stable line?" separately.

The README carries no badge, so this page is the only place the self-reported
caveat and the staleness date appear. It is load-bearing for honesty, not just
presentation: the caveat leads, and every row carries its own date.

Staleness: a result is stale once the pointer has MOVED OFF the tested
commit AND the result is older than --grace-days. Both conditions, same shape as
the tag-based rule it replaces. Expressed in days rather than a move count so it
stays correct if the daily cadence changes.

Honesty rules the rendering follows. Each is a choice in the code here, and the
`build` suite in scripts/selftest.py asserts it against the rendered page - a
comment is not an enforcement mechanism, which is why the tests exist:

- Verdict and freshness are ORTHOGONAL and get separate slots. A stale result
  that is also failing must show both; one must never suppress the other.
- Freshness is never absent. When it cannot be computed the page says so, in
  the header and on every card, because a missing "stale" marker otherwise
  reads as "fresh".
- A protocol tag is only claimed as covered when a phase carrying it PASSED.
  A tag whose only phase failed or was skipped is rendered as such. Rolling
  every tag up regardless of outcome claims coverage that was not earned.
- Pass colour is reserved for outcomes. The source-format badge is a neutral
  fact about the emitter, not a quality signal, so it never uses --ok.

The last rule is the one the suite does not cover: it is a stylesheet fact, and
asserting a CSS class name would pin the markup rather than the meaning.
"""
import argparse, datetime, glob, html, json, os, sys, urllib.request, urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from normalise import detect, ctrf_to_openhtf                  # noqa: E402

PHASE_OK = {"PASS"}
PHASE_BAD = {"FAIL", "ERROR", "TIMEOUT", "ABORTED"}

TAG_WHY = {
    "passed": "A phase carrying this tag passed.",
    "failed": "Every phase carrying this tag failed - this is not coverage.",
    "notrun": "Every phase carrying this tag was skipped or did not run - "
              "this protocol was NOT exercised.",
}


def tag_states(phases):
    """Per-tag coverage, the weakest claim the phase outcomes support.

    A tag counts as 'passed' only if some phase carrying it passed. Otherwise
    'failed' if some phase carrying it failed, else 'notrun'. Rolling tags up
    without looking at the outcome claims coverage the run did not earn - the
    sample data carries ISO15118-20 and TLS on a SKIPped phase only.
    """
    state = {}
    for ph in phases:
        out = ph.get("outcome")
        rank = 2 if out in PHASE_OK else 1 if out in PHASE_BAD else 0
        for t in (ph.get("tags") or []):
            state[t] = max(state.get(t, -1), rank)
    return {t: ("passed" if r == 2 else "failed" if r == 1 else "notrun")
            for t, r in state.items()}


def load_results():
    out = []
    for p in sorted(glob.glob(os.path.join(ROOT, "results", "*", "*", "*", "*.json"))):
        try:
            with open(p) as f:
                doc = json.load(f)
        except Exception:                                      # noqa: BLE001
            continue
        fmt = detect(doc)
        if fmt is None:
            continue
        rec = doc if fmt == "openhtf" else ctrf_to_openhtf(doc)
        md = rec.get("metadata") or {}
        ev = md.get("everest") or {}
        integ = md.get("integrator") or {}
        build = md.get("build") or {}
        phases = rec.get("phases") or []
        meas = []
        for ph in phases:
            for key, mv in (ph.get("measurements") or {}).items():
                meas.append({
                    "phase": ph.get("name"), "name": mv.get("name") or key,
                    "value": mv.get("measured_value"), "units": mv.get("units"),
                    "outcome": mv.get("outcome"),
                    "limits": ", ".join(str(x) for x in (mv.get("validators") or [])),
                })
        targets = build.get("targets") or []
        out.append({
            "file": os.path.relpath(p, ROOT).replace(os.sep, "/"),
            "sourceFormat": fmt,
            "pointer": ev.get("pointer") or "unknown",
            "tracks": ev.get("tracks"),
            "commit": ev.get("commit"),
            "environmentKind": ev.get("testEnvironment") or "unknown",
            "buildUrl": ev.get("buildUrl"),
            "integrator": integ.get("id", "unknown"),
            "displayName": integ.get("displayName"),
            "dutId": rec.get("dut_id"),
            "stationId": rec.get("station_id"),
            "outcome": rec.get("outcome"),
            "build": build,
            "modules": build.get("modules") or [],
            "targetsTotal": len(targets),
            "targetsFailed": sum(1 for t in targets if t.get("status") != "passed"),
            "hardware": md.get("hardware") or {},
            "phaseTotal": len(phases),
            "phasePassed": sum(1 for p_ in phases if p_.get("outcome") in PHASE_OK),
            "phaseFailed": sum(1 for p_ in phases if p_.get("outcome") in PHASE_BAD),
            "phaseSkipped": sum(1 for p_ in phases
                                if p_.get("outcome") not in PHASE_OK
                                and p_.get("outcome") not in PHASE_BAD),
            "measFailed": sum(1 for m in meas if m["outcome"] != "PASS"),
            "tagStates": tag_states(phases),
            "measurements": meas,
            "ranAt": rec.get("end_time_millis") or rec.get("start_time_millis") or 0,
        })
    return out


def pointer_tips(pointers, repo, offline):
    """Current tip of each pointer branch - the 'has it moved' half of staleness."""
    tips = {}
    if offline:
        return tips
    tok = os.environ.get("GITHUB_TOKEN")
    for name in pointers:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/branches/{name.replace('/', '%2F')}",
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "everest-himalayas"})
        if tok:
            req.add_header("Authorization", f"Bearer {tok}")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                tips[name] = json.load(r)["commit"]["sha"]
        except Exception:                                      # noqa: BLE001
            tips[name] = None
    return tips


def age_days(ms, now_ms=None):
    if not ms:
        return None
    now = now_ms if now_ms is not None else (
        datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)
    return (now - ms) / 86_400_000.0


def is_stale(row, tips, grace_days, now_ms=None):
    """Stale iff the pointer moved off this commit AND the result is past the grace."""
    tip = tips.get(row["pointer"])
    if not tip or not row["commit"]:
        return None
    if tip == row["commit"]:
        return False
    age = age_days(row["ranAt"], now_ms)
    if age is None:
        return None
    return age > grace_days


def stale_reason(row, tips):
    """Why freshness is unknown, in the reader's terms. '' when it is known."""
    if not tips.get(row["pointer"]):
        return "the pointer branch was not found, so its tip is unknown"
    if not row["commit"]:
        return "the submission names no commit"
    if not row["ranAt"]:
        return "the submission carries no run timestamp"
    return ""


def fmt_ms(ms):
    if not ms:
        return "unknown"
    return datetime.datetime.fromtimestamp(
        ms / 1000, datetime.timezone.utc).strftime("%Y-%m-%d")


def slug(name):
    """Stable DOM id for a pointer branch name, which contains a slash."""
    return "".join(c if c.isalnum() else "-" for c in name)


def fmt_ms_min(ms):
    """Date and time. The pointers move daily and the filename suffix allows
    several runs in one day, so a date alone cannot tell two rows apart."""
    if not ms:
        return "unknown"
    return datetime.datetime.fromtimestamp(
        ms / 1000, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")


def render(groups, tips, grace_days, generated, declared):
    def esc(x):
        return html.escape(str(x if x is not None else ""))

    now_ms = datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000

    def freshness(row):
        """(css class, label, long explanation) - never empty, by construction.

        A missing marker reads as 'fresh', so the unknown case is a rendered
        state rather than the absence of one.
        """
        st = is_stale(row, tips, grace_days)
        age = age_days(row["ranAt"], now_ms)
        aged = (f"{age:.0f} days old" if age is not None and age >= 0
                else "dated in the future")
        if st is None:
            return ("unknown", "freshness unknown",
                    "Freshness could not be evaluated: " + stale_reason(row, tips)
                    + ". This entry is not known to be current.")
        if st:
            return ("stale", f"stale &middot; ran {esc(fmt_ms(row['ranAt']))}",
                    f"The pointer has moved off the commit this tested and the result is "
                    f"{aged}, past the {grace_days}-day grace.")
        return ("fresh", f"current &middot; ran {esc(fmt_ms(row['ranAt']))}",
                f"Within the {grace_days}-day grace, or the pointer still points at the "
                f"commit this tested.")

    blocks = []
    unknown_tips = []
    for ptr in sorted(groups):
        rows = groups[ptr]
        per = {}
        for r in rows:
            per.setdefault(r["integrator"], []).append(r)
        for v in per.values():
            v.sort(key=lambda r: (r["ranAt"], r["file"]), reverse=True)

        tip = tips.get(ptr)
        if not tip:
            unknown_tips.append(ptr)
        tracks = (declared.get(ptr) or {}).get("tracks")
        cards = []
        for who in sorted(per):
            rs = per[who]
            latest = rs[0]
            name = latest["displayName"] or f"{who} (anonymous)"
            fcls, flabel, fwhy = freshness(latest)
            bstat = (latest["build"] or {}).get("status", "unknown")

            # Verdict and freshness get separate slots on purpose. The single-pill
            # chain this replaces let 'stale' suppress 'N of M failing', which are
            # orthogonal facts about the same entry. Do not collapse them again.
            if bstat != "passed":
                vcls, vtext = "bad", "build failed"
            elif latest["phaseFailed"]:
                vcls = "bad"
                vtext = f'{latest["phaseFailed"]} of {latest["phaseTotal"]} phases failed'
            elif latest["phaseTotal"] == 0:
                vcls, vtext = "none", "build only &middot; no tests reported"
            else:
                vcls = "good"
                vtext = f'{latest["phasePassed"]} of {latest["phaseTotal"]} phases passed'
            vpill = f'<span class="pill {vcls}">{vtext}</span>'
            fpill = f'<span class="pill {fcls}" title="{esc(fwhy)}">{flabel}</span>'

            # Things a headline verdict would otherwise swallow.
            notes = []
            if latest["phaseSkipped"]:
                notes.append(f'{latest["phaseSkipped"]} phase(s) did not run')
            if latest["targetsFailed"]:
                notes.append(f'{latest["targetsFailed"]} of {latest["targetsTotal"]} '
                             f'build targets failed')
            if latest["measFailed"]:
                notes.append(f'{latest["measFailed"]} measurement(s) outside limits')
            if latest["ranAt"] and latest["ranAt"] > now_ms:
                notes.append("run timestamp is in the future")
            notebar = ('<p class="notes">' + " &middot; ".join(esc(n) for n in notes)
                       + "</p>" if notes else "")

            tgt = "".join(
                f'<span class="tgt {"ok" if t.get("status")=="passed" else "no"}">'
                f'{esc(t.get("target"))}</span>'
                for t in ((latest["build"] or {}).get("targets") or []))
            # A record-level "passed" with a failed target inside is the whole
            # reason build.targets is plural, so state it next to the word.
            if latest["targetsFailed"]:
                bqual = (f' <span class="qual">&mdash; {latest["targetsFailed"]} of '
                         f'{latest["targetsTotal"]} targets failed</span>')
            elif latest["targetsTotal"]:
                bqual = f' <span class="dim">&mdash; {latest["targetsTotal"]} target(s)</span>'
            else:
                bqual = ' <span class="dim">&mdash; no target detail reported</span>'

            hw = latest["hardware"] or {}
            hwline = " &middot; ".join(esc(x) for x in
                                       [hw.get("board"), hw.get("bsp"),
                                        hw.get("chargeController")] if x)
            tstates = latest["tagStates"]
            ptags = "".join(
                f'<span class="ptag {tstates[t]}" title="{esc(TAG_WHY[tstates[t]])}">'
                f'{esc(t)}</span>' for t in sorted(tstates))
            link = (f'<a href="{esc(latest["buildUrl"])}">run log</a>'
                    if latest["buildUrl"]
                    else '<span class="dim" title="buildUrl is optional, so this entry '
                         'carries no cheap way for a reader to check it">no run link</span>')
            fmtbadge = ('<span class="fmt htf" title="Submitted as OpenHTF, which carries '
                        'measurements and their limits natively">OpenHTF</span>'
                        if latest["sourceFormat"] == "openhtf"
                        else '<span class="fmt ctrf" title="Submitted as CTRF and converted. '
                             'CTRF has no measurement concept, so this entry has no '
                             'measurements by construction - they are not missing.">'
                             'CTRF &rarr; OpenHTF</span>')

            mods = "".join(f'<span class="mod">{esc(m)}</span>'
                           for m in (latest["modules"] or []))

            if latest["measurements"]:
                mrows = "".join(
                    f'<tr><td>{esc(m["phase"])}</td><td>{esc(m["name"])}</td>'
                    f'<td class="num">{esc(m["value"])}'
                    f'{" " + esc(m["units"]) if m["units"] else ""}</td>'
                    f'<td class="lim">{esc(m["limits"])}</td>'
                    f'<td><span class="mo {"ok" if m["outcome"]=="PASS" else "no"}">'
                    f'{esc(m["outcome"])}</span></td></tr>'
                    for m in latest["measurements"])
                head = (f'{len(latest["measurements"])} measurement(s)'
                        + (f' &middot; <b class="badtxt">{latest["measFailed"]} outside '
                           f'limits</b>' if latest["measFailed"] else ''))
                meas_block = (
                    f'<details open><summary>{head}</summary>'
                    '<div class="tscroll"><table class="meas"><thead><tr><th>phase</th>'
                    '<th>measurement</th><th>value</th><th>limit</th><th>verdict</th>'
                    f'</tr></thead><tbody>{mrows}</tbody></table></div></details>')
            else:
                # Absence of measurements is a property of the source format, not
                # of the run. Say which, so it does not read as missing data.
                why = ("CTRF carries no measurement concept, so this entry has none by "
                       "construction." if latest["sourceFormat"] != "openhtf"
                       else "This OpenHTF record reported no measurements.")
                meas_block = f'<p class="nomeas">No measurements &mdash; {esc(why)}</p>'

            hist = "".join(
                f'<tr><td class="nw">{esc(fmt_ms_min(r["ranAt"]))}'
                f' <span class="dim">UTC</span></td>'
                f'<td>{esc(r["environmentKind"])}</td>'
                f'<td><code>{esc((r["commit"] or "")[:12])}</code></td>'
                f'<td>{esc((r["build"] or {}).get("status","?"))}'
                + (f' <span class="badtxt">({r["targetsFailed"]} of {r["targetsTotal"]} '
                   f'targets failed)</span>' if r["targetsFailed"]
                   else (f' <span class="dim">({r["targetsTotal"]} tgt)</span>'
                         if r["targetsTotal"] else ''))
                + '</td>'
                f'<td class="pfs"><span class="ok-inline">{r["phasePassed"]}</span>/'
                f'<span class="{"badtxt" if r["phaseFailed"] else "dim"}">'
                f'{r["phaseFailed"]}</span>/'
                f'<span class="dim">{r["phaseSkipped"]}</span></td>'
                f'<td><a href="{esc(r["file"])}">json</a></td></tr>' for r in rs)

            moved = ("<span class='dim'>&mdash; pointer has moved on</span>"
                     if tip and latest["commit"] and tip != latest["commit"]
                     else "<span class='ok-inline'>&mdash; current pointer tip</span>"
                     if tip and latest["commit"] == tip
                     else "<span class='dim'>&mdash; pointer tip unknown</span>")

            cards.append(f"""
      <article class="card v-{vcls} f-{fcls}">
        <header><h3>{esc(name)}</h3><div class="chips">{vpill}{fpill}</div></header>
        {notebar}
        <dl>
          <dt>commit</dt><dd><code>{esc((latest["commit"] or "")[:12])}</code> {moved}</dd>
          <dt>environment</dt><dd>{esc(latest["environmentKind"])}{" &middot; " + hwline if hwline else ""}</dd>
          <dt>device</dt><dd><code>{esc(latest["dutId"])}</code> <span class="dim">on</span> <code>{esc(latest["stationId"])}</code></dd>
          <dt>build</dt><dd>{esc(bstat)}{bqual}<div class="tgts">{tgt}</div></dd>
          {"<dt>modules</dt><dd class='mods'>" + mods + "</dd>" if mods else ""}
          <dt>ran</dt><dd>{esc(fmt_ms(latest["ranAt"]))} &middot; {link} &middot; {fmtbadge}</dd>
          {"<dt>covers</dt><dd>" + ptags + "</dd>" if ptags else ""}
        </dl>
        {meas_block}
        <details><summary>{len(rs)} submission(s) &middot; each with its own date</summary>
          <div class="tscroll"><table><thead><tr><th>ran (UTC)</th><th>env</th><th>commit</th>
          <th>build</th><th title="passed / failed / did not run">pass/fail/skip</th>
          <th></th></tr></thead>
          <tbody>{hist}</tbody></table></div>
        </details>
      </article>""")

        tipline = (f'current tip <code>{esc(tip[:12])}</code>' if tip
                   else '<span class="warntxt">tip unknown &mdash; the pointer branch was '
                        'not found, so nothing below can be aged</span>')
        blocks.append((ptr,
                       f'{len(per)} integrator(s) &middot; {len(rows)} submission(s)',
                       True, f"""
  <section class="pointer" id="p-{slug(ptr)}" data-pointer="{esc(ptr)}">
    <div class="phead">
      <h2><code>{esc(ptr)}</code></h2>
      <span class="ptracks">{"tracks <code>" + esc(tracks) + "</code>" if tracks else ""}</span>
      <span class="ptip">{tipline}</span>
      <span class="pcount">{len(per)} integrator(s) &middot; {len(rows)} submission(s)</span>
      <span class="psr" title="Every entry in this section was produced and reported by the integrator named on it. EVerest does not run, reproduce or verify it.">self-reported</span>
    </div>
    <div class="grid">{"".join(cards)}</div>
  </section>"""))

    # Declared pointers with no submissions are shown, not omitted: "nobody has
    # tested the stable line" is itself an answer. Render it as an answer.
    unknown = [p for p in declared if p not in groups]
    for p in sorted(unknown):
        etip = (f'current tip <code>{esc((tips.get(p) or "")[:12])}</code>' if tips.get(p)
                else '<span class="warntxt">tip unknown &mdash; the pointer branch was '
                     'not found</span>')
        blocks.append((p, 'no results reported', False,
            f'<section class="pointer" id="p-{slug(p)}" data-pointer="{esc(p)}">'
            f'<div class="phead"><h2><code>{esc(p)}</code></h2>'
            f'<span class="ptracks">tracks '
            f'<code>{esc((declared.get(p) or {}).get("tracks"))}</code></span>'
            f'<span class="ptip">{etip}</span>'
            f'<span class="pcount">0 integrator(s) &middot; 0 submission(s)</span></div>'
            f'<div class="grid"><article class="card empty">'
            f'<header><h3>No results</h3><div class="chips">'
            f'<span class="pill none">nobody has reported against this pointer</span>'
            f'</div></header>'
            f'<p>This pointer is declared in <code>pointers.json</code> and is listed here '
            f'deliberately. <b>No integrator has submitted a result for it.</b> That is '
            f'neither a pass nor a failure: it means this line is currently untested by '
            f'anyone reporting to Himalayas.</p></article></div></section>'))

    # One pointer branch is shown at a time, chosen from a menu. That
    # collides with the requirement that a declared pointer with no
    # submissions is SHOWN rather than omitted - a menu would hide exactly that
    # answer. So the menu itself carries each pointer's count, and 'no results
    # reported' is legible in it without opening any section. All sections are
    # rendered and the script only hides the unselected ones, so with JavaScript
    # off the page degrades to the full list rather than to one branch.
    order = ([b for b in blocks if b[0] in declared]
             + [b for b in blocks if b[0] not in declared])
    sections = "".join(b[3] for b in order)
    # Every DECLARED pointer with no readable tip, not only the ones carrying
    # submissions - otherwise an empty pointer's unknown tip goes unmentioned.
    unknown_tips = [pt for pt in declared if not tips.get(pt)] or unknown_tips
    if len(order) > 1:
        items = "".join(
            f'<li role="none"><button type="button" role="menuitemradio" '
            f'aria-checked="{str(i == 0).lower()}" data-target="p-{slug(ptr)}" '
            f'data-pointer="{esc(ptr)}"><code>{esc(ptr)}</code>'
            f'<span class="{"pn-has" if has else "pn-none"}">{summ}</span>'
            f'</button></li>'
            for i, (ptr, summ, has, _) in enumerate(order))
        nav = (
            '<nav class="ptrnav" aria-label="Pointer branch">'
            '<button type="button" id="pn-btn" class="pn-btn" aria-expanded="false"'
            ' aria-haspopup="true" aria-controls="pn-menu">'
            '<span class="pn-bars" aria-hidden="true"><i></i><i></i><i></i></span>'
            '<span class="pn-lbl">showing <code id="pn-cur">'
            + esc(order[0][0])
            + '</code></span><span class="pn-caret" aria-hidden="true">&#9662;</span>'
            '</button>'
            f'<ul id="pn-menu" class="pn-menu" role="menu" hidden>{items}</ul>'
            '<noscript><span class="pn-ns">JavaScript is off, so every pointer '
            'branch is listed below.</span></noscript>'
            '</nav>')
    else:
        nav = ""

    if unknown_tips:
        fresh_note = (
            '<div class="banner">'
            '<p><b>Freshness cannot be evaluated right now.</b> The pointer '
            + ("branches " if len(unknown_tips) > 1 else "branch ")
            + ", ".join(f"<code>{esc(p)}</code>" for p in unknown_tips)
            + (" were" if len(unknown_tips) > 1 else " was")
            + ' not found in <a href="https://github.com/EVerest/EVerest">EVerest</a>, '
            'so no entry below can be compared against a current tip. '
            '<b>The absence of a &ldquo;stale&rdquo; marker below therefore does not mean a '
            'result is current.</b> Every entry reads <i>freshness unknown</i> instead.</p>'
            '</div>')
    else:
        fresh_note = ""

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Himalayas &middot; EVerest federated testing status</title>
<style>
:root{{--green:#00CE7C;--blue:#2F96D0;--bg:#f6f8fc;--surface:#fff;--ink:#0C2351;
--ink2:#44577a;--ink3:#74849f;--line:#dae2ee;--warn:#9a5b06;--warnbg:#fdf2dc;
--warnline:#e8c584;--bad:#a71d1d;--badbg:#fdeaea;--badline:#eeb4b4;--ok:#04724d;
--okbg:#dcf5ea;--okline:#93ddc0;--info:#1c5d85;--infobg:#e4f1fa;--infoline:#9fcde9}}
@media(prefers-color-scheme:dark){{:root{{--bg:#080f1d;--surface:#0e1830;--ink:#e8eef8;
--ink2:#a9b8d0;--ink3:#7e8fab;--line:#22314f;--warn:#f0c076;--warnbg:#2e2410;
--warnline:#5b4718;--bad:#f0a1a1;--badbg:#2e1414;--badline:#5e2626;--ok:#7ce0b4;
--okbg:#0d2a20;--okline:#1d523c;--info:#8ec8ea;--infobg:#0b2233;--infoline:#1d4460}}}}
*{{box-sizing:border-box}}
body{{margin:0;padding:0 20px 60px;background:var(--bg);color:var(--ink);
font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif}}
.wrap{{max-width:1080px;margin:0 auto}}
header.top{{padding:40px 0 16px}}
h1{{margin:0;font-size:clamp(26px,4vw,38px);letter-spacing:-.02em}}
.sub{{color:var(--ink2);margin-top:10px;max-width:70ch}}
.caveat{{background:var(--warnbg);border:1px solid var(--warnline);
border-left:5px solid var(--warn);border-radius:10px;padding:18px 20px;margin:22px 0}}
.caveat .lead{{font-size:clamp(17px,2.5vw,22px);line-height:1.35;font-weight:750;
color:var(--warn);margin:0 0 10px;letter-spacing:-.01em;max-width:52ch}}
.caveat p{{margin:0 0 8px;max-width:78ch}} .caveat p:last-child{{margin:0}}
.caveat b{{color:var(--warn)}}
.banner{{background:var(--warnbg);border:1px solid var(--warnline);border-radius:10px;
padding:12px 16px;margin:0 0 4px;font-size:13.5px}}
.banner p{{margin:0;max-width:82ch}}
.meta{{display:flex;gap:20px;flex-wrap:wrap;color:var(--ink3);font-size:13px;margin:14px 0 0}}
.ptrnav{{position:relative;margin:20px 0 0}}
.pn-btn{{display:inline-flex;align-items:center;gap:10px;font:inherit;font-size:13.5px;
background:var(--surface);color:var(--ink);border:1px solid var(--line);border-radius:9px;
padding:8px 13px;cursor:pointer}}
.pn-btn:hover{{border-color:var(--ink3)}}
.pn-bars{{display:grid;gap:3px;width:15px}}
.pn-bars i{{display:block;height:2px;background:var(--ink2);border-radius:2px}}
.pn-lbl{{color:var(--ink2)}} .pn-lbl code{{color:var(--ink);font-weight:700}}
.pn-caret{{color:var(--ink3);font-size:11px}}
.pn-btn[aria-expanded="true"] .pn-caret{{transform:rotate(180deg)}}
.pn-menu{{list-style:none;margin:6px 0 0;padding:6px;position:absolute;z-index:20;
min-width:min(340px,92vw);background:var(--surface);border:1px solid var(--line);
border-radius:10px;box-shadow:0 10px 28px rgba(12,35,81,.14)}}
.pn-menu[hidden]{{display:none}}
.pn-menu button{{display:flex;flex-direction:column;gap:2px;width:100%;text-align:left;
font:inherit;font-size:13.5px;background:none;border:0;border-radius:7px;padding:8px 10px;
cursor:pointer;color:var(--ink)}}
.pn-menu button:hover{{background:var(--bg)}}
.pn-menu button[aria-checked="true"]{{background:var(--infobg);color:var(--info)}}
.pn-menu button code{{font-weight:700;background:none;border:0;padding:0}}
.pn-has{{font-size:11.5px;color:var(--ink3)}}
.pn-none{{font-size:11.5px;color:var(--warn);font-weight:650}}
.pn-ns{{font-size:12.5px;color:var(--ink3);margin-left:12px}}
section.pointer{{margin-top:34px}}
section.pointer[hidden]{{display:none}}
.phead{{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;padding-bottom:10px;
border-bottom:2px solid var(--line);margin-bottom:16px}}
.phead h2{{margin:0;font-size:19px}}
.phead h2 code{{font-size:.9em}}
.ptracks,.ptip,.pcount{{font-size:12.5px;color:var(--ink3)}}
.psr{{margin-left:auto;font-size:10.5px;font-weight:700;letter-spacing:.05em;
text-transform:uppercase;color:var(--warn);background:var(--warnbg);
border:1px solid var(--warnline);border-radius:999px;padding:2px 9px;white-space:nowrap}}
.warntxt{{color:var(--warn);font-weight:650}}
.badtxt{{color:var(--bad);font-weight:650}}
.grid{{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(min(400px,100%),1fr))}}
.grid>*{{min-width:0}}
.card{{background:var(--surface);border:1px solid var(--line);border-radius:10px;
padding:18px 20px;min-width:0;align-self:start}}
.card.v-bad{{border-left:5px solid var(--bad)}}
.card.v-good{{border-left:5px solid var(--okline)}}
.card.v-none{{border-left:5px solid var(--line)}}
/* Stale gets the hatch and the drained greens; UNKNOWN deliberately does not.
   Banner and chip are for unknown only. Unknown is not the same as bad, and
   a page that looks drained for weeks teaches readers to ignore the signal. */
.card.f-stale{{border-top:1px solid var(--warnline);
border-right:1px solid var(--warnline);border-bottom:1px solid var(--warnline);
background:repeating-linear-gradient(135deg,var(--surface),
var(--surface) 10px,var(--warnbg) 10px,var(--warnbg) 20px)}}
.card.f-stale .mo.ok,.card.f-stale .tgt.ok,.card.f-stale .ptag.passed,
.card.f-stale .ok-inline{{filter:saturate(.2)}}
.card.empty{{border-left:5px solid var(--line);color:var(--ink2)}}
.card.empty p{{margin:0;font-size:13.5px;max-width:62ch}}
.card header{{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;
flex-wrap:wrap;margin-bottom:10px}}
.card h3{{margin:0;font-size:16.5px;min-width:0;overflow-wrap:anywhere}}
.chips{{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end;min-width:0}}
.pill{{font-size:11.5px;font-weight:700;padding:3px 9px;border-radius:999px;
max-width:100%;overflow-wrap:anywhere}}
.pill.good{{background:var(--okbg);color:var(--ok);border:1px solid var(--okline)}}
.pill.bad{{background:var(--badbg);color:var(--bad);border:1px solid var(--badline)}}
.pill.none{{background:transparent;color:var(--ink3);border:1px dashed var(--ink3)}}
.pill.fresh{{background:var(--infobg);color:var(--info);border:1px solid var(--infoline)}}
.pill.stale{{background:var(--warnbg);color:var(--warn);border:1px solid var(--warn)}}
.pill.unknown{{background:var(--warnbg);color:var(--warn);border:1px dashed var(--warn)}}
.notes{{margin:0 0 10px;font-size:12.5px;color:var(--bad);font-weight:600;
max-width:100%;overflow-wrap:anywhere}}
dl{{display:grid;grid-template-columns:auto minmax(0,1fr);gap:5px 14px;margin:0;
font-size:13.5px}}
dt{{color:var(--ink3);font-weight:650}} dd{{margin:0;min-width:0;overflow-wrap:anywhere}}
code{{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.88em;
overflow-wrap:anywhere}}
.dim{{color:var(--ink3)}} .ok-inline{{color:var(--ok);font-weight:650}}
.qual{{color:var(--bad);font-weight:650}}
.tgts{{margin-top:2px}}
.tgt{{display:inline-block;font-size:11px;padding:1px 6px;border-radius:4px;
margin:2px 3px 0 0;font-family:ui-monospace,Menlo,monospace}}
.tgt.ok{{background:var(--okbg);color:var(--ok)}}
.tgt.no{{background:var(--badbg);color:var(--bad);border:1px solid var(--badline);
font-weight:700}}
.ptag{{display:inline-block;font-size:11px;padding:1px 7px;border-radius:999px;
margin:2px 3px 0 0}}
.ptag.passed{{background:var(--okbg);border:1px solid var(--okline);color:var(--ok)}}
.ptag.failed{{background:var(--badbg);border:1px solid var(--badline);color:var(--bad)}}
.ptag.notrun{{background:transparent;border:1px dashed var(--ink3);color:var(--ink3);
text-decoration:line-through}}
.mods .mod{{display:inline-block;font-size:11px;padding:1px 6px;border-radius:4px;
margin:2px 3px 0 0;background:var(--bg);border:1px solid var(--line);color:var(--ink2);
font-family:ui-monospace,Menlo,monospace}}
.fmt{{display:inline-block;font-size:10.5px;font-weight:700;padding:1px 6px;border-radius:4px;
border:1px solid var(--infoline);background:var(--infobg);color:var(--info)}}
.fmt.ctrf{{border-style:dashed}}
.nomeas{{margin:12px 0 0;font-size:12.5px;color:var(--ink3);background:var(--bg);
border:1px dashed var(--line);border-radius:6px;padding:8px 10px;max-width:100%}}
details{{margin-top:12px;font-size:13px}} summary{{cursor:pointer;color:var(--ink2)}}
.tscroll{{overflow-x:auto;max-width:100%}}
table{{width:100%;border-collapse:collapse;margin-top:8px;font-size:12.5px}}
th,td{{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line)}}
th{{color:var(--ink3);font-size:10.5px;text-transform:uppercase;letter-spacing:.06em}}
td.num{{text-align:right;font-family:ui-monospace,Menlo,monospace;white-space:nowrap}}
td.lim{{color:var(--ink3);font-family:ui-monospace,Menlo,monospace;font-size:.95em;
white-space:nowrap}}
td.pfs{{font-family:ui-monospace,Menlo,monospace;white-space:nowrap}}
td.nw{{white-space:nowrap}}
table.meas td:first-child{{color:var(--ink3);font-size:.95em;overflow-wrap:anywhere}}
table.meas{{background:var(--bg);border-radius:6px;min-width:430px}}
.mo{{font-size:10.5px;font-weight:700;padding:1px 6px;border-radius:4px;white-space:nowrap}}
.mo.ok{{background:var(--okbg);color:var(--ok)}}
.mo.no{{background:var(--badbg);color:var(--bad);border:1px solid var(--badline)}}
.legend{{display:flex;gap:14px 20px;flex-wrap:wrap;font-size:12px;color:var(--ink3);
margin:20px 0 0;padding:12px 16px;background:var(--surface);border:1px solid var(--line);
border-radius:8px}}
.legend>span{{display:flex;align-items:center;gap:7px;min-width:0}}
footer{{margin-top:28px;padding-top:18px;border-top:1px solid var(--line);color:var(--ink3);
font-size:12.5px}}
footer p{{max-width:82ch}}
</style></head><body><div class="wrap">
<header class="top">
  <h1>Himalayas</h1>
  <p class="sub"><b>EVerest federated testing.</b> Build and test results reported by EVerest
  integrators, against the floating <code>testing/</code> pointer branches of
  <a href="https://github.com/EVerest/EVerest">EVerest</a>.</p>
</header>

<div class="caveat">
  <p class="lead">Nothing on this page was run or verified by EVerest. Every result below is
  self-reported by the integrator named on it.</p>
  <p>Each entry was produced on the integrator's own hardware and in their own CI.
  Integrators may also report results from tests that are not public, so <b>a passing entry
  does not mean a fixed, comparable suite was run</b> &mdash; and two passing entries are not
  necessarily comparable with each other.</p>
  <p>What EVerest checks on submission: the record validates against the schemas, the commit
  exists and was a recorded tip of the pointer branch, and the reported build passed.
  <b>Nothing else.</b></p>
</div>
{fresh_note}
<div class="meta">
  <span>generated {esc(generated)}</span>
  <span>one pointer branch at a time</span>
  <span>staleness grace {grace_days} days</span>
</div>
{nav}
{sections}
<div class="legend">
  <span><span class="pill good">passed</span> an outcome the integrator reported</span>
  <span><span class="pill none">no results</span> nothing reported &mdash; not a pass</span>
  <span><span class="pill stale">stale</span> too old to read as current</span>
  <span><span class="pill unknown">unknown</span> freshness could not be computed</span>
  <span><span class="ptag notrun">TAG</span> a protocol whose phases did not run</span>
  <span><span class="fmt ctrf">CTRF</span> source format, not a quality rating</span>
</div>
<footer>
  <p>A result is marked <strong>stale</strong> once the pointer branch has moved off the commit
  it tested <em>and</em> the result is more than {grace_days} days old. Both conditions must
  hold: the pointers move daily, so a newer base alone means very little. When either
  condition cannot be evaluated the entry reads <strong>freshness unknown</strong> rather
  than current.</p>
  <p><strong>Coverage tags reflect outcomes.</strong> A protocol is shown as covered only when
  a phase carrying that tag passed; a tag whose phases failed or did not run is marked as
  such, so a skipped handshake is never counted as coverage.</p>
  <p>Submissions are accepted as CTRF or OpenHTF and normalised to OpenHTF, which is why
  measurements appear for some entries and not others &mdash; CTRF has no concept of a
  measurement, so those entries have none by construction rather than by omission.
  Machine-readable summary: <a href="summary.json">summary.json</a>.</p>
  <p><strong>Himalayas</strong> is the name of this programme. A submission is a pull request to
  <a href="https://github.com/EVerest/Himalayas">EVerest/Himalayas</a>.</p>
</footer>
</div>
<script>
/* One pointer branch at a time. Progressive enhancement on purpose:
   every section is in the HTML, so with this script absent the page is the full
   list. The choice lives in location.hash so a link can name a branch - people
   share "the state of main", and localStorage cannot be shared. */
(function () {{
  var nav = document.querySelector(".ptrnav");
  if (!nav) return;
  var btn = document.getElementById("pn-btn");
  var menu = document.getElementById("pn-menu");
  var cur = document.getElementById("pn-cur");
  var items = [].slice.call(menu.querySelectorAll("button"));
  var sections = [].slice.call(document.querySelectorAll("section.pointer"));

  function open(state) {{
    menu.hidden = !state;
    btn.setAttribute("aria-expanded", state ? "true" : "false");
  }}
  function show(id, push) {{
    var hit = items.filter(function (b) {{ return b.dataset.target === id; }})[0];
    if (!hit) hit = items[0];
    sections.forEach(function (s) {{ s.hidden = s.id !== hit.dataset.target; }});
    items.forEach(function (b) {{
      b.setAttribute("aria-checked", b === hit ? "true" : "false");
    }});
    cur.textContent = hit.dataset.pointer;
    if (push && location.hash.slice(1) !== hit.dataset.target) {{
      history.replaceState(null, "", "#" + hit.dataset.target);
    }}
  }}
  btn.addEventListener("click", function () {{
    open(btn.getAttribute("aria-expanded") !== "true");
  }});
  items.forEach(function (b) {{
    b.addEventListener("click", function () {{
      show(b.dataset.target, true);
      open(false);
      btn.focus();
    }});
  }});
  document.addEventListener("click", function (e) {{
    if (!nav.contains(e.target)) open(false);
  }});
  document.addEventListener("keydown", function (e) {{
    if (e.key === "Escape") {{ open(false); }}
  }});
  window.addEventListener("hashchange", function () {{ show(location.hash.slice(1), false); }});
  show(location.hash.slice(1), false);
}})();
</script>
</body></html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="EVerest/EVerest")
    ap.add_argument("--grace-days", type=int, default=14)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "status"))
    args = ap.parse_args()

    try:
        with open(os.path.join(ROOT, "pointers.json")) as f:
            declared = (json.load(f).get("pointers") or {})
    except Exception:                                          # noqa: BLE001
        declared = {}

    rows = load_results()
    tips = pointer_tips(declared.keys(), args.repo, args.offline)
    groups = {}
    for r in rows:
        groups.setdefault(r["pointer"], []).append(r)

    generated = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    summary = {
        "generated": generated, "selfReported": True,
        "graceDays": args.grace_days, "internalFormat": "openhtf",
        "pointers": {},
    }
    for ptr in sorted(set(list(groups) + list(declared))):
        per = {}
        for r in groups.get(ptr, []):
            per.setdefault(r["integrator"], []).append(r)
        for v in per.values():
            v.sort(key=lambda r: (r["ranAt"], r["file"]), reverse=True)
        summary["pointers"][ptr] = {
            "tracks": (declared.get(ptr) or {}).get("tracks"),
            "tip": tips.get(ptr),
            "integrators": {
                who: {
                    "displayName": rs[0]["displayName"],
                    "sourceFormat": rs[0]["sourceFormat"],
                    "commit": rs[0]["commit"],
                    "environment": rs[0]["environmentKind"],
                    "dutId": rs[0]["dutId"],
                    "build": (rs[0]["build"] or {}).get("status"),
                    "buildTargets": rs[0]["targetsTotal"],
                    "buildTargetsFailed": rs[0]["targetsFailed"],
                    "modules": rs[0]["modules"],
                    "phases": rs[0]["phaseTotal"],
                    "passed": rs[0]["phasePassed"],
                    "failed": rs[0]["phaseFailed"],
                    "skipped": rs[0]["phaseSkipped"],
                    "measurements": len(rs[0]["measurements"]),
                    "measurementsFailed": rs[0]["measFailed"],
                    "coverage": rs[0]["tagStates"],
                    "ran": fmt_ms(rs[0]["ranAt"]),
                    "ageDays": (round(age_days(rs[0]["ranAt"]), 1)
                                if age_days(rs[0]["ranAt"]) is not None else None),
                    "stale": is_stale(rs[0], tips, args.grace_days),
                    "staleUnknownBecause": stale_reason(rs[0], tips) or None,
                    "submissions": len(rs),
                } for who, rs in per.items()},
        }

    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
        f.write("\n")
    with open(os.path.join(args.out_dir, "index.html"), "w") as f:
        f.write(render(groups, tips, args.grace_days, generated, declared))
    print(f"wrote {args.out_dir}: {len(rows)} submission(s) across "
          f"{len(summary['pointers'])} pointer(s); tips="
          + ", ".join(f"{k}={'?' if not v else v[:8]}" for k, v in tips.items()))


if __name__ == "__main__":
    sys.exit(main())
