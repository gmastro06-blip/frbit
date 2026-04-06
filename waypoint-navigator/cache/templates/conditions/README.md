# Templates de iconos de condición

Coloca aquí recortes en PNG/JPG de los **iconos de estado** (veneno,
parálisis, quemadura, etc.) que aparecen en el panel de Tibia.

Nombra cada fichero según la condición que representa:

- `poison.png`
- `paralyze.png`
- `burning.png`
- `drunk.png`
- `bleeding.png`
- `freezing.png`

## Nota

El monitor usa **detección por color (HSV)** por defecto, que no
necesita templates. Los templates solo se usan si configuras
`condition_config.json` → `detection_mode: "template"`.

La detección por color funciona bien en la mayoría de casos;
usa templates si tienes muchos falsos positivos.
