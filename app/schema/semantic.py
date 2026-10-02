"""Semantic roles: what each column probably means (DATE, CLOSE, COMPANY, ...).

Detection is a hint, never an assumption: when two columns are equally plausible
for a role the role is marked ambiguous and the user must choose.
"""
from dataclasses import dataclass, field

import config
from app.text import name_tokens


@dataclass
class RoleCandidate:
    column: str
    score: float
    reason: str


@dataclass
class SemanticMap:
    candidates: dict = field(default_factory=dict)   # role -> [RoleCandidate] best first

    def of(self, role):
        return self.candidates.get(role, [])

    def tied(self, role):
        """Candidates too close to the best one to choose automatically."""
        cands = self.of(role)
        if not cands:
            return []
        best = cands[0].score
        return [c for c in cands if best - c.score < config.SEMANTIC_AMBIGUITY_MARGIN]

    def resolved(self, role):
        """The column for the role, or None when it is missing or ambiguous."""
        tied = self.tied(role)
        return tied[0].column if len(tied) == 1 else None

    def status(self, role):
        tied = self.tied(role)
        return "missing" if not tied else ("ok" if len(tied) == 1 else "ambiguous")


def detect_roles(profile):
    semantic = SemanticMap()
    for role, spec in config.SEMANTIC_ROLES.items():
        found = []
        for order, col in enumerate(profile.columns):
            if not _kind_matches(spec["kind"], col):
                continue
            score, reason = _score(role, spec["aliases"], col)
            if score >= config.SEMANTIC_MIN_SCORE:
                found.append((score, order, RoleCandidate(col.name, round(score, 2), reason)))
        # Deterministic: best score first, then original column order.
        semantic.candidates[role] = [c for _, _, c in sorted(found, key=lambda t: (-t[0], t[1]))]
    return semantic


def _kind_matches(kind, col):
    if kind == "numeric":
        return col.kind == "numeric"
    if kind == "date":
        return col.is_date
    if kind == "text":
        return col.kind == "text" and not col.is_date
    return True


def _score(role, aliases, col):
    tokens = name_tokens(col.name)
    joined = " ".join(tokens)
    best, reason = 0.0, ""
    for alias in aliases:
        alias_tokens = alias.split()
        if joined == alias:
            score, why = 1.0, f"nombre = '{alias}'"
        elif all(t in tokens for t in alias_tokens):
            score, why = (0.8 if len(alias_tokens) > 1 else 0.7), f"nombre contiene '{alias}'"
        else:
            continue
        if score > best:
            best, reason = score, why
    # Patterns from types and statistics.
    if role == "ID" and col.is_id and best < 0.9:
        best, reason = 0.9, "valores unicos en casi todas las filas"
    if role == "DATE" and best == 0.0:
        best, reason = 0.6, ("tipo fecha" if col.kind == "date" else f"texto con formato {col.date_format}")
    if role in ("COMPANY", "PRODUCT", "CATEGORY", "NAME") and best and not col.is_categorical:
        best -= 0.1   # free text with many values is a weaker match for a grouping role
    return best, reason
