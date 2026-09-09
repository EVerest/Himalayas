#!/usr/bin/env python3
"""Decide what a pull request is, from the files it changes.

WHY THIS IS A SCRIPT AND NOT SHELL IN THE WORKFLOW
The rule it enforces is the one that stops a fork pull request destroying every
integrator's records, so it has to be testable. The version this replaces lived
in `validate-submission.yml` and was untestable, and it was wrong in a way that
read as correct: it built the change list with `select(.status != "removed")`,
so a deletion-only pull request produced an EMPTY list, every loop after it
iterated zero times, and the workflow armed auto-merge on a vacuous pass. Any
GitHub account could have deleted the whole of results/ from a fork with no
write access and no human in the loop.

RESULTS IS APPEND-ONLY. Records are permanent, and removing one requires human
write access to main. A correction is a new record, never an edit and
never a removal. So this refuses a pull request that removes, modifies or
renames anything under results/ - which is what makes the defect above
unreachable rather than guarded: there is no deletion case left for the gate to
reason about.

Reads the GitHub pull request files API as JSON on stdin, or from --input.
Writes a decision to --github-output if given, and a human verdict to --verdict.

  kind=not-a-submission   nothing under results/ changed; the gate has no
                          opinion, and the check passes so it can be a
                          REQUIRED check on every pull request without
                          deadlocking the ones that are not submissions
  kind=refuse             a rule was broken before any file was even fetched
  kind=submission         one or more added files under results/, nothing else

Exit 0 always: the workflow decides what to do with the decision.
"""
import argparse, json, os, re, sys

# Same shape validate_submission.py enforces, applied HERE because the fetch
# step writes these names to disk. The only prior check was `grep -v '^results/'`,
# which accepts `results/../../etc`, and PATH_RE ran afterwards - so delivery
# depended on git refusing `..` in a tree, an invariant this repository neither
# owns nor documented.
PATH_RE = re.compile(
    r"^results/[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?/"
    r"testing/(?:main|stable-\d{4}\.\d{2})/"
    r"\d{8}(?:-(?:[2-9]|[1-9]\d+))?\.json$")


def md_code(s):
    """A filename inside a Markdown code span, with no way out of it.

    Filenames are attacker-controlled and were interpolated raw, so a backtick
    in a filename broke out of the span and the rest was rendered as Markdown.
    """
    s = str(s).replace("\\", "\\\\").replace("`", "‘").replace("\n", " ")
    return "`" + s.replace("|", "\\|") + "`"


def classify(files):
    """files: the API's list of {status, filename, previous_filename}."""
    added, forbidden, outside = [], [], []
    for f in files:
        name = f.get("filename") or ""
        status = f.get("status") or "unknown"
        if not name.startswith("results/"):
            # A rename OUT of results/ removes a record just as surely.
            prev = f.get("previous_filename") or ""
            if prev.startswith("results/"):
                forbidden.append((status, prev))
            else:
                outside.append((status, name))
            continue
        if status == "added":
            added.append(name)
        else:
            forbidden.append((status, name))

    if forbidden:
        return "refuse", added, forbidden, outside, "append-only"
    if not added:
        # Nothing was added under results/, and nothing under results/ was
        # touched in any other way. Whatever else this pull request does, it is
        # not a submission, so the gate passes and says nothing.
        return "not-a-submission", added, forbidden, outside, ""
    if outside:
        return "refuse", added, forbidden, outside, "mixed"
    bad = [n for n in added if not PATH_RE.match(n)]
    if bad:
        return "refuse", added, forbidden, outside, "bad-path"
    return "submission", added, forbidden, outside, ""


CAVEAT = ("\n---\n*Results in this repository are **self-reported** by integrators "
          "and are not reproduced or verified by the EVerest project.*")


def verdict_for(reason, added, forbidden, outside):
    L = ["### Submission rejected\n"]
    if reason == "append-only":
        L += ["`results/` is an **append-only** record. A correction is a new file, "
              "never an edit and never a removal, and a removal needs write access "
              "and a human - so no pull request can make one.\n",
              "This pull request would change existing records:\n"]
        L += [f"- {md_code(n)} &mdash; *{s}*" for s, n in forbidden]
        L += ["\nOpen a pull request that only **adds** files under `results/`.\n"]
    elif reason == "mixed":
        L += ["A results pull request must change files under `results/` **only**. "
              "This one also changes:\n"]
        L += [f"- {md_code(n)} &mdash; *{s}*" for s, n in outside]
        L += ["\nAllowlist changes, schema changes and code changes go in separate "
              "pull requests, because those are reviewed by a code owner and "
              "submissions are not.\n"]
    elif reason == "bad-path":
        bad = [n for n in added if not PATH_RE.match(n)]
        L += ["A submission must be named "
              "`results/<integrator>/<pointer>/YYYYMMDD.json`, with an optional "
              "`-2`, `-3`... suffix for a second run the same day. These are not:\n"]
        L += [f"- {md_code(n)}" for n in bad]
        L += [""]
    L.append(CAVEAT)
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", help="the files API response; stdin if omitted")
    ap.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT"))
    ap.add_argument("--verdict", help="write the rejection comment here")
    ap.add_argument("--added", help="write the accepted file list here, one per line")
    args = ap.parse_args()

    raw = open(args.input).read() if args.input else sys.stdin.read()
    try:
        files = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"error: the files API response is not JSON: {e}", file=sys.stderr)
        return 2
    if not isinstance(files, list):
        print("error: expected a JSON array from the files API", file=sys.stderr)
        return 2
    # `gh api --paginate --slurp` wraps the pages, so the array can be an array of
    # arrays. Flatten here rather than in the workflow: a shell one-liner doing it
    # would be one more untested thing between the API and the decision.
    if files and all(isinstance(x, list) for x in files):
        files = [f for page in files for f in page]
    if not all(isinstance(f, dict) for f in files):
        print("error: the files API response is not a list of objects", file=sys.stderr)
        return 2

    kind, added, forbidden, outside, reason = classify(files)
    print(f"kind={kind} reason={reason or '-'} added={len(added)} "
          f"forbidden={len(forbidden)} outside={len(outside)}")
    for s, n in forbidden:
        print(f"  forbidden: {s} {n}")
    for n in added:
        print(f"  added: {n}")

    if args.github_output:
        with open(args.github_output, "a") as f:
            f.write(f"kind={kind}\nreason={reason}\n")
    if args.added:
        # Only a submission produces a fetch list. A refused pull request must
        # leave nothing for the next step to fetch, whatever else it contains.
        with open(args.added, "w") as f:
            if kind == "submission":
                f.write("".join(n + "\n" for n in added))
    if args.verdict and kind == "refuse":
        with open(args.verdict, "w") as f:
            f.write(verdict_for(reason, added, forbidden, outside) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
