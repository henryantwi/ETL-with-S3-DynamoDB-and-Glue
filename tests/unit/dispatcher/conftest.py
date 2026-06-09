"""Put the Lambda handler dir on sys.path so `import handler` works.

The handler lives under lambda/ (a Python keyword), so it cannot be imported as
a `lambda.pipeline_dispatcher` package — import it as top-level `handler`.
"""

import sys
from pathlib import Path

LAMBDA_DIR = Path(__file__).resolve().parents[3] / "lambda" / "pipeline_dispatcher"
sys.path.insert(0, str(LAMBDA_DIR))
