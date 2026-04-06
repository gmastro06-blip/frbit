# Estado Actual (2020–2026): Bots, OTServers, Venezuela, GitHub

## 1. Bots
- Persisten bots avanzados, especialmente pixelbots y soluciones híbridas.
- BattlEye mantiene presión, pero la evasión sigue activa (VMS, IA, randomización).
- **Métodos públicos de BattlEye:**
    - Escaneo de memoria del proceso Tibia.exe para detectar firmas de bots, DLLs inyectadas y patrones sospechosos.
    - Bloqueo e identificación de procesos externos que interactúan con el cliente (memory reading, injection).
    - Verificación de integridad de archivos y memoria.
    - Detección de hooks en funciones críticas (WinAPI, DirectX, OpenGL).
    - Monitoreo de drivers y servicios sospechosos a nivel kernel.
    - Reporte y baneo automático de cuentas al detectar actividad anómala.
    - Ejemplo: Si un bot intenta leer la memoria de Tibia usando ReadProcessMemory, BattlEye puede detectar la llamada y bloquear el proceso externo, o marcar la cuenta para revisión.
    - Ejemplo: Si se inyecta una DLL para manipular funciones internas, BattlEye detecta la presencia de módulos no autorizados y puede cerrar el cliente o banear la cuenta.
- Comunidades de bots migran a foros privados y GitHub (repositorios privados, releases temporales).

## 2. OTServers
- Motores como The Forgotten Server y Canary siguen activos y actualizados (soporte Tibia 11+).
- Comunidad OTland y GitHub: miles de forks, custom servers, economías paralelas.
- Tendencia a servidores "hardcore" y "retro", y a la integración de sistemas anti-bot propios.

## 3. Venezuela y RMT
- Venezuela sigue siendo epicentro de gold farming y RMT (Real Money Trading).
- Jugadores profesionales, guilds organizadas, uso de bots para maximizar ingresos.
- Medidas de CipSoft: baneos selectivos, bloqueo de IPs, pero el fenómeno persiste.

## 4. GitHub y Comunidad
- Repositorios de bots y OTServers proliferan, aunque muchos son privados o efímeros.
- Colaboración internacional, documentación técnica y herramientas open source.
- El ciclo de detección y evasión se documenta y comparte rápidamente.
