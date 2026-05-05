import io
import csv as csv_module
import openpyxl
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.inventory_service import get_filtered_wips

EXPORT_COLUMNS = [
    "재고사업장", "관리사업장", "위치", "품목계정", "등급", "품명", "도료",
    "재질", "가로", "세로", "두께", "폭", "호칭경", "길이",
    "재고수량", "재고중량", "예약수량", "예약중량", "사용가능수량", "사용가능중량",
    "제조사", "소재번호", "입고일", "보관일수", "Bundle번호", "LOT번호",
    "재고구분", "단위중량", "단위", "창고", "적재위치", "소유주", "용도", "상태",
    "주문투입년월", "비고", "수주거래처", "현장", "상태일", "품목코드",
    "되감기", "도유", "아연함유량", "후처리", "표면", "계근", "LOT순번", "Heat번호", "PVC"
]


def _build_row(wip) -> dict:
    loc_name = wip.location.loc_name if wip.location else ""
    stack = wip.stack_level if wip.stack_level is not None else ""
    위치 = f"{loc_name}-{stack}" if loc_name and stack != "" else ""
    qr_code = wip.qr.qr_code if wip.qr else ""

    return {
        "재고사업장":    "(주)유석철강 청주지점",
        "관리사업장":    "(주)유석철강 청주지점",
        "위치":         위치,
        "품목계정":     "재공품",
        "등급":         "정상품",
        "품명":         "후판",
        "도료":         "없음",
        "재질":         wip.material or "",
        "가로":         0,
        "세로":         0,
        "두께":         wip.thickness or "",
        "폭":           wip.width or "",
        "호칭경":       "",
        "길이":         wip.length or "",
        "재고수량":     1,
        "재고중량":     wip.weight or "",
        "예약수량":     0,
        "예약중량":     0,
        "사용가능수량": 1,
        "사용가능중량": wip.weight or "",
        "제조사":       wip.manufacturer or "",
        "소재번호":     qr_code,
        "입고일":       "",
        "보관일수":     "",
        "Bundle번호":   "",
        "LOT번호":      "",
        "재고구분":     "자사",
        "단위중량":     wip.weight or "",
        "단위":         "EA",
        "창고":         "Laser 창고(오창)",
        "적재위치":     "공통",
        "소유주":       "유석철강(청주지점)",
        "용도":         "없음",
        "상태":         "정상",
        "주문투입년월": "",
        "비고":         "",
        "수주거래처":   "",
        "현장":         "",
        "상태일":       "",
        "품목코드":     "",
        "되감기":       "",
        "도유":         "",
        "아연함유량":   "",
        "후처리":       "",
        "표면":         "",
        "계근":         "",
        "LOT순번":      "",
        "Heat번호":     "",
        "PVC":          "",
    }


async def export_wip_xlsx(db: AsyncSession) -> io.BytesIO:
    results = await get_filtered_wips(db=db)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "리스트"
    ws.append(EXPORT_COLUMNS)
    for wip in results:
        row = _build_row(wip)
        ws.append([row[col] for col in EXPORT_COLUMNS])
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer


async def export_wip_csv(db: AsyncSession) -> bytes:
    results = await get_filtered_wips(db=db)
    output = io.StringIO()
    writer = csv_module.DictWriter(output, fieldnames=EXPORT_COLUMNS)
    writer.writeheader()
    for wip in results:
        writer.writerow(_build_row(wip))
    return output.getvalue().encode("utf-8-sig")