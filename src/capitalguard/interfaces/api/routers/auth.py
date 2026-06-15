#--- START OF FILE: src/capitalguard/interfaces/api/routers/auth.py ---
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from pydantic import BaseModel

from capitalguard.infrastructure.db.base import get_session
from capitalguard.infrastructure.db.models.auth import User
from capitalguard.interfaces.api.security import auth

router = APIRouter(prefix="/auth", tags=["Authentication"])

class UserCreate(BaseModel):
    email: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_user(user_in: UserCreate, db: Session = Depends(get_session)):
    """
    إنشاء مستخدم جديد. في بيئة الإنتاج، قد ترغب في حماية هذه النقطة.
    """
    user = db.query(User).filter(User.email == user_in.email).first()
    if user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )
    hashed_password = auth.hash_password(user_in.password)
    new_user = User(email=user_in.email, hashed_password=hashed_password)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"message": f"User {new_user.email} created successfully"}


@router.post("/token", response_model=Token)
def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(), 
    db: Session = Depends(get_session)
):
    """
    تسجيل الدخول باستخدام البريد الإلكتروني (username) وكلمة المرور.
    """
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not auth.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # ملاحظة: الأدوار هنا ثابتة كمثال. في نظام حقيقي، ستحتاج لجلبها من جدول user_roles.
    # سنترك هذا التحسين للمستقبل.
    access_token = auth.create_access_token(
        subject=user.email,
        roles=["analyst"] # نمنح كل المستخدمين دور "analyst" مؤقتًا
    )
    return {"access_token": access_token, "token_type": "bearer"}
#--- END OF FILE ---