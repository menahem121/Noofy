import json
from pathlib import Path

from app.workflows.package import WorkflowPackage


class WorkflowPackageLoader:
    def __init__(self, packages_dir: Path) -> None:
        self.packages_dir = packages_dir

    def list_packages(self) -> list[WorkflowPackage]:
        packages: list[WorkflowPackage] = []
        if not self.packages_dir.exists():
            return packages

        for package_file in sorted(self.packages_dir.glob("*/package.json")):
            packages.append(self._load_file(package_file))
        return packages

    def get_package(self, workflow_id: str) -> WorkflowPackage:
        for package in self.list_packages():
            if package.metadata.id == workflow_id:
                return package
        raise KeyError(f"Unknown workflow package: {workflow_id}")

    def _load_file(self, package_file: Path) -> WorkflowPackage:
        with package_file.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return WorkflowPackage.model_validate(data)
