"""Static discovery of PATH entries in common user shell configuration files."""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


@dataclass(frozen=True)
class PathSetup:
    """A shell configuration line that adds an executable directory to PATH."""

    file: str
    line: int
    entry: str


def _configuration_files(home: Path, env: Mapping[str, str]) -> list[tuple[str, Path]]:
    """Return supported user configuration files in a stable shell order."""
    zdotdir = Path(env.get("ZDOTDIR") or home).expanduser().absolute()
    xdg_config_home = (
        Path(env.get("XDG_CONFIG_HOME") or home / ".config").expanduser().absolute()
    )
    fish_dir = xdg_config_home / "fish"

    candidates: list[tuple[str, Path]] = [
        ("bash", home / ".bashrc"),
        ("bash", home / ".bash_profile"),
        ("bash", home / ".bash_login"),
        ("bash", home / ".profile"),
    ]
    candidates.append(("fish", fish_dir / "config.fish"))
    try:
        candidates.extend(
            ("fish", path)
            for path in sorted(fish_dir.joinpath("conf.d").glob("*.fish"))
        )
    except OSError:
        pass
    candidates.extend(
        ("zsh", zdotdir / name)
        for name in (".zshenv", ".zprofile", ".zshrc", ".zlogin")
    )

    unique: dict[str, tuple[str, Path]] = {}
    for shell, candidate in candidates:
        key = os.path.abspath(os.fspath(candidate))
        unique.setdefault(key, (shell, candidate))
    return list(unique.values())


def _expand_path_entry(value: str, home: Path) -> str | None:
    """Expand only literal home-directory forms; never evaluate shell code."""
    value = value.strip().rstrip(";")
    if not value:
        return None

    quote: str | None = None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        quote = value[0]
        value = value[1:-1]
    else:
        if value.startswith("\"") or value.startswith("'"):
            quote = value[0]
            value = value[1:]
        if value.endswith("\"") or value.endswith("'"):
            value = value[:-1]

    # Single quotes suppress both tilde and variable expansion in these shells.
    if quote != "'":
        if value == "~":
            value = str(home)
        elif value.startswith("~/"):
            value = str(home / value[2:])
        value = value.replace("${HOME}", str(home)).replace("$HOME", str(home))

    if not os.path.isabs(value) or "$" in value or "~" in value:
        return None
    return os.path.normpath(value)


def _path_values(line: str, shell: str) -> Sequence[str]:
    """Extract literal path candidates from common Bash/Zsh and Fish forms."""
    try:
        tokens = shlex.split(line, comments=True, posix=False)
    except ValueError:
        return ()
    if not tokens:
        return ()

    values: list[str] = []
    if shell in {"bash", "zsh"}:
        assignment_tokens = tokens[1:] if tokens[0] == "export" else tokens[:1]
        for token in assignment_tokens:
            if token.startswith("PATH=") or token.startswith("PATH+="):
                assignment_value = token.split("=", 1)[1]
                values.extend(assignment_value.split(":"))

    if shell == "fish":
        if tokens[0] == "set" and "PATH" in tokens[1:]:
            path_index = tokens.index("PATH", 1)
            flags = tokens[1:path_index]
            exported = "--export" in flags or any(
                flag.startswith("-") and "x" in flag[1:] for flag in flags
            )
            if exported:
                for token in tokens[path_index + 1 :]:
                    values.extend(token.split(":"))

        if tokens[0] == "fish_add_path":
            values.extend(token for token in tokens[1:] if not token.startswith("-"))

    return values


def _fish_user_paths(fish_dir: Path, home: Path) -> list[PathSetup]:
    """Read Fish v3 universal path records without executing shell config."""
    variables_file = fish_dir / "fish_variables"
    if not variables_file.is_file():
        return []
    try:
        lines = variables_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    if "# VERSION: 3.0" not in lines:
        return []

    record = re.compile(
        r"^SETUVAR(?:\s+--\S+)*\s+fish_user_paths:(.*)$"
    )
    escape = re.compile(rb"\\x([0-9a-fA-F]{2})")
    latest: tuple[int, list[str]] | None = None
    for line_number, line in enumerate(lines, start=1):
        match = record.match(line)
        if match is None:
            continue
        raw_value = match.group(1).encode("utf-8")
        decoded = escape.sub(
            lambda item: bytes((int(item.group(1), 16),)), raw_value
        )
        paths: list[str] = []
        for raw_path in decoded.split(b"\x1e"):
            value = raw_path.decode("utf-8", errors="replace")
            expanded = _expand_path_entry(value, home)
            if expanded is not None:
                paths.append(expanded)
        latest = (line_number, paths)

    if latest is None:
        return []
    line_number, paths = latest
    return [
        PathSetup(
            file=os.path.abspath(os.fspath(variables_file)),
            line=line_number,
            entry=f"Fish persistent PATH entry (fish_user_paths): {path}",
        )
        for path in paths
    ]


def find_path_setup(
    executable_directory: Path,
    *,
    env: Mapping[str, str] | None = None,
) -> list[PathSetup]:
    """Find config lines that statically add ``executable_directory`` to PATH."""
    current_env = os.environ if env is None else env
    home = Path(current_env.get("HOME") or Path.home()).expanduser().absolute()
    target = os.path.normpath(os.fspath(executable_directory.absolute()))
    matches: list[PathSetup] = []

    config_files = _configuration_files(home, current_env)
    for shell, config_file in config_files:
        try:
            lines = config_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, start=1):
            for value in _path_values(line, shell):
                expanded = _expand_path_entry(value, home)
                if expanded == target:
                    matches.append(
                        PathSetup(
                            file=os.path.abspath(os.fspath(config_file)),
                            line=line_number,
                            entry=line.strip(),
                        )
                    )
                    break

    xdg_config_home = Path(
        current_env.get("XDG_CONFIG_HOME") or home / ".config"
    ).expanduser().absolute()
    fish_dir = xdg_config_home / "fish"
    target = os.path.normpath(os.fspath(executable_directory.absolute()))
    fish_entry_prefix = "Fish persistent PATH entry (fish_user_paths): "
    matches.extend(
        item
        for item in _fish_user_paths(fish_dir, home)
        if item.entry.removeprefix(fish_entry_prefix) == target
    )

    return matches
