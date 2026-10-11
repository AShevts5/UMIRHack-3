from fastapi import APIRouter, Response

from app import mocks

router = APIRouter(tags=["files"])

@router.get(
    "/files/{request_id}/{page}",
    response_class=Response,
    responses={200: {"content": {"image/svg+xml": {}}}},
    summary="Обработанное изображение страницы",
)
async def get_file(request_id: str, page: str) -> Response:
    return Response(content=mocks.placeholder_page_svg(), media_type="image/svg+xml")