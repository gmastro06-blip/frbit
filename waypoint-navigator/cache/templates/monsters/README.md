# Templates de monstruos — Battle List

Coloca aquí recortes en PNG/JPG de los **iconos de monstruo** tal como
aparecen en el panel de Battle List del cliente Tibia.

## Cómo obtener un template

1. Abre Tibia y encuentra un monstruo en la zona de caza.
2. Corre `python examples/diag_hpmp.py --source obs-ws` para obtener
   un frame capturado de OBS.
3. Recorta el icono del monstruo del panel de Battle List
   (aprox. **22–28 × 22–28 px** es suficiente).
4. Guarda el recorte aquí con el nombre del monstruo: `troll.png`,
   `goblin.png`, etc.

## Notas

- El nombre del fichero se usa en los logs para identificar el monstruo.
- Si usas varios monstruos, añade uno por fichero.
- Resolución de referencia: **1920 × 1080**.
  Si juegas en diferente resolución, el detector escala el ROI.
- La confianza mínima por defecto es `0.65`; ajústala en
  `combat_config.json` → `confidence`.
