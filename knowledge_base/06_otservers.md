# OTServers — Open Tibia Servers

## ¿Qué es Open Tibia?

Open Tibia (OT / OTS) se refiere a **servidores no oficiales de Tibia gestionados por la comunidad**, construidos usando implementaciones de código abierto reverse-engineered del protocolo de servidor de Tibia. Estos servidores no están afiliados con CipSoft.

**Estado Legal:** Los OTServers existen en una zona gris legal. CipSoft no los avala oficialmente y ha tomado acciones legales contra grandes operaciones comerciales de OTS. Sin embargo, los servidores hobby no comerciales generalmente operan sin consecuencias legales directas.

## Historia de los OTServers

| Período | Descripción |
|---------|-------------|
| **Principios 2000s** | Primeros emuladores de servidor OT aparecen poco después de que Tibia se vuelve popular |
| **2004–2010** | La comunidad OTS explota, reflejando el pico de popularidad de Tibia |
| **2010–2017** | Crecimiento continuo; diversificación de comunidades nacionales |
| **2017+** | Explosión masiva post-BattlEye; botters migran de servidores oficiales a OTS |
| **2020–presente** | Ecosistema OTS maduro con miles de servidores activos globalmente |

Las comunidades brasileña y polaca fueron particularmente centros activos de OTS.

## Motores de OTServer

### The Forgotten Server (TFS)
- **Históricamente el más popular**
- TFS 1.6 lanzado junio 2024; soporta protocolo 13.10
- Lenguaje de scripting: Lua
- Base de código C++
- Documentación extensa; comunidad de soporte enorme
- **GitHub:** github.com/otland/forgottenserver

### OTX Server
- Fork de OTServBR-Global construido sobre TFS 0.3.7
- Ideal para servidores era antigua 8.6
- Popular para servidores nostálgicos de la vieja escuela

### Canary (OpenTibiaBR)
- Motor moderno por la comunidad OpenTibiaBR
- Soporta protocolos Tibia 14+; desarrollo activo
- Considerado el **futuro de los OTServers** pero menos estable que TFS
- **GitHub:** github.com/opentibiabr/canary

### OTServBR-Global
- Proyecto impulsado por la comunidad brasileña
- Base de varios forks populares
- Fuerte soporte en los foros brasileños

## Comunidades Clave

| Comunidad | URL | Enfoque |
|-----------|-----|---------|
| **OTLand** | otland.net | Hub principal en inglés; foros, código, listado de servidores |
| **otservlist.org** | otservlist.org | Listado y estadísticas de servidores |
| **otservers.online** | otservers.online | Base de datos de servidores con datos históricos |
| **xTibia** | — | Comunidad OTServer brasileña |
| **TibiaBR forums** | — | Gran comunidad Tibia brasileña |

## Personalización de OTServers

Los servidores OT típicamente presentan gameplay muy modificado:
- **Tasas de experiencia customizadas**: 2x, 5x, 10x, o incluso 1000x
- **Tasas de loot customizadas**
- **Mapas, ciudades y quests custom**
- **Mecánicas de juego modificadas**
- **NPCs y monstruos custom**
- **Características únicas** no encontradas en Tibia oficial

## OTClientV8 (OTCv8)

Cliente alternativo de Tibia escrito en C++17 con Lua; multiplataforma.

**Estadísticas (2023):**
- 1 millón de instalaciones únicas
- 250,000 en Android

**Características clave:**
- **Módulo bot incorporado** — el sistema de scripting completo usa Lua
- vBot es el bot oficial/no oficial empaquetado dentro de OTCv8
- Anti-detección irrelevante para la mayoría de OTS — el operador del servidor típicamente permite o tolera bots

## Bots Populares para OTServers (2020–2026)

| Bot | Estado | Descripción |
|-----|--------|-------------|
| **OTCv8 + vBot** | Activo | Cliente con bot incorporado; estándar de facto |
| **EasyBot** | Activo | Gratis, código abierto; soporta versiones 7.x–latest |
| **LoftyBot** | Activo | Comercial; 7,000+ suscripciones en 9 años; versiones 7.4–15 |
| **WindBot** | Activo (OTS) | Pivotó a OTS-only después de BattlEye |
| **XenoBot** | Activo (OTS) | Liberado gratis con credenciales abiertas (user/xenobot) |
| **ValidusBot** | Activo | Objetivo: Tibia 12.72+ (OTS) |
| **PandoriumX** | Activo | Bot OTS especializado |
| **DiaxBot** | Activo | Bot OTS comercial |

## Anti-Bot en OTServers

Las comunidades de servidores privados tienen sus propios debates internos sobre bots. Algunos servidores aceptan el botting como parte de su conjunto de características; otros intentan prevenirlo:

**Técnicas anti-bot en OTS:**
- Desafíos NPC tipo CAPTCHA (NPCs que hacen preguntas matemáticas aleatorias durante la caza)
- Trampas de teletransporte (tiles que teletransportan a caminantes de rutas repetitivas a salas de verificación)
- Análisis de patrones de movimiento del lado del servidor (implementaciones custom)
- Spawns de criaturas anti-bot

La mayoría de estas técnicas son fácilmente derrotadas por los bots modernos.

## Servidores OTS Históricos Populares

- **Primeros servidores populares de la era 2005–2009**: Siguen siendo puntos de referencia nostálgicos para la comunidad
- **Servidores de versiones antiguas**: Muchos emulan versiones específicas de Tibia antiguo (7.4, 7.6, 8.6) por nostalgia
- **Servidores con más de 1,000 jugadores simultáneos**: Han existido varios a lo largo de los años

## Fuentes

- TibiaWiki — Open Tibia: https://tibia.fandom.com/wiki/Open_Tibia
- OTLand: https://otland.net/
- The Forgotten Server 1.6: https://otland.net/threads/the-forgotten-server-1-6.289173/
- GitHub — Canary: https://github.com/opentibiabr/canary
- OTCv8 GitHub: https://github.com/OTCv8/otclientv8
- EasyBot OTLand: https://otland.net/threads/easybot-open-source-tibia-bot.291447/
