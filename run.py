"""Start the web app:  python run.py   (then open http://localhost:8000)"""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:get_app", factory=True, host=os.environ.get("HOST", "127.0.0.1"),
                port=int(os.environ.get("PORT", "8000")), reload=False)
