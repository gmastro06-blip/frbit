# Templates de cadáveres — Viewport

Coloca aquí recortes en PNG/JPG del **sprite del cadáver** tal como
aparece en el viewport del juego (no en el mapa ni en el panel).

## Cómo obtener un template

1. Mata un monstruo y deja el cadáver en el suelo.
2. Captura un frame de OBS.
3. Recorta el sprite del cadáver del área del viewport del juego.
   El tamaño varía según el monstruo: aprox. **32–64 × 32–64 px**.
4. Guarda el recorte aquí: `troll_corpse.png`, `goblin_corpse.png`, etc.

## Notas

- Captura el cadáver con el personaje cerca (el mismo zoom que usarás
  en el bot) para que el tamaño de píxeles coincida.
- Si la posición del tile del cadáver se conoce por coordenadas
  (vía `notify_kill`), el looter calculará la posición exacta en pantalla;
  el template se usa solo para **confirmar** que hay algo que lootear.
- Ajusta `loot_config.json` → `confidence` si hay falsos positivos.
