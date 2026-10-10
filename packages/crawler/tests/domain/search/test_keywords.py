from p2pwatch.domain.search.keywords import SearchKeyword, generate_keywords


def test_generates_one_keyword_per_input_in_order() -> None:
    keywords = generate_keywords(["keroro", "titar"])
    assert [kw.text for kw in keywords] == ["keroro", "titar"]


def test_origin_is_the_keyword_text() -> None:
    (kw,) = generate_keywords(["keroro"])
    assert kw == SearchKeyword(text="keroro", origin="keroro")


def test_deduplicates_keeping_first_seen_order() -> None:
    keywords = generate_keywords(["keroro", "titar", "keroro"])
    assert [kw.text for kw in keywords] == ["keroro", "titar"]


def test_drops_empty_strings() -> None:
    keywords = generate_keywords(["", "keroro"])
    assert [kw.text for kw in keywords] == ["keroro"]


def test_empty_input_yields_empty_tuple() -> None:
    assert generate_keywords([]) == ()


def test_keyword_is_frozen_and_hashable() -> None:
    keyword = SearchKeyword(text="keroro", origin="keroro")
    assert {keyword, keyword} == {keyword}
    assert hash(keyword) == hash(SearchKeyword(text="keroro", origin="keroro"))
