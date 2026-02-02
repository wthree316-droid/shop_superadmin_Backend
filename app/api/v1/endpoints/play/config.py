from typing import List, Optional
from uuid import UUID
from datetime import datetime
from sqlalchemy.orm import Session, joinedload
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, String

from app.api import deps
from app.schemas import (
    LottoCreate, LottoResponse,
    RateProfileCreate, RateProfileResponse,
    CategoryCreate, CategoryResponse,
    BulkRateRequest
)
from app.db.session import get_db
from app.models.lotto import LottoType, RateProfile, LottoCategory
from app.models.user import User, UserRole
from app.core import lotto_cache
from app.core.config import settings

from supabase import create_client, Client

router = APIRouter()

try:
    supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
    BUCKET_NAME = "lotto_images"
except Exception as e:
    print(f"Supabase Init Error: {e}")

DEFAULT_CATEGORIES_CONFIG = [
    {"label": "หวยรัฐบาลไทย", "color": "#EF4444"},
    {"label": "หวยฮานอย", "color": "#F59E0B"},
    {"label": "หวยลาว", "color": "#10B981"},
    {"label": "หวยหุ้น", "color": "#EC4899"},
    {"label": "หวยหุ้นVIP", "color": "#8B5CF6"},
    {"label": "หวยดาวโจนส์", "color": "#F43F5E"},
    {"label": "หวยอื่นๆ", "color": "#3B82F6"},
]

def parse_time(t_str: str):
    if not t_str: return None
    try:
        if len(t_str) == 5: t_str += ":00"
        return datetime.strptime(t_str, "%H:%M:%S").time()
    except ValueError:
        return None

# --- Rate Profiles ---
@router.get("/rates", response_model=List[RateProfileResponse])
def get_rate_profiles(
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    return db.query(RateProfile).filter(
        (RateProfile.shop_id == current_user.shop_id) | (RateProfile.shop_id == None)
    ).all()

@router.post("/rates", response_model=RateProfileResponse)
def create_rate_profile(
    profile_in: RateProfileCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    shop_id = current_user.shop_id if current_user.role == UserRole.admin else None

    new_profile = RateProfile(
        name=profile_in.name, 
        rates=profile_in.rates,
        shop_id=shop_id
    )
    db.add(new_profile)
    db.commit()
    db.refresh(new_profile)
    return new_profile

@router.put("/rates/{profile_id}", response_model=RateProfileResponse)
def update_rate_profile(
    profile_id: UUID,
    profile_in: RateProfileCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    profile = db.query(RateProfile).filter(RateProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Rate profile not found")

    # ถ้าเป็น Admin ร้าน ต้องเช็คว่าเป็นเจ้าของ Profile นี้ไหม
    if current_user.role == UserRole.admin:
        if profile.shop_id != current_user.shop_id:
             raise HTTPException(status_code=403, detail="Access denied")

    profile.name = profile_in.name
    profile.rates = profile_in.rates
    
    db.commit()
    db.refresh(profile)
    return profile

@router.delete("/rates/{profile_id}")
def delete_rate_profile(
    profile_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    profile = db.query(RateProfile).filter(RateProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Rate profile not found")

    linked_lottos = db.query(LottoType).filter(LottoType.rate_profile_id == profile_id).count()
    if linked_lottos > 0:
        raise HTTPException(status_code=400, detail=f"ไม่สามารถลบได้ เนื่องจากมีหวย {linked_lottos} รายการใช้งานโปรไฟล์นี้อยู่")

    db.delete(profile)
    db.commit()
    return {"status": "success", "message": "Deleted successfully"}

# --- Categories ---
@router.get("/categories", response_model=List[CategoryResponse])
def get_categories(
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role == UserRole.admin:
        if not current_user.shop_id: return [] 
        query = db.query(LottoCategory).filter(
            (LottoCategory.shop_id == current_user.shop_id) | 
            (LottoCategory.shop_id == None)
        )
        return query.order_by(LottoCategory.order_index.asc()).all()
    elif current_user.role == UserRole.superadmin:
        return db.query(LottoCategory).order_by(LottoCategory.shop_id, LottoCategory.order_index.asc()).all()
    else:
        if current_user.shop_id:
            query = db.query(LottoCategory).filter(
                (LottoCategory.shop_id == current_user.shop_id) | 
                (LottoCategory.shop_id == None)
            )
        else:
            query = db.query(LottoCategory).filter(LottoCategory.shop_id == None)
        return query.order_by(LottoCategory.order_index.asc()).all()

@router.post("/categories/init_defaults")
def init_default_categories(
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if not current_user.shop_id:
        raise HTTPException(status_code=400, detail="User has no shop")

    added_count = 0
    for default_cat in DEFAULT_CATEGORIES_CONFIG:
        exists = db.query(LottoCategory).filter(
            LottoCategory.shop_id == current_user.shop_id,
            LottoCategory.label == default_cat["label"]
        ).first()

        if not exists:
            new_cat = LottoCategory(
                label=default_cat["label"],
                color=default_cat["color"],
                shop_id=current_user.shop_id
            )
            db.add(new_cat)
            added_count += 1
    
    db.commit()
    return {"message": f"เพิ่มหมวดหมู่สำเร็จ {added_count} รายการ", "added": added_count}

@router.post("/categories", response_model=CategoryResponse)
def create_category(
    cat_in: CategoryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    target_shop_id = None
    if current_user.role == UserRole.admin:
        target_shop_id = current_user.shop_id
    elif current_user.role == UserRole.superadmin:
        target_shop_id = current_user.shop_id

    new_cat = LottoCategory(
        label=cat_in.label,
        color=cat_in.color,
        shop_id=target_shop_id,
        order_index=getattr(cat_in, 'order_index', 999)
    )
    db.add(new_cat)
    db.commit()
    db.refresh(new_cat)
    return new_cat

@router.put("/categories/{cat_id}", response_model=CategoryResponse)
def update_category(
    cat_id: UUID,
    cat_in: CategoryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    category = db.query(LottoCategory).filter(LottoCategory.id == cat_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    
    if current_user.role == UserRole.admin:
        if category.shop_id is not None and category.shop_id != current_user.shop_id:
            raise HTTPException(status_code=403, detail="Access denied")

    category.label = cat_in.label
    category.color = cat_in.color
    if hasattr(cat_in, 'order_index'):
        category.order_index = cat_in.order_index
        
    db.commit()
    db.refresh(category)
    return category

@router.delete("/categories/{cat_id}")
def delete_category(
    cat_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    category = db.query(LottoCategory).filter(LottoCategory.id == cat_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    if current_user.role == UserRole.admin:
        if category.shop_id is not None and category.shop_id != current_user.shop_id:
            raise HTTPException(status_code=403, detail="คุณลบหมวดหมู่ของร้านอื่นไม่ได้")

    lottos_in_category = db.query(LottoType).filter(LottoType.category == str(cat_id)).all()
    
    if lottos_in_category:
        general_cat = db.query(LottoCategory).filter(
            LottoCategory.shop_id == current_user.shop_id,
            LottoCategory.label.in_(["อื่นๆ", "General", "ทั่วไป"])
        ).first()

        new_cat_id = str(general_cat.id) if general_cat else "General"

        for lotto in lottos_in_category:
            if current_user.role == UserRole.admin and lotto.shop_id != current_user.shop_id:
                continue 
            lotto.category = new_cat_id
        db.commit()

    try:
        db.delete(category)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail="ไม่สามารถลบหมวดหมู่นี้ได้")
    
    return {"status": "success", "message": "ลบหมวดหมู่เรียบร้อยแล้ว"}

# -------------------------------------------------------------------
# ✅ [เพิ่มใหม่] ดึงข้อมูลแม่แบบ (ต้องวางไว้ก่อน get_lotto_detail)
# -------------------------------------------------------------------
@router.get("/lottos/templates", response_model=List[LottoResponse])
def get_lotto_templates(
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    # อนุญาตเฉพาะ Admin / Superadmin
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    # ดึงเฉพาะที่เป็น Template (is_template = True)
    query = db.query(LottoType).filter(LottoType.is_template == True)
    
    # ถ้าเป็น Admin ร้าน ให้เห็น Template ของตัวเอง + ของระบบกลาง (Shop ID = None)
    if current_user.role == UserRole.admin:
        query = query.filter(
            (LottoType.shop_id == current_user.shop_id) | 
            (LottoType.shop_id == None)
        )
        
    return query.all()

# --- Lottos ---
@router.get("/lottos", response_model=List[LottoResponse])
def get_lottos(
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    def fetch_all_lottos():
        return db.query(LottoType).order_by(LottoType.id).all()

    all_lottos = lotto_cache.get_cached_lottos(fetch_all_lottos)
    filtered_lottos = []
    
    for lotto in all_lottos:
        lotto_shop_id = str(lotto.get('shop_id')) if lotto.get('shop_id') else None
        user_shop_id = str(current_user.shop_id) if current_user.shop_id else None

        if current_user.role == UserRole.member:
            if lotto.get('is_template') is False:
                if user_shop_id:
                    if lotto_shop_id == user_shop_id:
                        filtered_lottos.append(lotto)
                else:
                    filtered_lottos.append(lotto)
        elif current_user.role == UserRole.admin:
            if lotto_shop_id == user_shop_id:
                filtered_lottos.append(lotto)
        else:
            filtered_lottos.append(lotto)
            
    return filtered_lottos

@router.get("/lottos/{lotto_id}", response_model=None)
def get_lotto_detail(
    lotto_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    lotto = db.query(LottoType).options(
        joinedload(LottoType.rate_profile),
        joinedload(LottoType.shop)
    ).filter(LottoType.id == lotto_id).first()

    if not lotto:
        raise HTTPException(status_code=404, detail="Lotto not found")

    if current_user.role == UserRole.admin and lotto.shop_id != current_user.shop_id:
         if not lotto.is_template:
             raise HTTPException(status_code=403, detail="Access denied")
         
    rates = {}
    if lotto.rate_profile:
        rates = lotto.rate_profile.rates

    final_theme = "#2563EB"
    if lotto.shop and hasattr(lotto.shop, 'theme_color') and lotto.shop.theme_color:
        final_theme = lotto.shop.theme_color

    if lotto.category:
        category = db.query(LottoCategory).filter(
            func.cast(LottoCategory.id, String) == str(lotto.category)
        ).first()
        if category and category.color and category.color.startswith("#"):
            final_theme = category.color

    return {
        "id": lotto.id,
        "name": lotto.name,
        "img_url": lotto.img_url,
        "close_time": lotto.close_time,
        "open_days": lotto.open_days,
        "rates": rates,
        "is_active": lotto.is_active,
        "theme_color": final_theme
    }

@router.post("/lottos", response_model=LottoResponse)
def create_lotto(
    lotto_in: LottoCreate, 
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    is_template = getattr(lotto_in, "is_template", False)
    shop_id = None
    if current_user.role == UserRole.admin:
        shop_id = current_user.shop_id
        is_template = False

    existing_lotto = db.query(LottoType).filter(
        LottoType.code == lotto_in.code,
        LottoType.shop_id == shop_id
    ).first()

    if existing_lotto:
        raise HTTPException(status_code=400, detail=f"รหัสหวย {lotto_in.code} มีอยู่แล้ว")

    new_lotto = LottoType(
        name=lotto_in.name,
        code=lotto_in.code,
        category=lotto_in.category,
        rate_profile_id=lotto_in.rate_profile_id,
        img_url=lotto_in.img_url,
        api_link=lotto_in.api_link,
        open_days=lotto_in.open_days,
        open_time=parse_time(lotto_in.open_time),
        close_time=parse_time(lotto_in.close_time),
        result_time=parse_time(lotto_in.result_time),
        is_active=True,
        shop_id=shop_id,
        is_template=is_template,
        rules=getattr(lotto_in, 'rules', {})
    )
    db.add(new_lotto)
    db.commit()
    lotto_cache.invalidate_lotto_cache()
    db.refresh(new_lotto)
    return new_lotto

@router.put("/lottos/{lotto_id}", response_model=LottoResponse)
def update_lotto(
    lotto_id: UUID,
    lotto_in: LottoCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    lotto = db.query(LottoType).filter(LottoType.id == lotto_id).first()
    if not lotto:
        raise HTTPException(status_code=404, detail="Not found")

    if lotto_in.img_url and lotto.img_url and lotto_in.img_url != lotto.img_url:
        try:
            old_file_name = lotto.img_url.split("/")[-1]
            supabase.storage.from_(BUCKET_NAME).remove([old_file_name])
        except Exception:
            pass

    lotto.name = lotto_in.name
    lotto.code = lotto_in.code
    lotto.category = lotto_in.category
    lotto.rate_profile_id = lotto_in.rate_profile_id
    lotto.img_url = lotto_in.img_url
    lotto.api_link = lotto_in.api_link
    lotto.open_days = lotto_in.open_days
    lotto.open_time = parse_time(lotto_in.open_time)
    lotto.close_time = parse_time(lotto_in.close_time)
    lotto.result_time = parse_time(lotto_in.result_time)
    lotto.rules = getattr(lotto_in, 'rules', {})
    
    db.commit()
    lotto_cache.invalidate_lotto_cache()
    db.refresh(lotto)
    return lotto

@router.patch("/lottos/{lotto_id}/toggle")
def toggle_lotto_status(
    lotto_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    lotto = db.query(LottoType).filter(LottoType.id == lotto_id).first()
    if not lotto:
        raise HTTPException(status_code=404, detail="Lotto not found")

    lotto.is_active = not lotto.is_active
    db.commit()
    lotto_cache.invalidate_lotto_cache() # ✅ ล้าง Cache
    return {"status": "success", "new_state": lotto.is_active}

@router.delete("/lottos/{lotto_id}")
def delete_lotto(
    lotto_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    lotto = db.query(LottoType).filter(LottoType.id == lotto_id).first()
    if not lotto:
        raise HTTPException(status_code=404, detail="Lotto not found")
    
    try:
        db.delete(lotto)
        db.commit()
        lotto_cache.invalidate_lotto_cache()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=400, detail="ไม่สามารถลบหวยนี้ได้")
    
    return {"status": "success", "message": "Lotto deleted successfully"}

@router.post("/lottos/import_defaults")
def import_default_lottos(
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role != UserRole.admin or not current_user.shop_id:
        raise HTTPException(status_code=403, detail="Only Shop Admin can import")

    templates = db.query(LottoType).filter(LottoType.is_template == True).all()
    if not templates:
        raise HTTPException(status_code=404, detail="ไม่พบข้อมูลแม่แบบ")

    default_rate = db.query(RateProfile).filter(
        RateProfile.shop_id == current_user.shop_id
    ).first()
    
    if not default_rate:
         raise HTTPException(status_code=400, detail="กรุณาสร้าง 'เรทราคา' ก่อน")
    
    imported_count = 0
    for tmpl in templates:
        exists = db.query(LottoType).filter(
            LottoType.shop_id == current_user.shop_id,
            LottoType.code == tmpl.code
        ).first()
        
        if not exists:
            new_lotto = LottoType(
                name=tmpl.name,
                code=tmpl.code,
                category=tmpl.category,
                img_url=tmpl.img_url,
                api_link=tmpl.api_link,
                open_days=tmpl.open_days,
                open_time=tmpl.open_time,
                close_time=tmpl.close_time,
                result_time=tmpl.result_time,
                is_active=True,
                is_template=False,
                shop_id=current_user.shop_id,
                rate_profile_id=default_rate.id,
                rules=tmpl.rules
            )
            db.add(new_lotto)
            imported_count += 1
    
    db.commit()
    lotto_cache.invalidate_lotto_cache()
    return {"message": f"ดึงข้อมูลสำเร็จ! เพิ่มหวยใหม่ {imported_count} รายการ"}

@router.put("/lottos/bulk-rate-update")
def bulk_update_lotto_rates(
    body: BulkRateRequest, 
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.superadmin, UserRole.admin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    try:
        query = db.query(LottoType).filter(LottoType.is_template == False)
        if current_user.role == UserRole.admin:
            query = query.filter(LottoType.shop_id == current_user.shop_id)

        updated_count = query.update(
            {LottoType.rate_profile_id: body.rate_profile_id},
            synchronize_session=False
        )
        
        db.commit()
        lotto_cache.invalidate_lotto_cache()
        return {"message": "Success", "updated_count": updated_count}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="เกิดข้อผิดพลาดในการอัปเดตข้อมูล")