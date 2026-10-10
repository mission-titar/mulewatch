from p2pwatch.ports.repository_errors import RepositoryError


def test_repository_error_is_an_exception() -> None:
    assert issubclass(RepositoryError, Exception)
    error = RepositoryError("boum")
    assert str(error) == "boum"
