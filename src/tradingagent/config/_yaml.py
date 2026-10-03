from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from tradingagent.config.errors import ConfigError

Location = tuple[int | str, ...]
Problem = tuple[Location, str]


@dataclass(frozen=True)
class YamlDocument:
    path: Path
    root: yaml.Node
    data: Any

    def locate(self, problems: Iterable[Problem]) -> list[str]:
        """One line per distinct problem, `path:line: where: message`, in file order."""
        located = sorted(
            {
                (_line_of(self.root, location), _dotted(location), message)
                for location, message in problems
            }
        )
        return [f"  {self.path}:{line}: {where}: {message}" for line, where, message in located]


def read_yaml(path: Path) -> YamlDocument:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigError(f"{path}: cannot read configuration ({error.strerror})") from None
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        line = mark.line + 1 if mark is not None else "?"
        raise ConfigError(f"{path}:{line}: invalid YAML: {error}") from None
    if root is None:
        raise ConfigError(f"{path}: configuration is empty")
    return YamlDocument(path=path, root=root, data=data)


def validation_problems(error: ValidationError, prefix: Location = ()) -> list[Problem]:
    # Locations and messages only, never the offending input.
    return [
        ((*prefix, *detail["loc"]), detail["msg"])
        for detail in error.errors(include_input=False, include_url=False)
    ]


def render(title: str, lines: Iterable[str]) -> str:
    return f"{title}:\n" + "\n".join(lines)


def _line_of(root: yaml.Node, location: Location) -> int:
    node = root
    for key in location:
        child: yaml.Node | None = None
        if isinstance(node, yaml.MappingNode):
            child = next(
                (
                    value
                    for name, value in node.value
                    if isinstance(name, yaml.ScalarNode) and name.value == str(key)
                ),
                None,
            )
        elif (
            isinstance(node, yaml.SequenceNode)
            and isinstance(key, int)
            and 0 <= key < len(node.value)
        ):
            child = node.value[key]
        if child is None:
            break
        node = child
    return int(node.start_mark.line) + 1


def _dotted(location: Location) -> str:
    rendered = ""
    for key in location:
        rendered += f"[{key}]" if isinstance(key, int) else (f".{key}" if rendered else key)
    return rendered or "(root)"
