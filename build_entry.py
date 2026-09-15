# Thin Nuitka entry point: launches the MarketHub server (app.server:main).
# Kept at repo root so the compiled standalone folder keeps `app/` next to the
# executable, which is what app/paths.py expects for config/data resolution.
from app.server import main

if __name__ == "__main__":
    main()
