#!/usr/bin/env python3
"""Work out the next release from conventional commit messages (standard library only).

The release workflow runs this on every push to main. Versions follow https://semver.org and the
commit prefixes follow https://www.conventionalcommits.org:

    feat: ...                          minor release  (0.1.0 -> 0.2.0)
    fix: ... / perf: ...               patch release  (0.1.0 -> 0.1.1)
    feat!: ... or a BREAKING CHANGE:   major release  (0.1.0 -> 1.0.0)
    docs, style, refactor, test,
    build, ci, chore                   no release on their own

The latest v<major>.<minor>.<patch> tag reachable from HEAD is the current version; with no tag the
count starts at 0.0.0. Git tags are the only source of truth, so nothing is committed back to main.

    python scripts/release.py                  # show what the next release would be
    python scripts/release.py --github-output "$GITHUB_OUTPUT" --notes release-notes.md
"""

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass

TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
SUBJECT = re.compile(r"^(?P<type>[A-Za-z]+)(?:\((?P<scope>[^)\n]+)\))?(?P<bang>!)?:\s+(?P<text>\S.*)$")
BREAKING_FOOTER = re.compile(r"^BREAKING[ -]CHANGE:\s*(?P<text>.+)$", re.MULTILINE)
BUMP_FOR_TYPE = {"feat": "minor", "fix": "patch", "perf": "patch"}
BUMP_RANK = {"patch": 1, "minor": 2, "major": 3}
NOTE_SECTIONS = [("feat", "Features"), ("fix", "Fixes"), ("perf", "Performance")]
FIELD, RECORD = "\x1f", "\x1e"

Version = tuple[int, int, int]


@dataclass(frozen=True)
class Commit:
    sha: str
    type: str
    scope: str
    text: str
    breaking: str  # empty, or the description of the breaking change

    @property
    def bump(self) -> str | None:
        if self.breaking:
            return "major"
        return BUMP_FOR_TYPE.get(self.type)


def parse_commit(sha: str, subject: str, body: str = "") -> Commit | None:
    """A Commit for a conventional-commit message, or None for merges, reverts and free-form messages."""
    subject = subject.strip()
    if subject.startswith(("Merge ", "Revert ")):
        return None
    match = SUBJECT.match(subject)
    if not match:
        return None
    breaking = match["text"].strip() if match["bang"] else ""
    footer = BREAKING_FOOTER.search(body or "")
    if footer:
        breaking = footer["text"].strip()
    return Commit(sha, match["type"].lower(), match["scope"] or "", match["text"].strip(), breaking)


def decide_bump(commits: list[Commit]) -> str | None:
    levels = [commit.bump for commit in commits if commit.bump]
    return max(levels, key=BUMP_RANK.__getitem__) if levels else None


def bump_version(version: Version, level: str) -> Version:
    major, minor, patch = version
    if level == "major":
        return major + 1, 0, 0
    if level == "minor":
        return major, minor + 1, 0
    return major, minor, patch + 1


def format_version(version: Version) -> str:
    return ".".join(map(str, version))


def latest_release(tags: list[str]) -> tuple[str | None, Version]:
    """The highest vX.Y.Z tag (compared as numbers, not text) and its version."""
    found = [(tuple(map(int, match.groups())), tag) for tag in tags if (match := TAG.match(tag))]
    if not found:
        return None, (0, 0, 0)
    version, tag = max(found)
    return tag, version


def release_notes(version: str, previous_tag: str | None, commits: list[Commit], repo_url: str = "") -> str:
    def bullet(commit: Commit, text: str) -> str:
        scope = f"**{commit.scope}:** " if commit.scope else ""
        link = f"[`{commit.sha[:7]}`]({repo_url}/commit/{commit.sha})" if repo_url else f"`{commit.sha[:7]}`"
        return f"- {scope}{text} ({link})"

    lines = []
    breaking = [commit for commit in commits if commit.breaking]
    if breaking:
        lines += ["### ⚠️ Breaking changes", *(bullet(commit, commit.breaking) for commit in breaking), ""]
    for kind, title in NOTE_SECTIONS:
        items = [commit for commit in commits if commit.type == kind]
        if items:
            lines += [f"### {title}", *(bullet(commit, commit.text) for commit in items), ""]
    if not lines:
        lines = ["Maintenance release.", ""]
    if repo_url and previous_tag:
        lines.append(f"**Full changelog:** {repo_url}/compare/{previous_tag}...v{version}")
    return "\n".join(lines).rstrip() + "\n"


def git(*args: str, cwd: str | None = None) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def commits_since(tag: str | None, cwd: str | None = None) -> tuple[list[Commit], int]:
    """Conventional commits after `tag` (all history without one) and how many commits that is in total."""
    revision = f"{tag}..HEAD" if tag else "HEAD"
    raw = git("log", "--no-merges", f"--format=%H{FIELD}%s{FIELD}%b{RECORD}", revision, cwd=cwd)
    commits, total = [], 0
    for record in filter(str.strip, raw.split(RECORD)):
        sha, subject, body = (record.strip("\n").split(FIELD) + ["", ""])[:3]
        total += 1
        if commit := parse_commit(sha.strip(), subject, body):
            commits.append(commit)
    return commits, total


def plan(cwd: str | None = None, repo_url: str = "") -> dict:
    """Everything the workflow needs to know about this commit's version."""
    tags = git("tag", "--merged", "HEAD", "--list", "v*", cwd=cwd).splitlines()
    previous_tag, current = latest_release(tags)
    short = git("rev-parse", "--short=7", "HEAD", cwd=cwd)
    on_head = [tag for tag in git("tag", "--points-at", "HEAD", cwd=cwd).splitlines() if TAG.match(tag)]
    commits, total = commits_since(previous_tag, cwd=cwd)
    level = decide_bump(commits)

    result = {"new_version": "", "app_version": "", "commit": short, "level": level or "", "notes": ""}
    if on_head:  # this very commit is already a release (for example a tag pushed by hand)
        result["app_version"] = format_version(latest_release(on_head)[1])
    elif level:
        new = format_version(bump_version(current, level))
        result["new_version"] = result["app_version"] = new
        result["notes"] = release_notes(new, previous_tag, commits, repo_url)
    else:
        result["app_version"] = f"{format_version(current)}+{total}" if total else format_version(current)
    result["minor"] = "" if "+" in result["app_version"] else ".".join(result["app_version"].split(".")[:2])
    return result


def main(argv: list[str] | None = None, cwd: str | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--github-output", help="append key=value lines for later workflow steps (a GITHUB_OUTPUT file)")
    parser.add_argument("--notes", help="write the release notes of a new release to this file")
    args = parser.parse_args(argv)

    server, repository = os.environ.get("GITHUB_SERVER_URL", ""), os.environ.get("GITHUB_REPOSITORY", "")
    result = plan(cwd=cwd, repo_url=f"{server}/{repository}" if server and repository else "")

    if result["new_version"]:
        print(f"Next release: v{result['new_version']} ({result['level']} bump)")
    else:
        print(f"No release: {result['app_version']} stays as is (no feat, fix, perf or breaking commits since the last tag)")
    if args.notes and result["new_version"]:
        with open(args.notes, "w", encoding="utf-8") as handle:
            handle.write(result["notes"])
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as handle:
            for key in ("new_version", "app_version", "minor", "commit"):
                handle.write(f"{key}={result[key]}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
