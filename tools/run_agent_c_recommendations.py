from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.run_agent_b_recommendations import main, run_agent_b_recommendations


run_agent_c_recommendations = run_agent_b_recommendations


if __name__ == "__main__":
    raise SystemExit(main())
