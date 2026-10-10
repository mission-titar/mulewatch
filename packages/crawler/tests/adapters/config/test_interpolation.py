import pytest

from p2pwatch.adapters.config.errors import ConfigError
from p2pwatch.adapters.config.interpolation import interpolate


def test_no_pattern_returns_value_unchanged() -> None:
    assert interpolate("plain value", {}, "champ") == "plain value"


def test_single_substitution() -> None:
    assert interpolate("${A}", {"A": "secret"}, "champ") == "secret"


def test_substring_substitution() -> None:
    env = {"ID": "111", "TOKEN": "xyz"}
    assert interpolate("discord://${ID}/${TOKEN}", env, "url") == "discord://111/xyz"


def test_repeated_variable() -> None:
    assert interpolate("${A}-${A}", {"A": "x"}, "champ") == "x-x"


def test_missing_variable_raises_naming_var_and_field() -> None:
    with pytest.raises(ConfigError) as err:
        interpolate("${MISSING}", {}, "crawler.amule_api_password")
    assert "MISSING" in str(err.value)
    assert "crawler.amule_api_password" in str(err.value)


def test_dollar_without_braces_is_literal() -> None:
    assert interpolate("price is $5", {}, "champ") == "price is $5"
