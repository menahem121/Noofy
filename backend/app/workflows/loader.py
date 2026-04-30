import json
from pathlib import Path

from app.workflows.package import WorkflowPackage


class WorkflowPackageLoader:
    """Load workflow packages from bundled and user directories.

    Bundled workflows are read-only starter packages shipped with the app.
    User workflows live in the app-data directory. Product behavior must not
    silently let a user package shadow a bundled workflow by matching
    ``metadata.id``. Development tooling can opt into overrides explicitly.
    """

    def __init__(
        self,
        packages_dir: Path,
        user_packages_dir: Path | None = None,
        allow_user_overrides: bool = False,
    ) -> None:
        self.packages_dir = packages_dir
        self.user_packages_dir = user_packages_dir
        self.allow_user_overrides = allow_user_overrides

    def list_packages(self) -> list[WorkflowPackage]:
        by_id: dict[str, WorkflowPackage] = {}

        # Bundled (lower priority)
        for package in self._load_from(self.packages_dir):
            by_id[package.metadata.id] = package

        # User packages cannot shadow bundled packages unless a development
        # caller opts into that behavior explicitly.
        if self.user_packages_dir is not None:
            for package in self._load_from(self.user_packages_dir):
                if package.metadata.id in by_id and not self.allow_user_overrides:
                    continue
                by_id[package.metadata.id] = package

        return sorted(by_id.values(), key=lambda p: p.metadata.id)

    def get_package(self, workflow_id: str) -> WorkflowPackage:
        for package in self.list_packages():
            if package.metadata.id == workflow_id:
                return package
        raise KeyError(f"Unknown workflow package: {workflow_id}")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_from(self, directory: Path) -> list[WorkflowPackage]:
        packages: list[WorkflowPackage] = []
        if not directory.exists():
            return packages
        for package_file in sorted(directory.glob("*/package.json")):
            packages.append(self._load_file(package_file))
        return packages

    def _load_file(self, package_file: Path) -> WorkflowPackage:
        with package_file.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return WorkflowPackage.model_validate(data)
