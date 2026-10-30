import os
import sys

# Ensure backend root is always on sys.path for test discovery and imports
backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_root not in sys.path:
    sys.path.insert(0, backend_root)

