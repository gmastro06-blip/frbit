# Mecánicas del Juego Tibia

## Cliente y Estilo Visual

Tibia usa una **perspectiva top-down, 2D basada en tiles** con gráficos de pixel art. Esta estética se ha preservado a lo largo de la historia del juego. Los bajos requerimientos de hardware eran intencionales — permitía que jugadores de todo el mundo (incluyendo Brasil y Europa del Este con hardware de menor especificación) participaran.

### Evolución del Cliente
- **Cliente standalone original** (1997–2015): Ejecutable clásico
- **Flash Client** (2009–2016): Versión basada en navegador, mejor GUI pero peor rendimiento
- **Tibia 11 / Cliente moderno** (19 abril 2016–presente): Nuevo cliente standalone reemplazando Flash

## Vocaciones (Clases de Personaje)

Al crear un personaje, los jugadores comienzan en **Rookgaard** (antiguamente) o **Dawnport** (desde 2014) sin vocación. Al llegar al nivel 8 y avanzar al continente principal, eligen una de (originalmente cuatro, ahora cinco) vocaciones:

| Vocación | Rol | Estilo de Combate Principal |
|----------|-----|------------------------------|
| **Knight** | Tank/Cuerpo a cuerpo | Espadas, Mazas, Hachas — más HP, poco mana |
| **Paladin** | Híbrido | Armas a distancia (arcos/ballestas) + magia moderada |
| **Druid** | Sanador/Soporte | Magia de Hielo y Tierra, mejores curaciones |
| **Sorcerer** | Daño | Magia de Fuego, Energía, Muerte — hechizos ofensivos más poderosos |
| **Monk** (2025) | Cuerpo a cuerpo/Soporte | Artes marciales (puños/bastón), sistema de Armonía |

El **Monk** fue anunciado en **febrero de 2025** y representa la **primera vocación nueva añadida a Tibia desde su lanzamiento en 1997** — una brecha de 28 años. El Monk usa un sistema único de "Armonía" para construir y gastar recursos, "Virtudes" (tres buffs alternables, solo uno activo a la vez), y más de 70 nuevos ítems dedicados.

## Sistema de Skills

Cada personaje tiene múltiples habilidades que aumentan a través de la práctica:
- **Skills de combate**: Espada, Maza, Hacha, Distancia, Escudo, Pelea sin armas
- **Nivel Mágico**: Determina poder de hechizos y eficiencia de mana
- **Pesca**: Habilidad secundaria de recolección

Cada vocación avanza skills a ritmos diferentes. Por ejemplo, los Knights avanzan más rápido en skills cuerpo a cuerpo mientras que los Sorcerers/Druids avanzan más rápido en Nivel Mágico. El avance de skills se desacelera logarítmicamente — entrenar skills altos requiere cantidades enormes de tiempo o armas de entrenamiento.

## Sistema Mágico

Tibia tiene dos tipos de magia:

### 1. Hechizos Instantáneos
Se lanzan directamente escribiendo el comando del hechizo (ej: `exura` para curar). Consumen maná inmediatamente.

**Hechizos importantes por categoría:**
- **Curaciones**: exura (leve), exura gran (mayor), exura vita (mana shield)
- **Ofensivos**: exori (strike), exevo gran mas flam (magia de área de fuego)
- **Soporte**: haste, utamo vita (magic shield), utamo tempo (berserker)

### 2. Magia de Runas
Los hechizos se "inscriben" en runas en blanco por Druids/Sorcerers con alto nivel mágico. Los ítems de runa resultantes pueden ser usados por cualquiera — esta fue históricamente la base de la economía de jugadores de Tibia (fabricantes de runas vendiéndolas a luchadores).

**Runas importantes:**
- **Sudden Death Rune (SD)**: Mayor daño de muerte a un objetivo
- **Explosion Rune**: Daño de fuego en área
- **Avalanche Rune**: Daño de hielo en área
- **Great Fireball Rune**: Área de fuego
- **Ultimate Healing Rune (UH)**: Curación mayor usable por todos

## Sistema de Combate

- El combate es **en tiempo real** con ataques automáticos ocurriendo cada ~2 segundos
- Los jugadores pueden usar hechizos e ítems entre auto-ataques
- **Sistema de Stamina** (introducido 2006): Los jugadores tienen un pool limitado de experiencia bonus que se regenera offline, desalentando el juego 24/7
- **Skill Wheel** (introducido Invierno 2022): Sistema de árbol de talentos complejo por vocación
- **Sistema de Proficiencia de Armas** (Verano 2025): Más de 400 árboles de habilidades para weapons individuales

### Mecánicas de Combate Detalladas
- **Auto-ataque**: Ocurre automáticamente cada ~2 segundos si el objetivo está en rango
- **Distancia de ataque**: Varía por arma (melee = 1 tile, distance = varios tiles)
- **Elementos**: Fuego, Hielo, Energía, Tierra, Muerte, Santo, Físico — cada criatura tiene resistencias/debilidades
- **Condiciones de estado**: Burning (quemado), Poison (envenenado), Electrified (electrificado), Freezing (congelado)
- **Speed**: Determina quién se mueve primero cuando dos jugadores/criaturas actúan simultáneamente

## Penalización por Muerte

Tibia tiene una de las penalizaciones por muerte más severas en cualquier MMORPG, central a su cultura:
- Los jugadores pierden **puntos de experiencia** (potencialmente bajando de nivel) y **progreso de skill**
- Hay una probabilidad de **perder ítems equipados** (10% base por ítem equipado sin bendiciones)
- El **sistema de Blessings** mitiga estas pérdidas — hasta 8 bendiciones comprables a NPCs o en la Tienda, cada una reduciendo la pérdida de XP ~8%, con una reducción máxima de 86%
- Después de la muerte, todas las bendiciones se pierden (excepto "Twist of Fate" en ciertas condiciones)

## Progresión de Niveles

No existe **tope de nivel** en Tibia. Los jugadores de mayor nivel están en el rango 2,000+. La experiencia requerida por nivel aumenta dramáticamente, haciendo que los niveles más altos sean un compromiso extremo a largo plazo.

**Experiencia aproximada por nivel:**
- Nivel 1→10: Miles de XP
- Nivel 50→100: Millones de XP
- Nivel 200→300: Cientos de millones de XP
- Nivel 1000+: Billones de XP

## Sistema de PvP y Skulls

En mundos Open/Hardcore PvP, existe el sistema de skulls para regular el player killing:

| Skull | Color | Significado |
|-------|-------|-------------|
| White Skull | Blanco | Atacó a un jugador sin skull; dura 15 minutos |
| Yellow Skull | Amarillo | Ha sido matado múltiples veces por el mismo jugador |
| Red Skull | Rojo | PK serial; pierde ítems al morir; temporal |
| Black Skull | Negro | PK extremo; pierde TODOS los ítems al morir |
| Green Skull | Verde | Indica que el personaje te está atacando (Retro Open PvP) |

## Tipos de Mundo

| Tipo | Descripción |
|------|-------------|
| **Optional PvP** | Sin PvP; servidores seguros PvE |
| **Open PvP** | PvP habilitado; Skull System regula el player killing |
| **Hardcore PvP** | Sin restricciones de skull; máxima libertad PvP |
| **Retro Open PvP** | Reglas antiguas (2 unjusts por kill, Green Skull vuelve) |
| **Retro Hardcore PvP** | Reglas antiguas sin restricciones de killing |

## Sistema de Casas y Gremios

- **Casas**: Propiedades rentables en las ciudades; ofrecen almacenamiento privado
- **Gremios**: Organizaciones de jugadores con sus propias páginas, halls (edificios de gremio comprables) e historias
- La política de gremios — guerras, alianzas, traiciones — forma una parte significativa del drama social de Tibia

## Fuentes

- TibiaWiki — Vocation: https://tibia.fandom.com/wiki/Vocation
- TibiaWiki — Death: https://tibia.fandom.com/wiki/Death
- TibiaWiki — Blessings: https://tibia.fandom.com/wiki/Blessings
- TibiaWiki — Skull System: https://tibia.fandom.com/wiki/Skull_System
- CipSoft — A Fifth Playable Character Class: https://www.cipsoft.com/en/359-a-fifth-playable-character-class-is-added-to-tibia
