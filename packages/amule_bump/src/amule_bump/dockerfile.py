import re
from dataclasses import dataclass

from amule_bump import BumpError


@dataclass(frozen=True)
class Pin:
    version: str
    commit: str

    def arg_lines(self) -> str:
        return f"ARG AMULE_VERSION={self.version}\nARG AMULE_COMMIT={self.commit}"


def read_pin(dockerfile: str) -> Pin:
    return Pin(_arg_value(dockerfile, "AMULE_VERSION"), _arg_value(dockerfile, "AMULE_COMMIT"))


def rewrite_pin(dockerfile: str, pin: Pin) -> str:
    dockerfile = _arg_line("AMULE_VERSION").sub(f"ARG AMULE_VERSION={pin.version}", dockerfile)
    return _arg_line("AMULE_COMMIT").sub(f"ARG AMULE_COMMIT={pin.commit}", dockerfile)


def _arg_value(dockerfile: str, name: str) -> str:
    # Exactly one line, or the rewrite would be ambiguous.
    values = _arg_line(name).findall(dockerfile)
    if len(values) != 1:
        raise BumpError(f"expected one 'ARG {name}=' line, found {len(values)}")
    return str(values[0])


def _arg_line(name: str) -> re.Pattern[str]:
    return re.compile(rf"^ARG {name}=(.*)$", re.MULTILINE)
