"""Entry point for the pluteus-admin executable (PyInstaller starts here)."""
import sys

from pluteus.admin_gui import main

sys.exit(main())
