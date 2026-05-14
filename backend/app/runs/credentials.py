from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from app.diagnostics import register_secret
from app.settings.api_keys import ApiKeyProvider, PROVIDER_LABELS
from app.workflows.package import DashboardControl, WorkflowPackage

SUPPORTED_COMFYUI_EXTRA_DATA_INJECTIONS = {
    ("comfy_org", "api_key_comfy_org"),
}
CREDENTIAL_PLAN_OPTION = "_credential_injection_plan"


class CredentialInjectionPlan(BaseModel):
    extra_data: dict[str, str] = Field(default_factory=dict)


class CredentialRequirementError(ValueError):
    pass


CredentialResolver = Callable[[ApiKeyProvider], str | None]


def credential_refs_from_inputs(inputs: dict[str, Any]) -> dict[str, str]:
    refs: dict[str, str] = {}
    for input_id, value in inputs.items():
        if isinstance(value, dict) and value.get("kind") == "api_key_ref":
            secret_ref = value.get("secret_ref")
            if isinstance(secret_ref, str):
                refs[input_id] = secret_ref
    return refs


def build_credential_injection_plan(
    *,
    package: WorkflowPackage,
    submitted_inputs: dict[str, Any],
    credential_resolver: CredentialResolver | None,
) -> CredentialInjectionPlan:
    extra_data: dict[str, str] = {}
    submitted_refs = credential_refs_from_inputs(submitted_inputs)

    for control in _credential_controls(package):
        provider = _provider(control)
        secret_ref = _secret_ref(control)
        strategy = control.injection_strategy
        field = strategy.field if strategy is not None else None
        strategy_supported = (
            strategy is not None
            and strategy.kind == "comfyui_extra_data"
            and (provider, field) in SUPPORTED_COMFYUI_EXTRA_DATA_INJECTIONS
        )
        if not strategy_supported:
            raise CredentialRequirementError(
                "This workflow uses an unsupported credential injection strategy. "
                "Only official ComfyUI Partner/API-node credentials are supported."
            )
        if secret_ref != f"api-key:{provider}":
            raise CredentialRequirementError("This workflow has an invalid credential reference.")

        input_id = control.input_id or control.id
        submitted_value = submitted_inputs.get(input_id)
        if submitted_value is not None and not (
            isinstance(submitted_value, dict)
            and submitted_value.get("kind") == "api_key_ref"
        ):
            raise CredentialRequirementError(
                f"{_provider_label(provider)} must be saved before running this workflow."
            )
        submitted_ref = submitted_refs.get(input_id)
        if submitted_ref is not None and submitted_ref != secret_ref:
            raise CredentialRequirementError("Submitted credential reference does not match the saved dashboard.")

        if credential_resolver is None:
            raise CredentialRequirementError(f"{_provider_label(provider)} is not configured.")
        secret = credential_resolver(provider)
        if not secret:
            if control.required:
                raise CredentialRequirementError(f"{_provider_label(provider)} is required to run this workflow.")
            continue
        if field is None:
            raise CredentialRequirementError("API credential is missing an injection field.")
        register_secret(secret)
        extra_data[field] = secret

    return CredentialInjectionPlan(extra_data=extra_data)


def strip_credential_inputs(
    package: WorkflowPackage,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    credential_ids = credential_input_ids(package)
    if not credential_ids:
        return dict(inputs)
    return {key: value for key, value in inputs.items() if key not in credential_ids}


def credential_input_ids(package: WorkflowPackage) -> set[str]:
    return {control.input_id or control.id for control in _credential_controls(package)}


def plan_from_options(options: dict[str, Any]) -> CredentialInjectionPlan:
    raw = options.get(CREDENTIAL_PLAN_OPTION)
    if isinstance(raw, CredentialInjectionPlan):
        return raw
    if isinstance(raw, dict):
        return CredentialInjectionPlan.model_validate(raw)
    return CredentialInjectionPlan()


def options_with_credential_plan(
    options: dict[str, Any],
    plan: CredentialInjectionPlan,
) -> dict[str, Any]:
    if not plan.extra_data:
        return dict(options)
    return {
        **options,
        CREDENTIAL_PLAN_OPTION: plan.model_dump(mode="json"),
    }


def safe_options_for_storage(options: dict[str, Any]) -> dict[str, Any]:
    safe = dict(options)
    safe.pop(CREDENTIAL_PLAN_OPTION, None)
    return safe


def package_requires_credential_injection(package: WorkflowPackage) -> bool:
    return any(True for _ in _credential_controls(package))


def _credential_controls(package: WorkflowPackage):
    for section in package.dashboard.sections:
        for control in section.controls:
            if control.type == "api_credential":
                yield control


def _provider(control: DashboardControl) -> ApiKeyProvider:
    if control.provider != "comfy_org":
        raise CredentialRequirementError("Unsupported API credential provider.")
    return "comfy_org"


def _secret_ref(control: DashboardControl) -> str:
    if not control.secret_ref:
        raise CredentialRequirementError("API credential is missing a secret reference.")
    return control.secret_ref


def _provider_label(provider: ApiKeyProvider) -> str:
    return PROVIDER_LABELS.get(provider, provider)
