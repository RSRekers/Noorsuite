"""NoorSuite / SciSuite -- organize and re-plot datasets from a research project.

Jupyter side (exploratory)::

    from NoorSuite import SciSuiteClient
    suite = SciSuiteClient().launch()
    suite.push_dataframe(df, name="IV_sweep")
    suite.plot("IV_sweep", x="time_s", y=["current_mA", "voltage_V"], new_sheet=True)

GUI side: ``python -m NoorSuite --gui 55555``.
"""
from .client import SciSuiteClient, launch
from .model import (ColorMap, DataObject, ImageObject, ImageRef, ProjectModel,
                    SheetModel, SubplotModel, TraceRef)

__version__ = "0.6.0"

__all__ = [
    "SciSuiteClient",
    "launch",
    "DataObject",
    "ImageObject",
    "ImageRef",
    "TraceRef",
    "SubplotModel",
    "SheetModel",
    "ColorMap",
    "ProjectModel",
    "__version__",
]
