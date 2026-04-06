"""
Permite ejecutar el proyecto directamente:

    # Desde el directorio waypoint-navigator/
    python .

    # Desde el directorio padre frbit/
    python waypoint-navigator/

    # Como módulo desde frbit/
    python -m waypoint-navigator   (requiere Python >= 3.11 con packages de directorio)
"""

from main import main  # noqa: E402

if __name__ == "__main__":
    main()
