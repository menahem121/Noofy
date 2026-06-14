from __future__ import annotations

import io

import pytest

from app.archive_safety import (
    MaterializedPathIndex,
    PathSafetyError,
    StreamLimitError,
    copy_stream_limited,
    safe_relative_posix_path,
)


@pytest.mark.parametrize(
    "value",
    [
        "../escape.py",
        "/absolute.py",
        "models//node.py",
        "models/./node.py",
        "models\\node.py",
    ],
)
def test_shared_relative_path_policy_rejects_unsafe_paths(value: str) -> None:
    with pytest.raises(PathSafetyError):
        safe_relative_posix_path(value, allow_nested=True)


def test_shared_path_index_rejects_case_and_file_directory_collisions() -> None:
    index = MaterializedPathIndex()
    index.add("models/Foo.py")

    with pytest.raises(PathSafetyError, match="collision"):
        index.add("models/foo.py")

    file_index = MaterializedPathIndex()
    file_index.add("models")

    with pytest.raises(PathSafetyError, match="collision"):
        file_index.add("models/node.py")


def test_shared_stream_copy_enforces_actual_byte_limit() -> None:
    destination = io.BytesIO()

    with pytest.raises(StreamLimitError) as error:
        copy_stream_limited(
            io.BytesIO(b"123456"),
            destination,
            max_bytes=4,
            chunk_bytes=2,
        )

    assert error.value.max_bytes == 4
    assert error.value.copied_bytes == 5
    assert destination.getvalue() == b"1234"
