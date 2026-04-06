# Enfoques Técnicos de Bots en Tibia (2026)

## 1. Memory Reading
- Lectura directa de memoria del cliente Tibia para extraer información de personajes, criaturas, mapas y estados.
- Uso de firmas dinámicas y patrones para localizar offsets tras cada update.
- Herramientas: Cheat Engine, custom DLLs, frameworks como Blackd Proxy.
- Ventajas: acceso completo y en tiempo real;
- Riesgos: detección por BattlEye, cambios frecuentes en offsets.

## 2. Code Injection
- Inyección de código (DLL injection) en el proceso Tibia.exe para interceptar funciones internas.
- Técnicas: hooks en DirectX, manipulación de WinAPI, inline patching.
- Permite automatización avanzada (cavebot, healing, targeting) y overlays.
- Contramedidas: BattlEye detecta firmas, heurísticas de comportamiento, integridad de memoria.

## 3. Proxy/Packet Bots
- Interceptan y modifican el tráfico entre cliente y servidor Tibia.
- Permiten bots multiplataforma, análisis de paquetes, bots para OTServers.
- Ejemplo: OTClient, proxies personalizados, bots para Android/iOS.
- Limitaciones: en servidores oficiales, el tráfico está cifrado y autenticado.

## 4. Pixel/Screen Bots
- Automatización basada en reconocimiento visual (color, OCR, templates).
- No interactúan con la memoria ni el cliente, solo simulan input humano.
- Usados para evadir BattlEye y en plataformas donde no es posible inyectar código.
- Herramientas: OpenCV, PyAutoGUI, Tesseract OCR.

## 5. Tendencias 2025–2026
- Aumento de bots híbridos (memory + pixel).
- Uso de IA para reconocimiento de patrones y evasión de detección.
- Mayor dificultad para mantener bots tras updates frecuentes y hardening del cliente.
