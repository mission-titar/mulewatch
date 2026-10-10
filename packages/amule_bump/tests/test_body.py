from amule_bump.body import (
    GITHUB_BODY_LIMIT,
    TRUNCATED,
    build_body,
    changelog_section,
    clip,
    fenced,
    tag_diff,
    wrap,
)
from amule_bump.dockerfile import Pin
from fakes import FakeRepo

OLD = Pin("3.0.1", "02db0d7faecfc377694ff6242bc23346185990ed")
NEW = Pin("3.1.0", "909d304d993ee07df6c6f6acf501a6d791d53666")

CHANGELOG = """# aMule Changelog

## Version 3.1.0 - "REST"
2026-09-21

- amuleapi (#123), thanks @someone

## Version 3.0.1 \u2014 "bugfixes"

- older entry
"""


def test_changelog_section_keeps_the_version_heading_down_to_the_next_one() -> None:
    assert changelog_section(CHANGELOG, "3.1.0") == (
        '## Version 3.1.0 - "REST"\n2026-09-21\n\n- amuleapi (#123), thanks @someone'
    )


def test_changelog_section_of_a_later_version_runs_to_the_end() -> None:
    assert changelog_section(CHANGELOG, "3.0.1").endswith("- older entry")


def test_changelog_section_does_not_take_a_version_prefix_for_the_version() -> None:
    assert changelog_section("## Version 3.1.0.1 - x\nbody\n", "3.1.0") == ""


def test_changelog_section_is_empty_when_the_version_is_missing() -> None:
    assert changelog_section(CHANGELOG, "9.9.9") == ""


def test_wrap_breaks_long_lines_at_spaces_and_keeps_short_ones() -> None:
    long_line = " ".join(["word"] * 30)
    wrapped = wrap(f"short\n\n{long_line}\n", width=20)
    assert wrapped.splitlines()[:3] == ["short", "", "word word word word"]
    assert all(len(line) <= 20 for line in wrapped.splitlines())


def test_fenced_uses_four_backticks_so_references_do_not_link() -> None:
    assert fenced("see #123\n", "text") == "````text\nsee #123\n````"


def test_fenced_outgrows_a_backtick_run_in_the_text() -> None:
    assert fenced("a ````` b\n", "text") == "``````text\na ````` b\n``````"


def test_fenced_adds_the_missing_final_newline() -> None:
    assert fenced("no newline", "diff") == "````diff\nno newline\n````"


def test_clip_leaves_a_short_text_alone() -> None:
    assert clip("one\ntwo\n", limit=8) == "one\ntwo\n"


def test_clip_cuts_at_a_line_boundary_and_says_so() -> None:
    assert clip("one\ntwo\nthree\n", limit=9) == f"one\ntwo\n{TRUNCATED}\n"


def test_tag_diff_shows_the_change_between_the_two_tags() -> None:
    repo = FakeRepo(
        files={
            ("3.0.1", "cmake/options.cmake"): "a\nb\n",
            ("3.1.0", "cmake/options.cmake"): "a\nc\n",
        }
    )
    assert tag_diff(repo, OLD, NEW, "cmake/options.cmake") == (
        "--- 3.0.1/cmake/options.cmake\n"
        "+++ 3.1.0/cmake/options.cmake\n"
        "@@ -1,2 +1,2 @@\n"
        " a\n"
        "-b\n"
        "+c\n"
    )


def test_tag_diff_shows_a_file_absent_at_the_old_tag_as_added() -> None:
    repo = FakeRepo(files={("3.1.0", "docs/api/REFERENCE.md"): "# API\n"})
    assert tag_diff(repo, OLD, NEW, "docs/api/REFERENCE.md").endswith("@@ -0,0 +1 @@\n+# API\n")


def test_tag_diff_is_empty_when_the_file_did_not_change() -> None:
    repo = FakeRepo(files={("3.0.1", "f"): "same\n", ("3.1.0", "f"): "same\n"})
    assert tag_diff(repo, OLD, NEW, "f") == ""


def full_repo() -> FakeRepo:
    return FakeRepo(
        files={
            ("3.1.0", "docs/CHANGELOG.md"): CHANGELOG,
            ("3.0.1", "cmake/options.cmake"): "option(A)\n",
            ("3.1.0", "cmake/options.cmake"): "option(A)\noption(B)\n",
            ("3.1.0", "docs/api/REFERENCE.md"): "# API\n",
        }
    )


def test_build_body_links_the_release_and_the_compare_view() -> None:
    body = build_body(full_repo(), OLD, NEW)
    assert body.startswith(
        "Bumps aMule from 3.0.1 to [3.1.0](https://github.com/amule-org/amule/releases/tag/3.1.0)"
        " (commit `909d304d993ee07df6c6f6acf501a6d791d53666`). Full upstream diff:"
        " https://github.com/amule-org/amule/compare/3.0.1...3.1.0\n"
    )


def test_build_body_fences_the_changelog_section() -> None:
    body = build_body(full_repo(), OLD, NEW)
    assert '## Changelog\n\n````text\n## Version 3.1.0 - "REST"\n' in body
    assert "older entry" not in body


def test_build_body_carries_both_diffs() -> None:
    body = build_body(full_repo(), OLD, NEW)
    assert "## `cmake/options.cmake` (3.0.1 to 3.1.0)\n\n````diff\n" in body
    assert "+option(B)\n" in body
    assert "## `docs/api/REFERENCE.md` (3.0.1 to 3.1.0)\n\n````diff\n" in body
    assert "+# API\n" in body


def test_build_body_ends_with_the_checklist_naming_the_new_version() -> None:
    body = build_body(full_repo(), OLD, NEW)
    assert body.endswith(
        "## Checklist\n\n"
        "- [ ] Read the changelog: anything that changes the daemon's defaults or the network"
        " behaviour?\n"
        "- [ ] `options.cmake` diff: a new switch to set explicitly in the Dockerfile's CMake"
        " options?\n"
        "- [ ] `REFERENCE.md` diff: does the `mule_api` adapter need a change (with its tests"
        " first)?\n"
        "- [ ] `p2pwatch_amule.config`: does a default we override or rely on change?\n"
        "- [ ] CI green on amd64 and arm64.\n"
        "- [ ] Merge, then tag a release (`vX.Y.Z - aMule 3.1.0`): the image only changes on a"
        " tag.\n"
    )


def test_build_body_says_so_when_the_changelog_or_a_diff_is_empty() -> None:
    body = build_body(FakeRepo(), OLD, NEW)
    assert "No `## Version 3.1.0` section in upstream's docs/CHANGELOG.md.\n" in body
    assert body.count("No change between the two tags.\n") == 2


def test_build_body_stays_under_the_github_limit_with_huge_upstream_text() -> None:
    huge = "x" * 99 + "\n"
    repo = FakeRepo(
        files={
            ("3.1.0", "docs/CHANGELOG.md"): "## Version 3.1.0 - big\n" + huge * 5000,
            ("3.1.0", "cmake/options.cmake"): huge * 5000,
            ("3.1.0", "docs/api/REFERENCE.md"): huge * 5000,
        }
    )
    body = build_body(repo, OLD, NEW)
    assert len(body) < GITHUB_BODY_LIMIT
    assert body.count(TRUNCATED) == 3
