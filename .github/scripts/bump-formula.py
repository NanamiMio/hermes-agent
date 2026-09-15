"""Bump the homebrew tap formula for a followed upstream release.

Usage: TAG=v2026.9.14 SHA=<tarball sha256> REV_SHA=<tag commit sha>
       OWNER_REPO=NanamiMio/hermes-agent FORMULA_PATH=Formula/hermes-agent.rb
       python3 bump-formula.py   (run inside the cloned tap repo's parent)

Only touches the tarball url + its sha256 line and HERMES_REVISION.
Resource-block sha256 lines are never modified.
"""
import os
import re
import sys

formula_path = os.path.join("tap-repo", os.environ["FORMULA_PATH"])
s = open(formula_path).read()
tag = os.environ["TAG"]
sha = os.environ["SHA"]
rev = os.environ["REV_SHA"]
owner_repo = os.environ["OWNER_REPO"]

s2, n1 = re.subn(
    r"(" + re.escape(owner_repo) + r"/archive/refs/tags/)v[0-9.]+\.tar\.gz(\"\n\s+sha256 \") *[a-f0-9]{64}",
    rf"\g<1>{tag}.tar.gz\g<2>{sha}",
    s,
)
s2, n2 = re.subn(r'(HERMES_REVISION:\s*") *[a-f0-9]{40}', rf"\g<1>{rev}", s2)
if (n1, n2) != (1, 1):
    sys.exit(f"expected exactly 1+1 replacements, got {n1}+{n2}")
open(formula_path, "w").write(s2)
print("formula bumped:", tag, sha[:12], rev[:12])
