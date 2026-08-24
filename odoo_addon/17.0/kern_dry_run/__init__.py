# El import es el registro del addon: Odoo carga este paquete y los submodulos
# quedan registrados como efecto de importarlos. No estan "sin usar" -- sacarlos
# rompe el addon en runtime, que es justo lo que ruff no puede ver.
from . import models, wizards  # noqa: F401
