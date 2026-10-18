"""API/Worker share the sibling E checkout, including children of an existing reload process."""
from pathlib import Path
import sys

_e_source = Path(__file__).resolve().parents[2] / 'intelligent_analysis'
if (_e_source / 'intelligent_analysis' / '__init__.py').is_file():
    if str(_e_source) not in sys.path:
        sys.path.insert(0, str(_e_source))
