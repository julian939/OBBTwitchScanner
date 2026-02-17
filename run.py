import uvicorn

import certifi
import os
os.environ["SSL_CERT_FILE"] = certifi.where()

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)