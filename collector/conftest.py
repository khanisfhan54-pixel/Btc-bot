import sys
import os

# Insert the parent of the project root so that 'collector' resolves to the
# outer collector/ directory, allowing 'from collector.collector.X' imports.
_project_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_parent not in sys.path:
    sys.path.insert(0, _project_parent)
