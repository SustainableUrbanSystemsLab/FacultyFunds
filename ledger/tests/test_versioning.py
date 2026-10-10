import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

from config.version import AppVersion, from_describe, from_version_file, resolve

SCRIPT = Path(settings.BASE_DIR) / "scripts" / "release.py"
_spec = importlib.util.spec_from_file_location("release_script", SCRIPT)
release = importlib.util.module_from_spec(_spec)
sys.modules["release_script"] = release
_spec.loader.exec_module(release)

PASSWORD = "correct horse battery staple"


class CommitParsingTests(SimpleTestCase):
    def parse(self, subject, body=""):
        return release.parse_commit("a" * 40, subject, body)

    def test_feature_fix_and_performance_commits_set_the_bump(self):
        self.assertEqual(self.parse("feat: add funding").bump, "minor")
        self.assertEqual(self.parse("fix(imports): skip totals").bump, "patch")
        self.assertEqual(self.parse("perf: batch queries").bump, "patch")

    def test_scope_and_text_are_split_out(self):
        commit = self.parse("fix(imports): skip total rows")
        self.assertEqual((commit.type, commit.scope, commit.text), ("fix", "imports", "skip total rows"))

    def test_breaking_changes_are_major(self):
        self.assertEqual(self.parse("feat!: drop the old importer").bump, "major")
        self.assertEqual(self.parse("refactor!: rename tables").bump, "major")
        commit = self.parse("feat: new fund form", "Longer text.\n\nBREAKING CHANGE: funds need a kind")
        self.assertEqual(commit.bump, "major")
        self.assertEqual(commit.breaking, "funds need a kind")

    def test_other_types_do_not_release_on_their_own(self):
        for subject in ("docs: update readme", "style: tidy css", "chore(release): housekeeping", "ci: tweak", "test: more"):
            with self.subTest(subject=subject):
                self.assertIsNone(self.parse(subject).bump)

    def test_merges_reverts_and_free_text_are_ignored(self):
        for subject in ("Merge pull request #3 from x/y", 'Revert "feat: thing"', "update stuff", "feat add thing"):
            with self.subTest(subject=subject):
                self.assertIsNone(self.parse(subject))


class VersionMathTests(SimpleTestCase):
    def test_the_biggest_change_wins(self):
        commits = [release.parse_commit("a", s) for s in ("fix: a", "feat: b", "docs: c")]
        self.assertEqual(release.decide_bump(commits), "minor")
        commits.append(release.parse_commit("b", "fix!: c"))
        self.assertEqual(release.decide_bump(commits), "major")
        self.assertIsNone(release.decide_bump([release.parse_commit("a", "docs: x")]))
        self.assertIsNone(release.decide_bump([]))

    def test_bumping_resets_the_lower_numbers(self):
        self.assertEqual(release.bump_version((1, 4, 7), "patch"), (1, 4, 8))
        self.assertEqual(release.bump_version((1, 4, 7), "minor"), (1, 5, 0))
        self.assertEqual(release.bump_version((1, 4, 7), "major"), (2, 0, 0))
        self.assertEqual(release.format_version((0, 10, 2)), "0.10.2")

    def test_latest_release_compares_numbers_not_text(self):
        tag, version = release.latest_release(["v0.9.0", "v0.10.0", "v1.0.0-rc1", "foo", "v2", "v0.2.1"])
        self.assertEqual((tag, version), ("v0.10.0", (0, 10, 0)))
        self.assertEqual(release.latest_release(["nothing"]), (None, (0, 0, 0)))

    def test_release_notes_group_commits(self):
        commits = [
            release.parse_commit("1" * 40, "feat(funds): state funds lapse each year"),
            release.parse_commit("2" * 40, "fix: months in the burn rate"),
            release.parse_commit("3" * 40, "feat!: new importer", "BREAKING CHANGE: re-map your columns"),
            release.parse_commit("4" * 40, "docs: nothing to see"),
        ]
        notes = release.release_notes("1.0.0", "v0.9.0", commits, "https://github.com/o/r")
        self.assertIn("### ⚠️ Breaking changes", notes)
        self.assertIn("re-map your columns", notes)
        self.assertIn("### Features", notes)
        self.assertIn("**funds:** state funds lapse each year", notes)
        self.assertIn("### Fixes", notes)
        self.assertNotIn("nothing to see", notes)
        self.assertIn("https://github.com/o/r/compare/v0.9.0...v1.0.0", notes)


class ReleasePlanTests(SimpleTestCase):
    """Runs the planner against a throwaway git repository."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.repo = folder.name
        self.env = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
        self.git("init", "-q", "-b", "main")

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.repo, env=self.env, check=True, capture_output=True, text=True)

    def commit(self, message):
        self.git("commit", "--allow-empty", "-q", "-m", message)

    def plan(self):
        return release.plan(cwd=self.repo, repo_url="https://github.com/o/r")

    def test_first_release_counts_from_zero(self):
        self.commit("chore: start")
        self.commit("feat: add funds")
        self.commit("fix: typo")
        result = self.plan()
        self.assertEqual((result["new_version"], result["app_version"], result["minor"]), ("0.1.0", "0.1.0", "0.1"))
        self.assertIn("add funds", result["notes"])
        self.assertEqual(len(result["commit"]), 7)

    def test_each_kind_of_commit_bumps_from_the_latest_tag(self):
        self.commit("feat: first")
        self.git("tag", "v0.1.0")
        self.commit("fix: small thing")
        self.assertEqual(self.plan()["new_version"], "0.1.1")
        self.commit("feat: bigger thing")
        self.assertEqual(self.plan()["new_version"], "0.2.0")
        self.commit("feat!: breaking thing")
        self.assertEqual(self.plan()["new_version"], "1.0.0")

    def test_no_release_when_nothing_user_facing_changed(self):
        self.commit("feat: first")
        self.git("tag", "v0.1.0")
        self.commit("docs: wording")
        self.commit("Update a thing without a prefix")
        result = self.plan()
        self.assertEqual(result["new_version"], "")
        self.assertEqual((result["app_version"], result["minor"]), ("0.1.0+2", ""))

    def test_a_commit_that_is_already_tagged_is_that_release(self):
        self.commit("feat: first")
        self.git("tag", "v1.4.0")
        result = self.plan()
        self.assertEqual((result["new_version"], result["app_version"], result["minor"]), ("", "1.4.0", "1.4"))

    def test_only_tags_reachable_from_head_count(self):
        self.commit("feat: first")
        self.git("tag", "v0.1.0")
        self.git("checkout", "-q", "-b", "side")
        self.commit("feat: side work")
        self.git("tag", "v0.9.0")
        self.git("checkout", "-q", "main")
        self.commit("fix: main work")
        self.assertEqual(self.plan()["new_version"], "0.1.1")

    def test_command_line_writes_outputs_and_notes(self):
        self.commit("feat: add funds")
        out, notes = Path(self.repo, "out.txt"), Path(self.repo, "notes.md")
        with contextlib.redirect_stdout(io.StringIO()):
            status = release.main(["--github-output", str(out), "--notes", str(notes)], cwd=self.repo)
        self.assertEqual(status, 0)
        lines = out.read_text().splitlines()
        self.assertIn("new_version=0.1.0", lines)
        self.assertIn("app_version=0.1.0", lines)
        self.assertIn("minor=0.1", lines)
        self.assertIn("add funds", notes.read_text())


class DescribeParsingTests(SimpleTestCase):
    def test_exact_release(self):
        self.assertEqual(from_describe("v0.2.0"), AppVersion("0.2.0", ""))
        self.assertEqual(from_describe("v0.2.0-0-gabc1234"), AppVersion("0.2.0", "abc1234"))
        self.assertTrue(from_describe("v0.2.0").is_release)

    def test_commits_after_a_release(self):
        version = from_describe("v0.2.0-3-gabc1234def")
        self.assertEqual((version.version, version.commit, version.label), ("0.2.0+3", "abc1234", "v0.2.0+3"))
        self.assertFalse(version.is_release)

    def test_uncommitted_changes_are_marked(self):
        self.assertEqual(from_describe("v0.2.0-0-gabc1234-dirty").version, "0.2.0+dirty")
        self.assertEqual(from_describe("v0.2.0-3-gabc1234-dirty").version, "0.2.0+3.dirty")

    def test_garbage_is_rejected(self):
        for text in ("", "abc1234", "$Format:%(describe)$", "release-1"):
            self.assertIsNone(from_describe(text))

    def test_dev_label(self):
        self.assertEqual(AppVersion("dev", "abc1234").label, "dev")
        self.assertFalse(AppVersion("dev").is_release)


class ResolveVersionTests(SimpleTestCase):
    def folder(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        return Path(folder.name)

    def test_environment_wins(self):
        root = self.folder()
        (root / "VERSION").write_text("v9.9.9\nzzzzzzz\n")
        version = resolve(root, {"APP_VERSION": "v1.2.3", "APP_COMMIT": "0123456789abcdef"})
        self.assertEqual((version.version, version.commit, version.label), ("1.2.3", "0123456", "v1.2.3"))

    def test_expanded_version_file(self):
        root = self.folder()
        (root / "VERSION").write_text("v0.3.0-2-gdeadbee\ndeadbee\n")
        self.assertEqual(resolve(root, {}), AppVersion("0.3.0+2", "deadbee"))

    def test_version_file_without_tags_is_dev_with_a_commit(self):
        root = self.folder()
        (root / "VERSION").write_text("\ndeadbee\n")
        self.assertEqual(resolve(root, {}), AppVersion("dev", "deadbee"))

    def test_unexpanded_placeholders_fall_through(self):
        root = self.folder()
        (root / "VERSION").write_text("$Format:%(describe:tags=true,match=v[0-9]*)$\n$Format:%h$\n")
        self.assertIsNone(from_version_file(root / "VERSION"))
        self.assertEqual(resolve(root, {}), AppVersion("dev"))  # no .git either

    def test_nothing_known_is_dev(self):
        self.assertEqual(resolve(self.folder(), {}), AppVersion("dev"))

    def test_the_real_checkout_resolves_to_something(self):
        self.assertTrue(resolve(Path(settings.BASE_DIR), {}).label)


class FooterVersionTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("owner", password=PASSWORD)
        device = TOTPDevice.objects.create(user=user, name="phone", confirmed=True)
        self.client.post(reverse("accounts:login"), {"username": "owner", "password": PASSWORD})
        token = f"{totp(device.bin_key, device.step, device.t0, device.digits):06d}"
        self.client.post(reverse("accounts:verify"), {"token": token})

    def footer(self, version):
        with override_settings(APP_VERSION=version):
            page = self.client.get(reverse("ledger:dashboard")).content.decode()
        return page[page.index('class="site-footer-inner"'):]

    def test_release_links_to_its_notes(self):
        footer = self.footer(AppVersion("1.2.3", "abc1234"))
        self.assertIn("v1.2.3", footer)
        self.assertIn("/releases/tag/v1.2.3", footer)
        self.assertIn("(abc1234)", footer)

    def test_builds_between_releases_do_not_link(self):
        footer = self.footer(AppVersion("1.2.3+4", "abc1234"))
        self.assertIn("v1.2.3+4", footer)
        self.assertNotIn("/releases/tag/", footer)

    def test_dev_builds_say_dev(self):
        footer = self.footer(AppVersion("dev"))
        self.assertIn(">dev<", footer)
        self.assertNotIn("(", footer.split("Source on GitHub")[0].split("Faculty Funds")[1])

    def test_signed_out_pages_do_not_show_the_version(self):
        self.client.logout()
        with override_settings(APP_VERSION=AppVersion("1.2.3", "abc1234")):
            page = self.client.get(reverse("accounts:login")).content.decode()
        self.assertNotIn("v1.2.3", page)
