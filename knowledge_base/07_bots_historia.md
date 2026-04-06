# Historia Cronológica de los Bots de Tibia (2004–2025)

## Era 1: Pre-Bot (1997–2003)

Los primeros años de Tibia no tuvieron bots notables. El juego requería que los jugadores realizaran todas las acciones manualmente. El trampeo en esta era consistía principalmente en:
- Explotar bugs del juego
- Multi-clienting (ejecutar múltiples personajes simultáneamente)
- Automatización básica con scripts para tareas muy específicas
- "Power Abusing" guilds dominaban los servidores a través de juego humano coordinado

**CipSoft no codificó el banishing por macros hasta el 8 de marzo de 2004.** Antes de eso, los Gamemasters podían banir jugadores por "comportamiento destructivo", multi-clienting, o uso obvio de macros, pero no había regla formal.

## Era 2: Primeros Bots (2004–2007)

Cuando los primeros bots simples emergieron alrededor de 2004–2007, eran raros, primitivos y socialmente estigmatizados dentro de la comunidad de Tibia.

**Contexto importante:**
- **Reglas de macros formalizadas (marzo 2004):** CipSoft prohibió oficialmente macros y bots como violaciones de reglas
- **Sistema de Gamemasters voluntarios activo:** Un cuerpo de jugadores-Gamemasters confiables existía cuyo trabajo principal era investigar y reportar tramposos
- **CIPBot**: Aparece en discusiones de foros polacos de esta era; asociado con Tibia pre-magic-system (versiones 7.1–7.6, circa 2004–2006); probablemente fue una herramienta simple de lectura de memoria o macro

## Era 3: La Explosión de Bots (2007–2011)

Para 2007, tres grandes plataformas de bots emergieron que definirían el botting de Tibia durante años.

---

### TibiaBot NG (NGBot) — "Lord of War" / NGSoft Team
**Período activo:** ~2007–2010

El primer bot de amplio lanzamiento que ganó penetración real en el mercado.
- "NG" significaba "Next Generation"
- **Características:** Auto-healer, auto-ataque de objetivo, cavebot (caza automatizada), runemaker, hotkeys, alarma de detección de GM, alarma de desconexión, alarma de jugador detectado
- El creador "Lord of War" se ocultó a mediados de 2010 después de que CipSoft encontró un método para detectar específicamente la firma de su bot
- Originalmente comenzó como proyecto personal antes de ser lanzado públicamente

---

### ElfBot NG — "Ekx" / Elite Bot Group
**Período activo:** ~2008–2011 (dominante), con uso legado en OTS hasta ~2014

El bot más dominante de la era. El creador "Ekx" cerró operaciones y desapareció en diciembre de 2011.

**Características técnicas:**
- **Lenguaje de scripting TCL** incrustado para personalización
- **Algoritmo de pathfinding A*** incorporado para navegación de cavebot
- **Cuatro sistemas de curación distintos**: Spell Healer, Potion Healer, Condition Healer, Mana Trainer
- **Modos de targeting**: Proximidad, Salud (menor HP primero), Stick (mismo objetivo)
- Acciones auto-PvP incluyendo paralización, empujes
- Gestión de runas/hechizos con hotkeys
- **Sistema de licencias basado en hardware fingerprinting**
- Se ejecutaba via un `loader.exe` que requería privilegios de Administrador

**Arquitectura:** Aplicación externa de lectura de memoria (lee la memoria del proceso de Tibia vía `ReadProcessMemory`), no es una herramienta basada en inyección.

Descrito por los contemporáneos como "uno de los mejores bots PvP jamás creados".

---

### TUGBot (TibiaUndergroundBot) — "DarkstaR"
**Período activo:** Contemporáneo con los anteriores

- Originalmente gratis, luego se volvió comercial debido a dificultades financieras del desarrollador
- Descrito como construido "con el sueño de hacer el mejor bot freeware del mundo"
- **Enfoque técnico:** Lectura de memoria de proceso externo, conjunto estándar de características (cavebot, healer, runemaker)

---

## Era 4: El Vacío de Poder y Nuevos Participantes (2012–2014)

Después de la desaparición de Ekx en diciembre de 2011, tres nuevos desarrolladores ingresaron al mercado simultáneamente, incluyendo Nick Cano (XenoBot). El mercado continuó creciendo a pesar de la primera Herramienta de Detección Automática de CipSoft.

Aproximadamente 5,000 cuentas eran baneadas por mes, subiendo a picos de 62,000 en algunos meses para 2015.

---

### XenoBot — Nick Cano
**Período activo:** ~2012–2017 (oficial), solo OTS después

El desarrollo comenzó circa 2010–2012, cuando Cano tenía aproximadamente 15–17 años. En su pico tuvo casi **2,000 suscriptores mensuales pagos**.

**Arquitectura: Inyección de DLL**
Un biblioteca principal (C++) es inyectada directamente en el proceso del cliente de Tibia, dando al código del bot acceso a la memoria del juego, funciones y contenido como parte del juego mismo.

**Componentes técnicos documentados por Cano:**
- Biblioteca de hooking de código
- Motor GUI hecho a mano
- Interfaz de captura de packets
- Spoofer de packets
- Biblioteca de manipulación de memoria de procesos
- Rutinas de inyección de código y llamadas de función propietarias
- Escáner de huella de memoria (para detección de versión)
- Interfaz de scripting Lua expuesta a usuarios

**Modelo de threading:**
Hookeó la API de Windows `PeekMessage` (hook de Import Address Table) para insertar la ejecución del código del bot en el propio loop principal del juego, evitando los peligros de concurrencia de ejecutarse en un hilo separado.

**Gestión de versiones:**
Usó `versionMagic` (huella de 4 bytes) + `versionAddress` para auto-detectar qué versión del cliente estaba corriendo, luego cargaba el archivo de offsets apropiado (`.xblua`) y DLL principal.

**Protocolo de entrega de actualizaciones:**
Protocolo de texto personalizado usando etiquetas (`XBUPDATE`, `ADDR`, `DLL`, etc.)

**Legado:** Nick Cano luego escribió "Game Hacking: Developing Autonomous Bots for Online Games" (No Starch Press), uno de los pocos libros formales sobre el tema, y habló en conferencias de seguridad informática.

---

### MageBot
**Período activo:** ~2012–2017 (oficial), OTS después

- Modificación de cliente (basado en inyección)
- Especializado para clases mago (Druids, Sorcerers) pero adaptable
- Cavebot completo con 20+ scripts incorporados para niveles 1–300
- Auto-healer y looter completamente configurables con activación de hotkey
- Soportó múltiples versiones de Tibia

---

### BBot — "Mega" / Fernando B.
**Período activo:** ~15 años de historia de desarrollo (orígenes ~2006–2008)

- Inicialmente de pago, se volvió **gratis en abril de 2021**, completamente **open source en GitHub en septiembre de 2022**
- Soportó versiones de Tibia 8.5 hasta 10.99
- Explícitamente abandonó soporte para Tibia 11.0+ con BattlEye
- **Características:** Cavebot, healer, looter, sistema de macros, soporte de ítems custom, herramientas de depuración
- **Infraestructura técnica:** Backend PHP4 (luego migrado a PHP8 con Docker/Kubernetes en 2021)

---

## Era 5: La Era Proxy — Blackd Proxy

### Blackd Proxy / Blackd Tools
**Período activo:** A través de Tibia versión 11.11 y anteriores

Usó un **enfoque arquitectónico fundamentalmente diferente: interceptación de proxy**.

**Cómo funcionaba:**
1. Reemplaza la clave pública RSA estática de Tibia con su propia clave
2. El cliente se conecta al proxy local del bot en lugar de directamente al servidor del juego
3. El proxy descifra los packets del cliente (usando su clave privada), los lee/modifica, re-cifra y reenvía al servidor
4. Los packets del servidor son igualmente interceptados en el camino de regreso

**Características:**
- Módulo de cheats, Runemaker, Cavebot (con scripts "safe" nuevos y legados)
- Hotkeys, War bot, Trainer
- Motor de Eventos, Magebomb (coordinador de hechizos de área)
- Eventos Condicionales ("miniscripts" basados en condiciones in-game)
- Sistema de configuración config.ini
- Ejecutaba `crackd.dll` como módulo de conexión optimizado

**Seguridad:** Sufrió un incidente de inyección PHP donde un atacante accedió a emails/contraseñas de usuarios; fue reforzado después.

**Código fuente:** La versión clásica fue lanzada posteriormente en GitHub (blackdtools/Blackd-Proxy-CLASSIC)

---

### Tibia Auto
**Período activo:** Registrado en SourceForge el 17 enero 2006; última actualización 24 noviembre 2019

Bot de código abierto creado por "vanitas" y "wisling".

**Características:**
- Sistema de waypoints basado en minimapa
- Pathfinding inteligente entre waypoints
- Automatización de killing/looting/training/depot
- Lanzamiento de hechizos (curación, invocación, AoE)
- Automatización de pociones y runas
- Comunicación con jugadores

---

## Era 6: WindBot y NeoBot (2013–2017)

### WindBot — Lucas Terra
**Período activo:** ~2013–2017 (oficial), solo OTS después

Creado por Lucas Terra, identificado como "una de las mentes principales detrás de ElfBot" (probablemente el mismo "Ekx" o estrechamente asociado).

**Arquitectura: Overlay externo**
NO inyecta en el juego; en cambio envía clics de ratón y pulsaciones de teclado mientras renderiza sus propios visuales sobre la ventana del juego.

**Características detalladas:**
- **Scripting:** Lenguaje Lua con capacidades extendidas (variables nativas, iteradores foreach, bloques init)
- **Sistema HUD:** Puede dibujar imágenes, rectángulos, círculos, arcos, textos, gráficos vectoriales, ítems y outfits en pantalla; "World HUD" sincroniza el renderizado con la tasa de frames del cliente
- **Networking:** Permite coordinación de jugadores, intercambio de archivos, seguimiento de aliados/enemigos via bases de datos locales
- **Curación:** Cuatro conjuntos de reglas con **puntos de activación aleatorizados** para evitar patrones de detección
- **Cavebot:** Navegación basada en waypoints con tipos de nodo (Stand, Walk, Shovel, Rope) más waypoints de acción
- **Looting:** Reclama 95% de tasa de recuperación con listas de ítems auto-generadas
- **Rendimiento:** Diseñado para múltiples instancias simultáneas sin picos de CPU

---

### NeoBot (~2015)
**Período activo:** ~2015–2017

- Se distinguió **simulando movimientos de ratón y teclado** en lugar de manipulación directa de memoria
- Específicamente diseñado para evadir los métodos de detección de la era
- Engañó con éxito a los mecanismos anti-cheat menos sofisticados antes de que llegara BattlEye

---

## Era 7: Post-BattlEye y Código Abierto (2017–2025)

### Impacto Inmediato de BattlEye (2017)
BattlEye efectivamente mató todos los bots basados en inyección tradicional en servidores oficiales de Tibia. La mayoría de los desarrolladores de bots anunciaron públicamente que sus herramientas ya no funcionaban en Tibia real en semanas de la aplicación completa.

**Donde fueron los bots:**
- Migración masiva a OTServers
- Desarrollo de enfoques basados en píxeles/visión por computadora
- Open-source y proyectos comunitarios en GitHub

### Proyectos Open-Source Activos en GitHub (2020–2026)

| Proyecto | Stars | Lenguaje | Enfoque |
|---------|-------|----------|---------|
| **PyTibia** | 288 | Python | Detección de píxeles + CNN/RNN planeados |
| **TibiaAuto12** | 188 | Python | Bot de píxeles con OpenCV para v12 |
| **OldBot** | ~50 | AutoHotkey | Híbrido memoria + píxeles (50k líneas, 8 años) |
| **TibiaPilotNG** | 44 | Python | Bot de píxeles avanzado (activo marzo 2026) |
| **tibia_12_bot** | 12 | Python | Bot Python ligero |
| **pyBot** | 2 | Python | Implementación básica |

---

## Perfiles de los Principales Desarrolladores de Bots

| Desarrollador | Bot | Era | Legado |
|--------------|-----|-----|--------|
| **Lord of War (NGSoft)** | TibiaBot NG | 2007–2010 | Pionero del botting serio de Tibia; luego intentó crear su propio MMORPG |
| **Ekx / Lucas Terra** | ElfBot NG + WindBot | 2008–presente | "Dos de las mentes principales" citadas repetidamente; desapareció de ElfBot en dic 2011 |
| **Nick Cano** | XenoBot | 2012–2017 | Comenzó a los 15 años; luego autor de "Game Hacking" (No Starch Press); carrera en investigación de seguridad |
| **DarkstaR** | TUGBot | 2008–2014 | Creador de TUGBot; originalmente gratis, monetizado por necesidad financiera |
| **Mega / Fernando B.** | BBot | ~2008–2022 | ~15 años de desarrollo solo; open-sourced en 2022 |

## Fuentes

- TibiaWiki — Bot: https://tibia.fandom.com/wiki/Bot
- TibiaWiki — Cheating: https://tibia.fandom.com/wiki/Cheating
- Nick Cano — Throwbacks Part 1: https://nickcano.com/throwbacks-1-bots-tibia/
- Nick Cano — Bot Architecture: https://nickcano.com/bot-architecture-0/
- ElfBot NG — NoxiousWiki: https://wiki.noxiousot.com/wiki/Elfbot_NG
- WindBot Features: https://www.tibiawindbot.com/features.html
- BBot: https://bbot.bmega.net/
- Blackd Tools: https://www.blackdtools.com/blackdproxy.php
- TibiaServers — Bots history: https://tibiaservers.net/blog/post/tibia-bots-what-with-botting-industry
- GitHub topics/tibia-bot: https://github.com/topics/tibia-bot
