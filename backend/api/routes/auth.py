# Authentication routes
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.database.db import get_db
from backend.database.models import User
from backend.database.schemas import (
    FacebookCredentials,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserProfileUpdate,
    UserResponse,
)
from backend.api.auth import (
    create_access_token,
    hash_password,
    verify_password,
    get_current_user
)
from backend.database.crud import FacebookSessionCRUD, UserCRUD

router = APIRouter()


@router.post("/register", response_model=TokenResponse)
async def register(user_data: UserCreate, db: Session = Depends(get_db)):
    """Register a new user"""
    
    # Check if user exists
    existing_user = db.query(User).filter(
        (User.username == user_data.username) | (User.email == user_data.email)
    ).first()
    
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username or email already registered",
        )
    
    # Create new user
    hashed_password = hash_password(user_data.password)
    new_user = User(
        username=user_data.username,
        email=user_data.email,
        password_hash=hashed_password,
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    # Create tokens
    access_token = create_access_token(data={"sub": new_user.id})
    
    return TokenResponse(
        access_token=access_token,
        expires_in=60 * 24,  # 24 hours in minutes
        user=UserResponse.model_validate(new_user),
    )


@router.post("/login", response_model=TokenResponse)
async def login(credentials: UserLogin, db: Session = Depends(get_db)):
    """Authenticate user and return token"""
    
    # Find user by username
    user = db.query(User).filter(User.username == credentials.username).first()
    
    if not user or not verify_password(credentials.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        )
    
    # Create token
    access_token = create_access_token(data={"sub": str(user.id)})
    UserCRUD.update_last_login(db, user.id)
    user = db.query(User).filter(User.id == user.id).first()

    return TokenResponse(
        access_token=access_token,
        expires_in=60 * 24,  # 24 hours
        user=UserResponse.model_validate(user),
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get current user profile"""
    return UserResponse.model_validate(current_user)


@router.put("/me", response_model=UserResponse)
async def update_me(
    profile_data: UserProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update current user profile."""
    payload = profile_data.model_dump(exclude_unset=True)

    if "username" in payload and payload["username"] != current_user.username:
        existing_username = db.query(User).filter(User.username == payload["username"]).first()
        if existing_username:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already registered",
            )
        current_user.username = payload["username"]

    if "email" in payload and payload["email"] != current_user.email:
        existing_email = db.query(User).filter(User.email == payload["email"]).first()
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered",
            )
        current_user.email = payload["email"]

    db.commit()
    db.refresh(current_user)
    return UserResponse.model_validate(current_user)


@router.put("/me/facebook-credentials")
async def update_facebook_credentials(
    credentials: FacebookCredentials,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Store Facebook session data used by the scraper."""
    payload = {}
    if credentials.cookies is not None:
        payload["fb_cookies"] = credentials.cookies
    if credentials.fb_dtsg is not None:
        payload["fb_dtsg"] = credentials.fb_dtsg
    if credentials.user_agent is not None:
        payload["fb_user_agent"] = credentials.user_agent

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No Facebook credentials provided",
        )

    active_session = FacebookSessionCRUD.upsert_active_for_user(
        db=db,
        user_id=current_user.id,
        fb_cookies=payload.get("fb_cookies"),
        fb_dtsg=payload.get("fb_dtsg"),
        fb_user_agent=payload.get("fb_user_agent"),
    )

    return {
        "message": "Facebook credentials updated successfully",
        "updated_at": datetime.utcnow().isoformat(),
        "has_cookies": bool(active_session.fb_cookies),
        "has_fb_dtsg": bool(active_session.fb_dtsg),
        "has_user_agent": bool(active_session.fb_user_agent),
    }


@router.get("/me/facebook-credentials")
async def get_facebook_credentials_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return whether Facebook scraper credentials are configured."""
    active_session = FacebookSessionCRUD.get_active_by_user_id(db, current_user.id)
    return {
        "has_cookies": bool(active_session and active_session.fb_cookies),
        "has_fb_dtsg": bool(active_session and active_session.fb_dtsg),
        "has_user_agent": bool(active_session and active_session.fb_user_agent),
    }


@router.post("/logout")
async def logout(current_user: User = Depends(get_current_user)):
    """Logout user (client should discard token)"""
    return {"message": "Logged out successfully. Please discard the token."}
