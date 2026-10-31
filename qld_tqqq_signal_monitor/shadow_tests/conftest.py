"""Make shadow tests independently runnable, as in the separate CI command."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
