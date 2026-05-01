from enum import StrEnum


class ModelVerificationLevel(StrEnum):
    SHA256_SIZE = "sha256_size"
    FILENAME_SIZE = "filename_size"
    FILENAME_ONLY = "filename_only"


class AssetOwnership(StrEnum):
    NOOFY_DOWNLOADED = "noofy_downloaded"
    NOOFY_IMPORTED = "noofy_imported"
    USER_LOCAL = "user_local"
    EXTERNAL_REFERENCE = "external_reference"
