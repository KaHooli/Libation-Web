from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.users import CreateUserRequest, UpdateUserRequest, UserAdminResponse, PermissionsUpdate
from ..schemas.auth import MessageResponse
from ..services.auth import hash_password, get_user_by_id
from ..models.user import User, DEFAULT_PERMISSIONS
from .auth import get_current_user

router = APIRouter(prefix="/api/users", tags=["users"])


def require_admin(current_user=Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


@router.get("", response_model=list[UserAdminResponse])
def list_users(db: Session = Depends(get_db), _=Depends(require_admin)):
    return db.query(User).order_by(User.created_at).all()


@router.post("", response_model=UserAdminResponse, status_code=status.HTTP_201_CREATED)
def create_user(body: CreateUserRequest, db: Session = Depends(get_db), _=Depends(require_admin)):
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already taken")
    if len(body.username) < 3:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Username must be at least 3 characters")
    if len(body.password) < 8:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Password must be at least 8 characters")
    user = User(
        username=body.username,
        hashed_password=hash_password(body.password),
        is_admin=body.is_admin,
        permissions=dict(DEFAULT_PERMISSIONS) if not body.is_admin else None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/{user_id}/permissions", response_model=UserAdminResponse)
def update_permissions(
    user_id: int,
    body: PermissionsUpdate,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    user = get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.is_admin:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Admin users inherit all permissions automatically")

    current_perms = dict(user.permissions or DEFAULT_PERMISSIONS)
    for flag in ("can_download", "can_scan", "can_manage_accounts", "can_liberate", "can_remove_downloads"):
        val = getattr(body, flag)
        if val is not None:
            current_perms[flag] = val
    user.permissions = current_perms

    if body.download_cap is not None:
        user.download_cap = body.download_cap if body.download_cap > 0 else None

    db.commit()
    db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserAdminResponse)
def update_user(
    user_id: int,
    body: UpdateUserRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    user = get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user_id == current_user.id and body.is_admin is False:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot revoke your own admin status")
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.is_admin is not None:
        user.is_admin = body.is_admin
    if body.new_password:
        if len(body.new_password) < 8:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Password must be at least 8 characters")
        user.hashed_password = hash_password(body.new_password)
    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}", response_model=MessageResponse)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    if user_id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete your own account")
    user = get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    db.delete(user)
    db.commit()
    return MessageResponse(message="User deleted")
