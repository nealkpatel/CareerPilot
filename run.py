"""Start CareerPilot:  python run.py   then open http://127.0.0.1:8000"""

import uvicorn

from server.config import HOST, PORT

if __name__ == "__main__":
    print(f"\n  CareerPilot is starting on http://{HOST}:{PORT}\n")
    uvicorn.run("server.app:app", host=HOST, port=PORT, log_level="info")
