"""DataSeek integration only. S3 protocol traffic goes directly to filesystem-s3."""
from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.application.services.data_center_dataset_service import DataCenterDatasetService
from app.application.services.dataset_s3 import S3Error, issue_credentials
from app.application.services.dataset_s3_files import resolve_file
from app.domain.models.user import User
from app.interfaces.dependencies import get_current_user
from app.interfaces.schemas.base import APIResponse

router = APIRouter(tags=['dataset-s3'])


class DownloadRequest(BaseModel):
    relative_path: str = Field(min_length=1, max_length=4096)


@router.post('/datasets/{dataset_id}/files/s3-download')
async def prepare_download(dataset_id: str, body: DownloadRequest, user: User = Depends(get_current_user)):
    dataset = await DataCenterDatasetService().get_dataset(dataset_id, user_id=user.id)
    try:
        target = resolve_file(dataset, body.relative_path)
        result = await issue_credentials(dataset_id, user.id, body.relative_path, target)
        return Response(APIResponse.success(result).model_dump_json(), media_type='application/json', headers={'Cache-Control': 'no-store'})
    except S3Error as exc:
        return Response(APIResponse(code=exc.status, msg=exc.message).model_dump_json(), status_code=exc.status, media_type='application/json', headers={'Cache-Control': 'no-store'})
