import pytest

from amule_bump import BumpError
from amule_bump.dockerfile import Pin, read_pin, rewrite_pin

DOCKERFILE = """FROM debian AS build
ARG AMULE_VERSION=3.0.1
ARG AMULE_COMMIT=02db0d7faecfc377694ff6242bc23346185990ed
RUN echo "$AMULE_VERSION"
"""
NEW_PIN = Pin("3.1.0", "909d304d993ee07df6c6f6acf501a6d791d53666")


def test_read_pin_returns_the_version_and_commit() -> None:
    assert read_pin(DOCKERFILE) == Pin("3.0.1", "02db0d7faecfc377694ff6242bc23346185990ed")


def test_read_pin_fails_on_a_missing_arg() -> None:
    without_commit = DOCKERFILE.replace("ARG AMULE_COMMIT=", "ARG OTHER=")
    with pytest.raises(BumpError, match="expected one 'ARG AMULE_COMMIT=' line, found 0"):
        read_pin(without_commit)


def test_read_pin_fails_on_a_duplicated_arg() -> None:
    doubled = DOCKERFILE + "ARG AMULE_VERSION=3.0.0\n"
    with pytest.raises(BumpError, match="expected one 'ARG AMULE_VERSION=' line, found 2"):
        read_pin(doubled)


def test_read_pin_ignores_a_mention_that_is_not_the_arg_line() -> None:
    commented = DOCKERFILE + "# see ARG AMULE_VERSION=x above\n"
    assert read_pin(commented).version == "3.0.1"


def test_rewrite_pin_replaces_both_args_and_nothing_else() -> None:
    assert rewrite_pin(DOCKERFILE, NEW_PIN) == (
        "FROM debian AS build\n"
        "ARG AMULE_VERSION=3.1.0\n"
        "ARG AMULE_COMMIT=909d304d993ee07df6c6f6acf501a6d791d53666\n"
        'RUN echo "$AMULE_VERSION"\n'
    )


def test_pin_lines_render_the_two_arg_lines() -> None:
    assert NEW_PIN.arg_lines() == (
        "ARG AMULE_VERSION=3.1.0\nARG AMULE_COMMIT=909d304d993ee07df6c6f6acf501a6d791d53666"
    )
