# Templates de NPC Trade — Interfaz de comercio

Coloca aquí recortes de los elementos de la ventana de comercio NPC.

## Templates necesarios

| Archivo | Qué capturar | Tamaño aprox |
| --------- | ------------- | ------------- |
| `npc_trade_window.png` | Borde/header de la ventana de trade | 100-200px |
| `buy_button.png` | Botón "Buy" de la ventana NPC | 40-80px |
| `sell_button.png` | Botón "Sell" de la ventana NPC | 40-80px |
| `ok_button.png` | Botón "OK" / confirmar en diálogos NPC | 40-80px |
| `amount_input.png` | Campo de cantidad (input numérico) | 40-80px |

## Cómo capturar

1. Abre el diálogo de trade con un NPC (ejemplo: "hi" → "trade")
2. Toma screenshot con el bot: `python tools/capture_templates.py --screenshot output/npc_trade.png`
3. Recorta: `python tools/capture_templates.py --from-file output/npc_trade.png --crop`
4. Selecciona "trade_items" como categoría
