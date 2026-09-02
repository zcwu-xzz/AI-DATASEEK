from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.domain.models.file import FileInfo
from app.interfaces.api.file_routes import (
    MolecularPreviewRequest,
    prepare_molecular_preview,
)


class _FileService:
    def __init__(self, file_info: FileInfo):
        self.file_info = file_info
        self.calls = []
        self.stream = BytesIO(b"structure")

    async def download_file(self, file_id, user_id):
        self.calls.append((file_id, user_id))
        return self.stream, self.file_info


def _file(filename: str, size: int = 1024) -> FileInfo:
    return FileInfo(
        file_id="file-1",
        filename=filename,
        file_path="/private/storage/tenant/structure.cif",
        content_type="chemical/x-cif",
        size=size,
        upload_date=datetime.now(UTC),
        user_id="user-1",
    )


@pytest.mark.asyncio
async def test_prepare_molecular_preview_authorizes_and_hides_storage_path():
    service = _FileService(_file("/private/storage/tenant/crystal.cif"))

    response = await prepare_molecular_preview(
        MolecularPreviewRequest(file_id="file-1"),
        file_service=service,
        current_user=SimpleNamespace(id="user-1"),
    )

    assert service.calls == [("file-1", "user-1")]
    assert response.data.source_name == "crystal.cif"
    assert response.data.source_format == "cif"
    assert response.data.supports_unit_cell is True
    assert "/private/" not in response.model_dump_json()
    assert service.stream.closed is True


@pytest.mark.asyncio
async def test_prepare_molecular_preview_supports_poscar_without_extension():
    service = _FileService(_file("POSCAR"))

    response = await prepare_molecular_preview(
        MolecularPreviewRequest(file_id="file-1"),
        file_service=service,
        current_user=SimpleNamespace(id="user-1"),
    )

    assert response.data.source_format == "vasp"
    assert response.data.periodic is True


@pytest.mark.asyncio
async def test_prepare_molecular_preview_rejects_unsupported_format_and_closes_stream():
    service = _FileService(_file("notes.txt"))

    with pytest.raises(HTTPException) as error:
        await prepare_molecular_preview(
            MolecularPreviewRequest(file_id="file-1"),
            file_service=service,
            current_user=SimpleNamespace(id="user-1"),
        )

    assert error.value.status_code == 415
    assert service.stream.closed is True


@pytest.mark.asyncio
async def test_prepare_molecular_preview_rejects_oversized_structure():
    service = _FileService(_file("large.sdf", size=51 * 1024 * 1024))

    with pytest.raises(HTTPException) as error:
        await prepare_molecular_preview(
            MolecularPreviewRequest(file_id="file-1"),
            file_service=service,
            current_user=SimpleNamespace(id="user-1"),
        )

    assert error.value.status_code == 413
    assert service.stream.closed is True
