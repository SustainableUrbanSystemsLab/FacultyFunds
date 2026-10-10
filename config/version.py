"""The version of the running app, shown in the page footer.

Release versions are git tags (see scripts/release.py). The app learns its version from, in order:

1. APP_VERSION and APP_COMMIT environment variables, set when the release workflow builds the Docker image;
2. the VERSION file, which `git archive` (and GitHub's zip download) fills in with `git describe`;
3. `git describe` in a checkout, for local development;
4. otherwise "dev".
"""

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# git describe output: v1.2.3 (exactly a tag) or v1.2.3-4-gabc1234 (4 commits later), maybe with -dirty.
DESCRIBE = re.compile(r"^v?(?P<version>\d+\.\d+\.\d+)(?:-(?P<distance>\d+)-g(?P<sha>[0-9a-f]+))?(?P<dirty>-dirty)?$")
SHORT_SHA = 7


@dataclass(frozen=True)
class AppVersion:
    version: str  # "1.2.3", "1.2.3+4" (4 commits after the release), or "dev"
    commit: str = ""  # short commit id, if known

    @property
    def label(self) -> str:
        return "dev" if self.version == "dev" else f"v{self.version}"

    @property
    def is_release(self) -> bool:
        """True for an exact release such as 1.2.3, false for builds in between and for "dev"."""
        return bool(re.fullmatch(r"\d+\.\d+\.\d+", self.version))


def from_describe(text: str, commit: str = "") -> AppVersion | None:
    match = DESCRIBE.match(text.strip())
    if not match:
        return None
    version = match["version"]
    distance = int(match["distance"] or 0)
    extra = [text for text in (str(distance) if distance else "", "dirty" if match["dirty"] else "") if text]
    if extra:
        version += "+" + ".".join(extra)
    return AppVersion(version, (match["sha"] or commit)[:SHORT_SHA])


def from_version_file(path: Path) -> AppVersion | None:
    """VERSION holds two `git archive` placeholders; in a plain checkout they are still unexpanded."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    describe, commit = (lines + ["", ""])[:2]
    if describe.startswith("$Format") or commit.startswith("$Format"):
        return None
    return from_describe(describe, commit) or (AppVersion("dev", commit.strip()[:SHORT_SHA]) if commit.strip() else None)


def from_git(root: Path) -> AppVersion | None:
    if not (root / ".git").exists():
        return None

    def run(*args: str) -> str | None:
        try:
            done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=3)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip() if done.returncode == 0 else None

    described = run("describe", "--tags", "--long", "--dirty", "--match", "v[0-9]*")
    if described and (version := from_describe(described)):
        return version
    commit = run("rev-parse", f"--short={SHORT_SHA}", "HEAD")
    return AppVersion("dev", commit) if commit else None


def resolve(root: Path, environ=None) -> AppVersion:
    environ = os.environ if environ is None else environ
    if declared := environ.get("APP_VERSION", "").strip():
        return AppVersion(declared.removeprefix("v"), environ.get("APP_COMMIT", "").strip()[:SHORT_SHA])
    return from_version_file(root / "VERSION") or from_git(root) or AppVersion("dev")
