import threading
import time
import webbrowser

import uvicorn


def _open() -> None:
    time.sleep(0.8)
    webbrowser.open("http://127.0.0.1:8787")


if __name__ == "__main__":
    threading.Thread(target=_open, daemon=True).start()
    uvicorn.run("app.main:app", host="127.0.0.1", port=8787, reload=False)
