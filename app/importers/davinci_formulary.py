"""Adapter for HL7 Da Vinci US Drug Formulary FHIR resources."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, Field


FORMULARY_REFERENCE_URL = "usdf-FormularyReference-extension"
AVAILABILITY_STATUS_URL = "usdf-AvailabilityStatus-extension"
PHARMACY_BENEFIT_TYPE_URL = "usdf-PharmacyBenefitType-extension"
DRUG_TIER_URL = "usdf-DrugTierID-extension"
AVAILABILITY_PERIOD_URL = "usdf-AvailabilityPeriod-extension"
PRIOR_AUTH_URL = "usdf-PriorAuthorization-extension"
STEP_THERAPY_URL = "usdf-StepTherapyLimit-extension"
STEP_THERAPY_NEW_STARTS_URL = "usdf-StepTherapyLimitNewStartsOnly-extension"
QUANTITY_LIMIT_URL = "usdf-QuantityLimit-extension"
SUPPORTED_RESOURCE_TYPES = {"Basic", "InsurancePlan", "MedicationKnowledge"}


class FormularyDrug(BaseModel):
    """Normalized FHIR MedicationKnowledge formulary drug."""

    drug_id: str
    code: str | None = None
    system: str | None = None
    display: str | None = None
    status: str | None = None
    source_resource_id: str | None = None


class FormularyPlan(BaseModel):
    """Normalized FHIR InsurancePlan."""

    plan_id: str
    name: str | None = None
    status: str | None = None
    plan_type_codes: list[str] = Field(default_factory=list)
    source_resource_id: str | None = None


class FormularyItem(BaseModel):
    """Normalized FHIR Basic/usdf-FormularyItem."""

    item_id: str
    formulary_id: str | None = None
    drug_reference: str | None = None
    availability_status: str | None = None
    pharmacy_benefit_types: list[str] = Field(default_factory=list)
    drug_tier: str | None = None
    availability_start: str | None = None
    availability_end: str | None = None
    prior_authorization_required: bool | None = None
    step_therapy_required: bool | None = None
    step_therapy_new_starts_only: bool | None = None
    quantity_limit: bool | None = None
    source_resource_id: str | None = None


class FormularyCatalog(BaseModel):
    """Collection of normalized Da Vinci formulary resources."""

    plans: list[FormularyPlan] = Field(default_factory=list)
    drugs: list[FormularyDrug] = Field(default_factory=list)
    items: list[FormularyItem] = Field(default_factory=list)

    def item_for_drug(self, drug_reference_or_id: str) -> list[FormularyItem]:
        needle = drug_reference_or_id.strip()
        short = needle.rsplit("/", 1)[-1]
        return [
            item
            for item in self.items
            if item.drug_reference in {needle, short, f"MedicationKnowledge/{short}"}
        ]


class DaVinciFormularyAdapter:
    """Parse Da Vinci formulary FHIR JSON, bundles, directories, or endpoints."""

    def from_directory(self, directory: str | Path) -> FormularyCatalog:
        resources: list[dict[str, Any]] = []
        root = Path(directory)
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix == ".json":
                resources.extend(
                    self._resources_from_json(
                        json.loads(path.read_text(encoding="utf-8"))
                    )
                )
            elif suffix == ".ndjson":
                if path.stem not in SUPPORTED_RESOURCE_TYPES:
                    continue
                with path.open(encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if line:
                            resources.extend(
                                self._resources_from_json(json.loads(line))
                            )
        return self.from_resources(resources)

    def from_ndjson(self, path: str | Path) -> FormularyCatalog:
        resources = []
        with Path(path).open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    resources.append(json.loads(line))
        return self.from_resources(resources)

    def from_bundle(self, bundle: dict[str, Any]) -> FormularyCatalog:
        return self.from_resources(self._resources_from_json(bundle))

    def from_resources(self, resources: Iterable[dict[str, Any]]) -> FormularyCatalog:
        plans: list[FormularyPlan] = []
        drugs: list[FormularyDrug] = []
        items: list[FormularyItem] = []
        for resource in resources:
            parsed = self.parse_resource(resource)
            if isinstance(parsed, FormularyPlan):
                plans.append(parsed)
            elif isinstance(parsed, FormularyDrug):
                drugs.append(parsed)
            elif isinstance(parsed, FormularyItem):
                items.append(parsed)
        return FormularyCatalog(plans=plans, drugs=drugs, items=items)

    def fetch_search(
        self,
        base_url: str,
        resource_type: str,
        params: dict[str, str] | None = None,
        *,
        timeout_seconds: int = 30,
    ) -> FormularyCatalog:
        """Fetch one FHIR search result bundle and parse its resources."""
        query = urllib.parse.urlencode(params or {})
        url = f"{base_url.rstrip('/')}/{resource_type}"
        if query:
            url = f"{url}?{query}"
        request = urllib.request.Request(url, headers={"Accept": "application/fhir+json"})
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return self.from_bundle(payload)

    def fetch_catalog(
        self,
        base_url: str,
        resource_types: Iterable[str] | None = None,
        *,
        queries_by_type: dict[str, dict[str, str]] | None = None,
        timeout_seconds: int = 30,
    ) -> FormularyCatalog:
        """Fetch and merge multiple FHIR search endpoints into one catalog."""

        merged_plans: dict[str, FormularyPlan] = {}
        merged_drugs: dict[str, FormularyDrug] = {}
        merged_items: dict[str, FormularyItem] = {}
        types = list(resource_types or ("InsurancePlan", "MedicationKnowledge", "Basic"))
        queries_by_type = queries_by_type or {}

        for resource_type in types:
            catalog = self.fetch_search(
                base_url,
                resource_type,
                params=queries_by_type.get(resource_type),
                timeout_seconds=timeout_seconds,
            )
            for plan in catalog.plans:
                merged_plans.setdefault(plan.plan_id or plan.source_resource_id or "", plan)
            for drug in catalog.drugs:
                merged_drugs.setdefault(drug.drug_id or drug.source_resource_id or "", drug)
            for item in catalog.items:
                merged_items.setdefault(item.item_id or item.source_resource_id or "", item)

        return FormularyCatalog(
            plans=list(merged_plans.values()),
            drugs=list(merged_drugs.values()),
            items=list(merged_items.values()),
        )

    def parse_resource(self, resource: dict[str, Any]):
        resource_type = resource.get("resourceType")
        if resource_type == "Basic" and self._is_formulary_item(resource):
            return self._parse_item(resource)
        if resource_type == "MedicationKnowledge":
            return self._parse_drug(resource)
        if resource_type == "InsurancePlan":
            return self._parse_plan(resource)
        return None

    @staticmethod
    def _resources_from_json(payload: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(payload, dict) or "resourceType" not in payload:
            return []
        if payload.get("resourceType") == "Bundle":
            return [
                entry["resource"]
                for entry in payload.get("entry", [])
                if isinstance(entry, dict) and isinstance(entry.get("resource"), dict)
                and entry["resource"].get("resourceType") in SUPPORTED_RESOURCE_TYPES
            ]
        if payload.get("resourceType") not in SUPPORTED_RESOURCE_TYPES:
            return []
        return [payload]

    @staticmethod
    def _is_formulary_item(resource: dict[str, Any]) -> bool:
        coding = (resource.get("code") or {}).get("coding") or []
        return any(c.get("code") == "formulary-item" for c in coding)

    def _parse_item(self, resource: dict[str, Any]) -> FormularyItem:
        extensions = resource.get("extension") or []
        period = self._extension_value(extensions, AVAILABILITY_PERIOD_URL, "valuePeriod") or {}
        return FormularyItem(
            item_id=resource.get("id") or "",
            formulary_id=self._reference_tail(
                self._extension_reference(extensions, FORMULARY_REFERENCE_URL)
            ),
            drug_reference=self._reference_tail((resource.get("subject") or {}).get("reference")),
            availability_status=self._extension_value(
                extensions, AVAILABILITY_STATUS_URL, "valueCode"
            ),
            pharmacy_benefit_types=self._extension_code_list(
                extensions, PHARMACY_BENEFIT_TYPE_URL
            ),
            drug_tier=self._first_extension_code(extensions, DRUG_TIER_URL),
            availability_start=period.get("start"),
            availability_end=period.get("end"),
            prior_authorization_required=self._extension_value(
                extensions, PRIOR_AUTH_URL, "valueBoolean"
            ),
            step_therapy_required=self._extension_value(
                extensions, STEP_THERAPY_URL, "valueBoolean"
            ),
            step_therapy_new_starts_only=self._extension_value(
                extensions, STEP_THERAPY_NEW_STARTS_URL, "valueBoolean"
            ),
            quantity_limit=self._extension_value(
                extensions, QUANTITY_LIMIT_URL, "valueBoolean"
            ),
            source_resource_id=resource.get("id"),
        )

    def _parse_drug(self, resource: dict[str, Any]) -> FormularyDrug:
        coding = self._first_coding(resource)
        return FormularyDrug(
            drug_id=resource.get("id") or "",
            code=coding.get("code") if coding else None,
            system=coding.get("system") if coding else None,
            display=coding.get("display") if coding else resource.get("name"),
            status=resource.get("status"),
            source_resource_id=resource.get("id"),
        )

    def _parse_plan(self, resource: dict[str, Any]) -> FormularyPlan:
        type_codes = []
        for type_entry in resource.get("type") or []:
            for coding in type_entry.get("coding") or []:
                code = coding.get("code")
                if code:
                    type_codes.append(code)
        return FormularyPlan(
            plan_id=resource.get("id") or "",
            name=resource.get("name"),
            status=resource.get("status"),
            plan_type_codes=type_codes,
            source_resource_id=resource.get("id"),
        )

    @staticmethod
    def _first_coding(resource: dict[str, Any]) -> dict[str, Any]:
        for field in ("code", "drugCode"):
            coding = (resource.get(field) or {}).get("coding") or []
            if coding:
                return coding[0]
        return {}

    @staticmethod
    def _matches_url(url: str | None, suffix: str) -> bool:
        return bool(url and url.endswith(suffix))

    def _extension_value(
        self,
        extensions: list[dict[str, Any]],
        suffix: str,
        value_key: str,
    ):
        for extension in extensions:
            if self._matches_url(extension.get("url"), suffix) and value_key in extension:
                return extension[value_key]
        return None

    def _extension_reference(
        self,
        extensions: list[dict[str, Any]],
        suffix: str,
    ) -> str | None:
        ref = self._extension_value(extensions, suffix, "valueReference") or {}
        return ref.get("reference")

    def _extension_code_list(
        self,
        extensions: list[dict[str, Any]],
        suffix: str,
    ) -> list[str]:
        codes: list[str] = []
        for extension in extensions:
            if not self._matches_url(extension.get("url"), suffix):
                continue
            concept = extension.get("valueCodeableConcept") or {}
            for coding in concept.get("coding") or []:
                code = coding.get("code")
                if code and code not in codes:
                    codes.append(code)
        return codes

    def _first_extension_code(
        self,
        extensions: list[dict[str, Any]],
        suffix: str,
    ) -> str | None:
        codes = self._extension_code_list(extensions, suffix)
        return codes[0] if codes else None

    @staticmethod
    def _reference_tail(reference: str | None) -> str | None:
        if not reference:
            return None
        return reference.rsplit("/", 1)[-1]
