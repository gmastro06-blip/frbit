# Templates de ítems de loot — Contenedor

Coloca aquí recortes en PNG/JPG de los **iconos de ítem** tal como
aparecen en la ventana del contenedor de loot de Tibia.

## Cómo obtener un template

1. Abre un contenedor en el juego con el ítem que quieres lootear.
2. Captura un frame de OBS.
3. Recorta el icono del ítem del slot del contenedor
   (aprox. **32 × 32 px**).
4. Guarda el recorte aquí: `sword.png`, `gold_coin.png`, etc.

## Whitelist

Para usar solo ítems específicos (modo `--loot-mode whitelist`):

- Añade los nombres de los ficheros (sin extensión) a
  `loot_config.json` → `loot_whitelist`:

  ```json
  "loot_whitelist": ["sword", "gold_coin", "leather_armor"]
  ```

## Modo "all"

Con `--loot-mode all` (por defecto) el bot recoge todos los ítems
de todos los slots ocupados del contenedor — no se necesitan templates
de ítems individuales.
