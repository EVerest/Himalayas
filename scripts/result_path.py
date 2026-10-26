"""The path shape of a stored submission, shared by the gate and the pull-request
classifier so the two cannot drift apart."""
import re

# The whole-file cap. results/ keeps files indefinitely, and the intake skips a
# larger file rather than open a pull request the gate must refuse.
MAX_FILE_BYTES = 1_048_576

# results/<integrator>/<pointer>/<YYYYMMDD>[-<label>][-N].json - the pointer keeps
# its slash, so testing/main becomes two path segments.
#
# The label separates records of one day that come from different runs, e.g. a
# 15118 nightly at 20261027.json and an OCPP nightly at 20261027-ocpp201-sil.json,
# so two bots submitting as one integrator never race for the next free -N. Each
# label segment starts with a letter and -N is digits only, so the two never parse
# as each other.
#
# -N exists because the pointers move daily: a re-run after a flake, or a SIL and
# a HIL run on the same day, would otherwise overwrite each other.
RESULT_PATH_RE = re.compile(
    r"^results/(?P<who>[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?)/"
    r"(?P<pointer>testing/(?:main|stable-\d{4}\.\d{2}))/"
    r"(?P<date>\d{8})"
    r"(?:-(?P<label>[a-z][a-z0-9]{0,15}(?:-[a-z][a-z0-9]{0,15}){0,3}))?"
    r"(?:-(?P<seq>[2-9]|[1-9]\d+))?\.json$")
