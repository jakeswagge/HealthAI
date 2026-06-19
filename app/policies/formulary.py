"""Normalized formulary-policy rules derived from Da Vinci resources."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Iterable

from app.importers.davinci_formulary import FormularyCatalog, FormularyItem


@dataclass(frozen=True)
class FormularyPolicyRule:
    """One normalized rule for a specific drug or drug family."""

    payer_id: str
    guideline_pack: str
    drug_key: str
    prior_authorization_required: bool | None = None
    step_therapy_required: bool | None = None
    quantity_limit: bool | None = None
    step_therapy_new_starts_only: bool | None = None
    source_resource_id: str | None = None
    source: str | None = None


@dataclass
class FormularyPolicyIndex:
    """Queryable formulary-policy snapshot for review-time overrides."""

    rules: list[FormularyPolicyRule] = field(default_factory=list)

    def rule_for(
        self,
        drug_key: str,
        *,
        payer_id: str | None = None,
    ) -> FormularyPolicyRule | None:
        payer = (payer_id or "").strip().upper()
        fallback: FormularyPolicyRule | None = None
        for rule in self.rules:
            if not _keys_match(rule.drug_key, drug_key):
                continue
            if payer and rule.payer_id.upper() == payer:
                return rule
            if not payer:
                return rule
            if rule.payer_id.upper() == "DEFAULT" and fallback is None:
                fallback = rule
                continue
        return fallback

    def rule_for_any(
        self,
        drug_keys: Iterable[str],
        *,
        payer_id: str | None = None,
    ) -> FormularyPolicyRule | None:
        for key in drug_keys:
            rule = self.rule_for(key, payer_id=payer_id)
            if rule is not None:
                return rule
        return None

    def step_therapy_required_for(
        self,
        drug_key: str,
        *,
        payer_id: str | None = None,
    ) -> bool | None:
        rule = self.rule_for(drug_key, payer_id=payer_id)
        return None if rule is None else rule.step_therapy_required

    @classmethod
    def from_catalog(
        cls,
        catalog: FormularyCatalog,
        *,
        payer_id: str = "DEFAULT",
        guideline_pack: str = "DEFAULT",
    ) -> "FormularyPolicyIndex":
        rules: list[FormularyPolicyRule] = []
        for item in catalog.items:
            drug_key = _drug_key_for_item(item, catalog)
            if not drug_key:
                continue
            rules.append(
                FormularyPolicyRule(
                    payer_id=payer_id,
                    guideline_pack=guideline_pack,
                    drug_key=drug_key,
                    prior_authorization_required=item.prior_authorization_required,
                    step_therapy_required=item.step_therapy_required,
                    quantity_limit=item.quantity_limit,
                    step_therapy_new_starts_only=item.step_therapy_new_starts_only,
                    source_resource_id=item.source_resource_id,
                    source="davinci-formulary",
                )
            )
        return cls(rules=rules)

    @classmethod
    def from_items(
        cls,
        items: Iterable[dict[str, Any]],
        *,
        payer_id: str = "DEFAULT",
        guideline_pack: str = "DEFAULT",
    ) -> "FormularyPolicyIndex":
        catalog = FormularyCatalog(items=[FormularyItem.model_validate(item) for item in items])
        return cls.from_catalog(catalog, payer_id=payer_id, guideline_pack=guideline_pack)


def _drug_key_for_item(item: FormularyItem, catalog: FormularyCatalog) -> str:
    if item.drug_reference:
        ref = item.drug_reference.rsplit("/", 1)[-1]
        for drug in catalog.drugs:
            if drug.source_resource_id == ref or drug.drug_id == ref:
                return drug.display or drug.code or drug.drug_id
        return ref
    for drug in catalog.drugs:
        if drug.source_resource_id and drug.source_resource_id == item.drug_reference:
            return drug.display or drug.code or drug.drug_id
    return item.item_id or ""


def _normalize_key(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


_DRUG_ALIASES = {
    "humira": {"adalimumab"},
    "adalimumab": {"humira"},
    "enbrel": {"etanercept"},
    "etanercept": {"enbrel"},
    "remicade": {"infliximab"},
    "infliximab": {"remicade"},
    "stelara": {"ustekinumab"},
    "ustekinumab": {"stelara"},
    "cosentyx": {"secukinumab"},
    "secukinumab": {"cosentyx"},
    "xeljanz": {"tofacitinib"},
    "tofacitinib": {"xeljanz"},
}


def _keys_match(left: str, right: str) -> bool:
    left_candidates = _candidate_keys(left)
    right_candidates = _candidate_keys(right)
    return bool(left_candidates & right_candidates)


def _candidate_keys(value: str) -> set[str]:
    normalized = _normalize_key(value)
    if not normalized:
        return set()

    candidates = {normalized}
    for token in re.findall(r"\[([^\]]+)\]|\(([^)]+)\)", value or ""):
        candidates.update(_candidate_keys(next(part for part in token if part)))

    words = set(re.findall(r"[a-z0-9]+", normalized))
    for key, aliases in _DRUG_ALIASES.items():
        if key in words or key in normalized:
            candidates.add(key)
            candidates.update(aliases)
        elif any(alias in words or alias in normalized for alias in aliases):
            candidates.add(key)
            candidates.update(aliases)

    return {item for item in candidates if item}
