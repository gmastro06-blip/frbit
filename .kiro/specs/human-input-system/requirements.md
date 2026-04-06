# Requirements Document

## Introduction

Este documento define los requisitos para el Sistema de Humanización de Inputs del bot de Tibia. El sistema tiene como objetivo hacer que los inputs generados por el bot sean estadísticamente indistinguibles de los inputs de un jugador humano real, minimizando la superficie de detección por sistemas anti-cheat como BattlEye.

El sistema se integra con el InputController existente y proporciona múltiples capas de humanización: timing variable, errores simulados, movimiento de mouse natural, y opcionalmente, inputs a nivel hardware mediante Arduino HID.

## Glossary

- **Human_Input_System**: Sistema completo de humanización que coordina todos los componentes
- **Timing_Humanizer**: Componente que introduce variabilidad temporal en los inputs
- **Behavior_Simulator**: Componente que simula comportamientos humanos como fatiga y errores
- **Mouse_Movement_Engine**: Componente que genera trayectorias de mouse naturales
- **Arduino_HID_Controller**: Componente opcional que envía inputs mediante dispositivo HID físico
- **Profile_Manager**: Componente que gestiona perfiles de comportamiento
- **Input_Event**: Evento de entrada (tecla, mouse) con timing y parámetros
- **Gaussian_Distribution**: Distribución normal para generar valores aleatorios naturales
- **Bezier_Curve**: Curva matemática para trayectorias suaves de mouse
- **HID_Device**: Human Interface Device - dispositivo de entrada reconocido por el sistema operativo
- **Reaction_Time**: Tiempo entre estímulo y respuesta (150-350ms para humanos)
- **Key_Press_Duration**: Duración de presión de una tecla (50-120ms típico)
- **Fatigue_Level**: Nivel de cansancio simulado que afecta velocidad y precisión (0.0-1.0)
- **Error_Rate**: Probabilidad de cometer errores humanos (0.0-1.0)
- **Behavior_Profile**: Conjunto de parámetros que definen un estilo de juego
- **Detection_Surface**: Características del comportamiento que pueden ser analizadas por anti-cheat
- **InputController**: Controlador de inputs existente del bot
- **Configuration_File**: Archivo YAML con parámetros del sistema

## Requirements

### Requirement 1: Timing Humanizado con Distribución Gaussiana

**User Story:** Como desarrollador del bot, quiero que los delays entre inputs sigan una distribución gaussiana, para que los tiempos no sean perfectamente regulares y parezcan humanos.

#### Acceptance Criteria

1. WHEN el Timing_Humanizer genera un delay, THE Timing_Humanizer SHALL apply una distribución gaussiana con media y desviación estándar configurables
2. THE Timing_Humanizer SHALL generate reaction times entre 150ms y 350ms con media de 220ms
3. THE Timing_Humanizer SHALL generate key press durations entre 50ms y 120ms con media de 80ms
4. WHEN se solicita un micro-pause, THE Timing_Humanizer SHALL generate delays entre 10ms y 50ms
5. FOR ALL generated delays, parsing the configuration then generating delays then measuring statistics SHALL produce mean and standard deviation within 5% of configured values (round-trip property)
6. FOR ALL sequences of 1000 delays, THE statistical distribution SHALL pass Kolmogorov-Smirnov test for normality with p-value > 0.05 (metamorphic property)

### Requirement 2: Simulación de Fatiga Progresiva

**User Story:** Como desarrollador del bot, quiero simular fatiga progresiva durante sesiones largas, para que el comportamiento refleje el cansancio natural de un jugador humano.

#### Acceptance Criteria

1. THE Behavior_Simulator SHALL maintain un Fatigue_Level que incrementa con el tiempo de sesión
2. WHEN Fatigue_Level aumenta, THE Behavior_Simulator SHALL increase reaction times proporcionalmente
3. WHEN Fatigue_Level aumenta, THE Behavior_Simulator SHALL increase Error_Rate proporcionalmente
4. WHEN Fatigue_Level excede 0.7, THE Behavior_Simulator SHALL trigger pausas AFK aleatorias
5. WHEN una pausa AFK ocurre, THE Behavior_Simulator SHALL reset Fatigue_Level a un valor entre 0.2 y 0.4
6. THE Fatigue_Level SHALL increment at a rate between 0.05 and 0.15 per hour of session time
7. FOR ALL fatigue increases, Fatigue_Level SHALL remain within bounds [0.0, 1.0] (invariant property)

### Requirement 3: Generación de Errores Humanos

**User Story:** Como desarrollador del bot, quiero que el sistema cometa errores ocasionales como un humano, para evitar patrones de ejecución perfecta que son detectables.

#### Acceptance Criteria

1. WHEN Error_Rate es mayor que 0, THE Behavior_Simulator SHALL generate errores con probabilidad igual a Error_Rate
2. WHEN se genera un error de tecla, THE Behavior_Simulator SHALL press una tecla adyacente en el teclado
3. WHEN se genera un error de doble-presión, THE Behavior_Simulator SHALL press la misma tecla dos veces con delay de 20-80ms
4. WHEN se genera un error de miss-click, THE Behavior_Simulator SHALL offset las coordenadas del mouse entre 5 y 25 píxeles
5. WHEN se genera una hesitación, THE Behavior_Simulator SHALL insert un delay adicional de 200-800ms
6. THE Behavior_Simulator SHALL generate cada tipo de error con probabilidades configurables independientes
7. FOR ALL error sequences, the total error rate SHALL match configured Error_Rate within 10% over 1000 actions (metamorphic property)

### Requirement 4: Movimiento de Mouse con Curvas de Bézier

**User Story:** Como desarrollador del bot, quiero que el mouse se mueva en trayectorias curvas naturales, para evitar movimientos en línea recta que son detectables.

#### Acceptance Criteria

1. WHEN el Mouse_Movement_Engine mueve el cursor, THE Mouse_Movement_Engine SHALL generate una trayectoria usando curvas de Bézier cúbicas
2. THE Mouse_Movement_Engine SHALL place puntos de control de Bézier con offset aleatorio de 10-30% de la distancia total
3. WHEN se genera una trayectoria, THE Mouse_Movement_Engine SHALL vary la velocidad del movimiento siguiendo una curva de aceleración-desaceleración
4. THE Mouse_Movement_Engine SHALL generate micro-movimientos de 1-3 píxeles durante el trayecto
5. WHEN el cursor llega al destino, THE Mouse_Movement_Engine SHALL simulate overshooting con probabilidad 0.3 y corrección posterior
6. THE Mouse_Movement_Engine SHALL complete movimientos en tiempo proporcional a la distancia (200-800ms para distancias típicas)
7. FOR ALL movements from point A to point B, the cursor SHALL reach point B within 2 pixels of target (invariant property)
8. FOR ALL movement paths, no segment SHALL be a perfectly straight line (all segments must have curvature > 0.01)

### Requirement 5: Comunicación con Arduino HID

**User Story:** Como desarrollador del bot, quiero enviar inputs mediante un Arduino que actúa como dispositivo HID real, para que los inputs sean indistinguibles de un teclado/mouse físico.

#### Acceptance Criteria

1. WHERE Arduino HID está habilitado, THE Arduino_HID_Controller SHALL establish comunicación serial con el dispositivo Arduino
2. WHEN se envía un input, THE Arduino_HID_Controller SHALL serialize el Input_Event y transmitirlo vía puerto serial
3. WHEN Arduino no está disponible, THE Arduino_HID_Controller SHALL fallback automáticamente a PostMessage
4. THE Arduino_HID_Controller SHALL detect la presencia de Arduino al inicializar el sistema
5. WHEN se envía un comando al Arduino, THE Arduino_HID_Controller SHALL wait confirmación con timeout de 100ms
6. IF el Arduino no responde, THEN THE Arduino_HID_Controller SHALL log el error y usar fallback
7. THE Arduino_HID_Controller SHALL support comandos para key press, key release, mouse move, y mouse click
8. FOR ALL commands sent to Arduino, serializing then deserializing SHALL produce equivalent command (round-trip property)

### Requirement 6: Parser de Configuración YAML

**User Story:** Como usuario del bot, quiero configurar todos los parámetros de humanización mediante un archivo YAML, para poder ajustar el comportamiento sin modificar código.

#### Acceptance Criteria

1. WHEN se inicia el sistema, THE Configuration_Parser SHALL parse el Configuration_File en formato YAML
2. WHEN el archivo YAML es inválido, THE Configuration_Parser SHALL return un mensaje de error descriptivo con línea y columna
3. THE Configuration_Parser SHALL validate que todos los parámetros numéricos estén dentro de rangos válidos
4. THE Configuration_Parser SHALL provide valores por defecto para parámetros opcionales
5. THE Pretty_Printer SHALL format objetos de configuración de vuelta a YAML válido
6. FOR ALL valid configuration objects, parsing then printing then parsing SHALL produce equivalent configuration (round-trip property)
7. THE Configuration_Parser SHALL support hot-reload de configuración sin reiniciar el bot

### Requirement 7: Gestión de Perfiles de Comportamiento

**User Story:** Como usuario del bot, quiero seleccionar entre diferentes perfiles de comportamiento (novato, experto, cansado), para simular diferentes estilos de juego.

#### Acceptance Criteria

1. THE Profile_Manager SHALL load perfiles predefinidos desde el Configuration_File
2. THE Profile_Manager SHALL allow cambio de perfil activo en tiempo de ejecución
3. WHEN se cambia de perfil, THE Profile_Manager SHALL apply los nuevos parámetros gradualmente en 5-15 segundos
4. THE Profile_Manager SHALL support perfiles personalizados definidos por el usuario
5. WHEN se selecciona perfil "novato", THE Profile_Manager SHALL configure Error_Rate alto y reaction times lentos
6. WHEN se selecciona perfil "experto", THE Profile_Manager SHALL configure Error_Rate bajo y reaction times rápidos
7. WHEN se selecciona perfil "cansado", THE Profile_Manager SHALL configure Fatigue_Level inicial alto
8. WHERE ajuste dinámico está habilitado, THE Profile_Manager SHALL modify parámetros según hora del día

### Requirement 8: Ajuste Dinámico por Hora del Día

**User Story:** Como desarrollador del bot, quiero que el comportamiento se ajuste según la hora del día, para simular patrones circadianos naturales de un jugador humano.

#### Acceptance Criteria

1. WHERE dynamic adjustment está habilitado, THE Profile_Manager SHALL check la hora del sistema cada 15 minutos
2. WHEN la hora está entre 23:00 y 06:00, THE Profile_Manager SHALL increase Fatigue_Level base en 0.2
3. WHEN la hora está entre 23:00 y 06:00, THE Profile_Manager SHALL increase reaction times en 15-25%
4. WHEN la hora está entre 10:00 y 18:00, THE Profile_Manager SHALL use parámetros normales del perfil
5. THE Profile_Manager SHALL apply transiciones suaves de parámetros durante 10-20 minutos

### Requirement 9: Sistema de Métricas y Logging

**User Story:** Como desarrollador del bot, quiero registrar métricas del comportamiento generado, para validar que las distribuciones estadísticas sean realistas.

#### Acceptance Criteria

1. THE Human_Input_System SHALL log todas las métricas a archivo con rotación diaria
2. THE Human_Input_System SHALL track estadísticas de delays (media, desviación, min, max)
3. THE Human_Input_System SHALL track tasa de errores por tipo
4. THE Human_Input_System SHALL track distribución de duraciones de key press
5. THE Human_Input_System SHALL track distribución de reaction times
6. WHEN se solicita reporte, THE Human_Input_System SHALL generate un resumen estadístico en formato JSON
7. THE Human_Input_System SHALL include timestamps con precisión de microsegundos en todos los logs

### Requirement 10: Integración con InputController Existente

**User Story:** Como desarrollador del bot, quiero que el sistema de humanización se integre transparentemente con el InputController existente, para no requerir cambios en el código del bot.

#### Acceptance Criteria

1. THE Human_Input_System SHALL expose la misma interfaz que el InputController actual
2. WHEN el bot llama a un método de input, THE Human_Input_System SHALL apply humanización antes de ejecutar
3. THE Human_Input_System SHALL support todos los métodos del InputController (press_key, release_key, move_mouse, click)
4. THE Human_Input_System SHALL maintain compatibilidad con código existente sin cambios
5. WHERE humanización está deshabilitada, THE Human_Input_System SHALL delegate directamente al InputController sin overhead
6. THE Human_Input_System SHALL initialize sin errores si el InputController está disponible

### Requirement 11: Pausas AFK Aleatorias

**User Story:** Como desarrollador del bot, quiero que el sistema genere pausas AFK aleatorias, para simular momentos en que un jugador humano se distrae o descansa.

#### Acceptance Criteria

1. THE Behavior_Simulator SHALL generate pausas AFK con probabilidad configurable por hora
2. WHEN se genera una pausa AFK, THE Behavior_Simulator SHALL suspend todos los inputs durante 30 segundos a 5 minutos
3. WHEN una pausa AFK termina, THE Behavior_Simulator SHALL resume inputs con un warm-up period de 2-5 segundos
4. THE Behavior_Simulator SHALL avoid pausas AFK durante situaciones críticas (combate, bajo HP)
5. WHEN Fatigue_Level es alto, THE Behavior_Simulator SHALL increase la probabilidad de pausas AFK
6. THE Behavior_Simulator SHALL log inicio y fin de cada pausa AFK con duración

### Requirement 12: Validación de Distribuciones Estadísticas

**User Story:** Como desarrollador del bot, quiero validar que las distribuciones generadas sean estadísticamente similares a jugadores reales, para asegurar que el sistema funciona correctamente.

#### Acceptance Criteria

1. THE Human_Input_System SHALL provide un modo de testing que genera 10000 samples de cada tipo de delay
2. WHEN se ejecuta validación estadística, THE Human_Input_System SHALL perform Kolmogorov-Smirnov test en las distribuciones
3. WHEN se ejecuta validación estadística, THE Human_Input_System SHALL calculate media, mediana, desviación estándar y percentiles
4. THE Human_Input_System SHALL compare las estadísticas generadas contra rangos esperados de jugadores humanos
5. IF alguna distribución falla validación, THEN THE Human_Input_System SHALL log warning con detalles del fallo
6. THE Human_Input_System SHALL generate gráficos de distribución en formato PNG para inspección visual

### Requirement 13: Seguridad y Anti-Detección

**User Story:** Como desarrollador del bot, quiero minimizar la Detection_Surface del sistema, para reducir la probabilidad de detección por BattlEye.

#### Acceptance Criteria

1. THE Human_Input_System SHALL use nombres de proceso y módulos que no contengan términos sospechosos (bot, cheat, hack)
2. THE Human_Input_System SHALL avoid patrones determinísticos en secuencias de inputs
3. THE Human_Input_System SHALL not leave firmas detectables en memoria del proceso del juego
4. WHERE Arduino HID está habilitado, THE Human_Input_System SHALL appear como dispositivo HID legítimo al sistema operativo
5. THE Human_Input_System SHALL randomize el orden de inicialización de componentes
6. THE Human_Input_System SHALL avoid timing perfecto en loops (agregar jitter de 1-5ms)

### Requirement 14: Manejo de Errores y Recuperación

**User Story:** Como usuario del bot, quiero que el sistema maneje errores gracefully y continúe operando, para evitar crashes que interrumpan sesiones de juego.

#### Acceptance Criteria

1. WHEN ocurre un error en un componente, THE Human_Input_System SHALL log el error con stack trace completo
2. IF el Arduino_HID_Controller falla, THEN THE Human_Input_System SHALL switch a fallback mode automáticamente
3. IF el Configuration_Parser falla, THEN THE Human_Input_System SHALL use configuración por defecto y log warning
4. WHEN un componente falla, THE Human_Input_System SHALL attempt recovery hasta 3 veces antes de deshabilitar el componente
5. THE Human_Input_System SHALL continue operando con funcionalidad reducida si componentes opcionales fallan
6. IF un error crítico ocurre, THEN THE Human_Input_System SHALL notify al usuario y ofrecer modo seguro

### Requirement 15: Tests Unitarios y Property-Based Testing

**User Story:** Como desarrollador del bot, quiero tests exhaustivos del sistema, para asegurar que todas las propiedades de correctitud se mantienen.

#### Acceptance Criteria

1. THE test suite SHALL include property-based tests para todas las distribuciones estadísticas
2. THE test suite SHALL verify round-trip properties para parsers y serializers
3. THE test suite SHALL verify invariants (Fatigue_Level bounds, coordenadas válidas)
4. THE test suite SHALL verify metamorphic properties (distribuciones estadísticas)
5. THE test suite SHALL test error conditions con inputs inválidos
6. THE test suite SHALL achieve mínimo 85% code coverage
7. THE test suite SHALL execute en menos de 60 segundos en hardware estándar

## Non-Functional Requirements

### Performance

- El overhead de humanización no debe exceder 5ms por input
- El sistema debe soportar mínimo 100 inputs por segundo
- El uso de memoria no debe exceder 50MB

### Compatibility

- Compatible con Windows 10 y Windows 11
- Compatible con Python 3.11+
- Compatible con Arduino Leonardo, Micro, y Pro Micro

### Maintainability

- Código documentado con docstrings en español
- Arquitectura modular con componentes independientes
- Configuración externalizada en YAML
