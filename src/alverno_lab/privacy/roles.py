"""Organisation roles and headcounts (``data/hr/org_roles.yaml``), for small-group rules.

A role or a role on one shift with fewer than k people (k = 5, a lab convention for
small-cell suppression, not a legal threshold) is a small group: a question or a
document that ties pay or a record to it can single someone out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from alverno_lab.privacy.lexicon import FUNCTION_WORDS, normalize
from alverno_lab.privacy.types import PrivacyConfigError

Shift = Literal["day", "evening", "night"]

# Shift qualifiers after a role, on normalised text: « de soir », « du quart de jour »...
# There is no night shift (headcount 0 on purpose, spec M9).
_SHIFT_RE = re.compile(r"\s+(?:(?:du|de|au|sur le) quart )?(?:de|du) (soir|jour|nuit)(?![\w-])")
_SHIFT_NAMES: dict[str, Shift] = {"jour": "day", "soir": "evening", "nuit": "night"}


class _RoleModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z_]+$")
    aliases: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1)
    day: int = Field(ge=0)
    evening: int = Field(ge=0)


class _DepartmentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    total: int = Field(ge=0)
    roles: list[_RoleModel] = Field(min_length=1)


class _OrgModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    departments: list[_DepartmentModel] = Field(min_length=1)


@dataclass(frozen=True)
class Role:
    """One role with its normalised aliases and its headcount per shift."""

    id: str
    department: str
    aliases: tuple[str, ...]
    day: int
    evening: int

    @property
    def total(self) -> int:
        """Headcount over both shifts."""
        return self.day + self.evening

    def headcount(self, shift: Shift | None = None) -> int:
        """Headcount on ``shift`` (``night`` is always 0), or in total."""
        if shift is None:
            return self.total
        if shift == "night":
            return 0
        return self.day if shift == "day" else self.evening


@dataclass(frozen=True)
class RoleMention:
    """A role named in a normalised text, with the shift qualifier that follows it."""

    role: Role
    start: int
    end: int
    shift: Shift | None

    @property
    def headcount(self) -> int:
        """People the mention can designate."""
        return self.role.headcount(self.shift)


@dataclass(frozen=True)
class OrgRoles:
    """Roles of the fictional company, matched by alias on normalised text."""

    roles: tuple[Role, ...]

    @classmethod
    def load(cls, path: Path) -> OrgRoles:
        """Read and validate ``org_roles.yaml``; every error is a :class:`PrivacyConfigError`."""
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise PrivacyConfigError(f"organisation roles not readable: {path}") from exc
        except yaml.YAMLError:
            raise PrivacyConfigError(f"{path}: invalid YAML") from None
        try:
            model = _OrgModel.model_validate(raw)
        except ValidationError as exc:
            locs = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in exc.errors())
            raise PrivacyConfigError(f"{path}: invalid organisation roles ({locs})") from None
        roles: list[Role] = []
        for dep in model.departments:
            if sum(r.day + r.evening for r in dep.roles) != dep.total:
                raise PrivacyConfigError(f"{path}: department total differs from its roles")
            roles.extend(
                Role(
                    id=r.id,
                    department=dep.name,
                    aliases=tuple(dict.fromkeys(normalize(a).strip() for a in r.aliases)),
                    day=r.day,
                    evening=r.evening,
                )
                for r in dep.roles
            )
        return cls.from_roles(roles, source=str(path))

    @classmethod
    def from_roles(cls, roles: list[Role], *, source: str = "roles") -> OrgRoles:
        """Build from roles; ids and aliases must be unique, aliases non-empty and not common
        words (aliases are normalised)."""
        ids = [r.id for r in roles]
        if len(set(ids)) != len(ids):
            raise PrivacyConfigError(f"{source}: duplicate role id")
        aliases = [a for r in roles for a in r.aliases]
        if len(set(aliases)) != len(aliases):
            raise PrivacyConfigError(f"{source}: an alias names two roles")
        # An empty alias or a function word (« de », « la ») would match almost anywhere.
        if any(not a or a in FUNCTION_WORDS for a in aliases):
            raise PrivacyConfigError(f"{source}: an alias is empty or a common word")
        return cls(tuple(roles))

    @cached_property
    def _alias_index(self) -> tuple[re.Pattern[str], dict[str, Role]]:
        by_alias = {a: r for r in self.roles for a in r.aliases}
        # Longest alias first, so « inspecteur en soudage » wins over « inspecteur ».
        ordered = sorted(by_alias, key=len, reverse=True)
        pattern = re.compile(
            r"(?<![\w-])(" + "|".join(re.escape(a) for a in ordered) + r")(?![\w-])"
        )
        return pattern, by_alias

    @property
    def total(self) -> int:
        """Headcount of the company."""
        return sum(r.total for r in self.roles)

    def get(self, role_id: str) -> Role:
        """The role with this id (KeyError when unknown)."""
        for role in self.roles:
            if role.id == role_id:
                return role
        raise KeyError(role_id)

    def is_role(self, text: str) -> bool:
        """True when ``text`` is exactly one of the aliases (normalised)."""
        return normalize(text).strip() in self._alias_index[1]

    def mentions(self, normalized: str) -> list[RoleMention]:
        """Role mentions in an already normalised text, each with its shift, if any."""
        pattern, by_alias = self._alias_index
        found: list[RoleMention] = []
        for match in pattern.finditer(normalized):
            shift_match = _SHIFT_RE.match(normalized, match.end())
            shift = _SHIFT_NAMES[shift_match.group(1)] if shift_match else None
            end = shift_match.end() if shift_match else match.end()
            found.append(RoleMention(by_alias[match.group(1)], match.start(), end, shift))
        return found

    def small_roles(self, k: int) -> list[Role]:
        """Roles whose total headcount is below ``k``."""
        return [r for r in self.roles if r.total < k]
