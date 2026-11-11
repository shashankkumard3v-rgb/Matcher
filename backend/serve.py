"""Start the Matcher server: python serve.py  (run.bat does this for you).

Same as the long uvicorn command, plus one fix: the project lives in OneDrive, where Windows
doesn't always announce file changes, so auto-restart checks files by polling instead
(about once a second). Edits to app, config or skills restart the server by themselves;
runs and your notes never do.
"""
import os

os.environ.setdefault("WATCHFILES_FORCE_POLLING", "true")

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app", host="127.0.0.1", port=8000, reload=True,
        reload_dirs=["app", "config", "skills"], reload_includes=["*.py", "*.yaml", "*.md"],
    )
