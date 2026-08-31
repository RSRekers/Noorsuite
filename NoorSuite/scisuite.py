"""Backwards-compatible shim.

The implementation lives in :mod:`NoorSuite.model`, :mod:`NoorSuite.protocol`,
:mod:`NoorSuite.ipc`, :mod:`NoorSuite.client`, :mod:`NoorSuite.sheet`,
:mod:`NoorSuite.dialogs` and :mod:`NoorSuite.app`.  Import from :mod:`NoorSuite`
directly; this module only re-exports the common names.
"""
from __future__ import annotations

import sys

from .client import SciSuiteClient
from .ipc import IPCBridge
from .model import DataObject, SubplotModel, TraceRef
from .sheet import PlotSheet

try:  # avoid importing Qt widgets unless they are available
    from .app import ModernOriginSuite, SciSuiteWindow
except Exception:  # pragma: no cover
    ModernOriginSuite = SciSuiteWindow = None

__all__ = ["SciSuiteClient", "IPCBridge", "DataObject", "TraceRef", "SubplotModel",
           "PlotSheet", "SciSuiteWindow", "ModernOriginSuite"]


if __name__ == "__main__":
    from .__main__ import main
    sys.exit(main())
