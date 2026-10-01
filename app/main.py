from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import router
from app.core.config import get_settings

settings = get_settings()
app = FastAPI(title="EduTrio ERP Certificate Scanner", version="0.1.0",
              description="Certificate scanning backend using the existing EduTrio Supabase project and schema.")
app.add_middleware(CORSMiddleware, allow_origins=settings.origins, allow_credentials=True,
                   allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["Authorization", "Content-Type"])
app.include_router(router)
