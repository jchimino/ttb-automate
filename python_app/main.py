"""TTB Automate - FastAPI Server"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import os
import socket

from routers import pages, api

app = FastAPI(title="TTB Automate", description="TTB Label Verification Assistant")

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(pages.router)
app.include_router(api.router)

# Static files
static_dir = os.path.join(os.path.dirname(__file__), 'static')
if os.path.exists(static_dir):
    app.mount('/static', StaticFiles(directory=static_dir), name='static')


@app.on_event("startup")
async def startup_banner():
    try:
        local_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        local_ip = "0.0.0.0"

    banner = """
\033[2J\033[H
\033[1;32m  TTB Automate — AI-Powered Label Compliance\033[0m
  ─────────────────────────────────────────────

  \033[1mApplication is ready.\033[0m Navigate to:

    \033[1;36m➜  Local:    http://localhost:8004\033[0m
    \033[1;36m➜  Network:  http://{ip}:8004\033[0m

  Demo credentials (see README for full list):
    Industry:  industrytest@gmail.com / Password1
    Staff:     sam@treasury.gov / Password1
    Admin:     admin@ttb.gov / Password1

  \033[2mPress Ctrl+C to stop\033[0m
""".format(ip=local_ip)

    print(banner)


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8004, reload=True)
