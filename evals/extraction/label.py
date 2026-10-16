"""Owner audit and blind labeling CLI for the golden notices. See recallops.evals.label_cli.

Run from the repository root:
    make label ARGS="--audit --source-id 24082"
"""

import sys

from recallops.evals.label_cli import main

if __name__ == "__main__":
    sys.exit(main())
