# tests/test_wips.py
"""
잔재 재고 API 테스트
  - POST  /api/steelWip            (잔재 등록)
  - GET   /api/steelWip/{wip_id}   (단건 조회)
  - GET   /api/steelWip            (목록 조회 / 다중 필터링)
  - PATCH /api/steelWip/{wip_id}   (상태·위치 업데이트)
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Locations, SteelWip, QrCodes


# ══════════════════════════════════════════════════════════════════════
# 테스트 데이터 헬퍼
# ══════════════════════════════════════════════════════════════════════

async def make_location(db: AsyncSession, name: str = "A-1") -> Locations:
    loc = Locations(loc_name=name, loc_can_stock=1, loc_stack_height=10)
    db.add(loc)
    await db.flush()
    return loc


async def make_qr(db: AsyncSession, code: str = "QR-001") -> QrCodes:
    qr = QrCodes(qr_code=code)
    db.add(qr)
    await db.flush()
    return qr


async def make_wip(
    db: AsyncSession,
    location_id: int | None = None,
    qr_id: int | None = None,
    material: str = "SM355A",
    thickness: float = 20.0,
    width: float = 2438.0,
    length: float = 6096.0,
    status: str = "IN_STOCK",
    manufacturer: str = "POSCO",
) -> SteelWip:
    wip = SteelWip(
        status=status,
        material=material,
        thickness=thickness,
        width=width,
        length=length,
        weight=100.0,
        manufacturer=manufacturer,
        location_id=location_id,
        qr_id=qr_id,
    )
    db.add(wip)
    await db.flush()
    return wip


# ══════════════════════════════════════════════════════════════════════
# POST /api/steelWip — 잔재 등록
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_create_wip_success(client: AsyncClient, db_session: AsyncSession):
    """잔재 등록 성공 — 201 + data 반환"""
    loc = await make_location(db_session)
    await db_session.commit()

    payload = {
        "status": "IN_STOCK",
        "manufacturer": "POSCO",
        "material": "SM355A",
        "thickness": 20.0,
        "width": 2438.0,
        "length": 6096.0,
        "weight": 100.0,
        "location_id": loc.id,
    }
    response = await client.post("/api/steelWip", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == 201
    assert body["message"] == "재공품 등록 성공"
    assert body["data"]["material"] == "SM355A"
    assert body["data"]["manufacturer"] == "POSCO"
    assert body["data"]["location_id"] == loc.id


@pytest.mark.asyncio
async def test_create_wip_without_location(client: AsyncClient, db_session: AsyncSession):
    """위치 없이도 등록 가능 (location_id Optional)"""
    payload = {
        "status": "REGISTERED",
        "manufacturer": "HYUNDAI",
        "material": "SS275",
        "thickness": 16.0,
        "width": 1500.0,
        "length": 3000.0,
        "weight": 60.0,
    }
    response = await client.post("/api/steelWip", json=payload)

    assert response.status_code == 200
    assert response.json()["status"] == 201
    assert response.json()["data"]["location_id"] is None


@pytest.mark.asyncio
async def test_create_wip_duplicate_qr(client: AsyncClient, db_session: AsyncSession):
    """이미 사용 중인 QR ID로 등록 시 400"""
    qr = await make_qr(db_session, "QR-DUP")
    await make_wip(db_session, qr_id=qr.id)
    await db_session.commit()

    payload = {
        "status": "IN_STOCK",
        "manufacturer": "POSCO",
        "material": "SS275",
        "thickness": 12.0,
        "width": 2000.0,
        "length": 4000.0,
        "weight": 80.0,
        "qr_id": qr.id,  # 이미 다른 WIP에 연결된 QR
    }
    response = await client.post("/api/steelWip", json=payload)

    assert response.status_code == 400
    assert "이미 등록된 QR 코드" in response.json()["message"]


# ══════════════════════════════════════════════════════════════════════
# GET /api/steelWip/{wip_id} — 단건 조회
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_get_wip_success(client: AsyncClient, db_session: AsyncSession):
    """존재하는 WIP 단건 조회 성공"""
    loc = await make_location(db_session, "B-1")
    wip = await make_wip(db_session, location_id=loc.id, material="SS275")
    await db_session.commit()

    response = await client.get(f"/api/steelWip/{wip.id}")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["id"] == wip.id
    assert data["material"] == "SS275"
    assert data["location_id"] == loc.id


@pytest.mark.asyncio
async def test_get_wip_not_found(client: AsyncClient, db_session: AsyncSession):
    """존재하지 않는 WIP ID 조회 → 404"""
    response = await client.get("/api/steelWip/99999")

    assert response.status_code == 404
    assert "찾을 수 없습니다" in response.json()["message"]


# ══════════════════════════════════════════════════════════════════════
# GET /api/steelWip — 목록 조회 / 다중 필터링
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_get_inventory_all(client: AsyncClient, db_session: AsyncSession):
    """필터 없이 전체 목록 반환"""
    await make_wip(db_session, material="SM355A")
    await make_wip(db_session, material="SS275")
    await db_session.commit()

    response = await client.get("/api/steelWip")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == 200
    assert len(body["data"]) == 2


@pytest.mark.asyncio
async def test_get_inventory_empty(client: AsyncClient, db_session: AsyncSession):
    """데이터 없을 때 빈 배열 반환"""
    response = await client.get("/api/steelWip")

    assert response.status_code == 200
    assert response.json()["data"] == []


@pytest.mark.asyncio
async def test_get_inventory_filter_material(client: AsyncClient, db_session: AsyncSession):
    """material 필터 — 일치 항목만 반환"""
    await make_wip(db_session, material="SM355A")
    await make_wip(db_session, material="SS275")
    await db_session.commit()

    response = await client.get("/api/steelWip", params={"material": "SM355A"})

    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["material"] == "SM355A"


@pytest.mark.asyncio
async def test_get_inventory_filter_manufacturer(client: AsyncClient, db_session: AsyncSession):
    """manufacturer 필터"""
    await make_wip(db_session, manufacturer="POSCO")
    await make_wip(db_session, manufacturer="HYUNDAI")
    await db_session.commit()

    response = await client.get("/api/steelWip", params={"manufacturer": "POSCO"})

    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["manufacturer"] == "POSCO"


@pytest.mark.asyncio
async def test_get_inventory_filter_thickness(client: AsyncClient, db_session: AsyncSession):
    """thickness 필터 — 정확히 일치하는 항목만 반환"""
    await make_wip(db_session, thickness=20.0)
    await make_wip(db_session, thickness=12.0)
    await db_session.commit()

    response = await client.get("/api/steelWip", params={"thickness": 20.0})

    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["thickness"] == 20.0


@pytest.mark.asyncio
async def test_get_inventory_filter_min_width(client: AsyncClient, db_session: AsyncSession):
    """minWidth 필터 — 지정값 이상인 항목만 반환"""
    await make_wip(db_session, width=2438.0)   # 범위 안
    await make_wip(db_session, width=500.0)    # 범위 밖
    await db_session.commit()

    response = await client.get("/api/steelWip", params={"minWidth": 1000.0})

    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["width"] == 2438.0


@pytest.mark.asyncio
async def test_get_inventory_filter_max_width(client: AsyncClient, db_session: AsyncSession):
    """maxWidth 필터 — 지정값 이하인 항목만 반환"""
    await make_wip(db_session, width=2438.0)   # 범위 밖
    await make_wip(db_session, width=500.0)    # 범위 안
    await db_session.commit()

    response = await client.get("/api/steelWip", params={"maxWidth": 1000.0})

    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["width"] == 500.0


@pytest.mark.asyncio
async def test_get_inventory_filter_min_length(client: AsyncClient, db_session: AsyncSession):
    """minLength 필터"""
    await make_wip(db_session, length=6096.0)
    await make_wip(db_session, length=1000.0)
    await db_session.commit()

    response = await client.get("/api/steelWip", params={"minLength": 2000.0})

    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["length"] == 6096.0


@pytest.mark.asyncio
async def test_get_inventory_filter_max_length(client: AsyncClient, db_session: AsyncSession):
    """maxLength 필터"""
    await make_wip(db_session, length=6096.0)
    await make_wip(db_session, length=1000.0)
    await db_session.commit()

    response = await client.get("/api/steelWip", params={"maxLength": 2000.0})

    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["length"] == 1000.0


@pytest.mark.asyncio
async def test_get_inventory_filter_width_range(client: AsyncClient, db_session: AsyncSession):
    """minWidth + maxWidth 복합 범위 필터"""
    await make_wip(db_session, width=500.0)   # 범위 밖 (작음)
    await make_wip(db_session, width=1500.0)  # 범위 안
    await make_wip(db_session, width=3000.0)  # 범위 밖 (큼)
    await db_session.commit()

    response = await client.get("/api/steelWip", params={"minWidth": 1000.0, "maxWidth": 2000.0})

    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["width"] == 1500.0


@pytest.mark.asyncio
async def test_get_inventory_filter_qr(client: AsyncClient, db_session: AsyncSession):
    """QR 코드 값으로 필터링"""
    qr = await make_qr(db_session, "QR-FILTER-TARGET")
    await make_wip(db_session, qr_id=qr.id, material="SM355A")
    await make_wip(db_session, material="SS275")  # QR 없음
    await db_session.commit()

    response = await client.get("/api/steelWip", params={"qr": "QR-FILTER-TARGET"})

    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["qr_code_value"] == "QR-FILTER-TARGET"
    assert data[0]["material"] == "SM355A"


@pytest.mark.asyncio
async def test_get_inventory_combined_filters(client: AsyncClient, db_session: AsyncSession):
    """material + thickness 복합 필터"""
    await make_wip(db_session, material="SM355A", thickness=20.0)  # 일치
    await make_wip(db_session, material="SM355A", thickness=12.0)  # thickness 불일치
    await make_wip(db_session, material="SS275",  thickness=20.0)  # material 불일치
    await db_session.commit()

    response = await client.get("/api/steelWip", params={"material": "SM355A", "thickness": 20.0})

    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["material"] == "SM355A"
    assert data[0]["thickness"] == 20.0


# ══════════════════════════════════════════════════════════════════════
# PATCH /api/steelWip/{wip_id} — 상태·위치 업데이트
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_update_wip_status(client: AsyncClient, db_session: AsyncSession):
    """상태(status) 업데이트 성공"""
    wip = await make_wip(db_session, status="IN_STOCK")
    await db_session.commit()

    response = await client.patch(f"/api/steelWip/{wip.id}", json={"status": "RESERVATED"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "RESERVATED"


@pytest.mark.asyncio
async def test_update_wip_location(client: AsyncClient, db_session: AsyncSession):
    """위치(location_id) + 적재 층(stack_level) 업데이트 성공"""
    loc = await make_location(db_session, "C-3")
    wip = await make_wip(db_session)
    await db_session.commit()

    response = await client.patch(
        f"/api/steelWip/{wip.id}",
        json={"location_id": loc.id, "stack_level": 3}
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["location_id"] == loc.id
    assert data["stack_level"] == 3


@pytest.mark.asyncio
async def test_update_wip_partial(client: AsyncClient, db_session: AsyncSession):
    """일부 필드만 수정해도 나머지 필드는 변하지 않는다 (PATCH 특성)"""
    loc = await make_location(db_session, "D-1")
    wip = await make_wip(db_session, location_id=loc.id, status="IN_STOCK")
    await db_session.commit()

    # status만 변경
    response = await client.patch(f"/api/steelWip/{wip.id}", json={"status": "CONSUMED"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "CONSUMED"
    assert data["location_id"] == loc.id  # 기존 위치 유지


@pytest.mark.asyncio
async def test_update_wip_not_found(client: AsyncClient, db_session: AsyncSession):
    """존재하지 않는 WIP 업데이트 → 404"""
    response = await client.patch("/api/steelWip/99999", json={"status": "CONSUMED"})

    assert response.status_code == 404
    assert "찾을 수 없습니다" in response.json()["message"]
