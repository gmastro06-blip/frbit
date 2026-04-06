# Plan de Implementación: Sistema de Humanización de Inputs

## Descripción General

Este plan implementa un sistema completo de humanización de inputs para el bot de Tibia, con múltiples capas de transformación que hacen que los inputs generados sean estadísticamente indistinguibles de un jugador humano real.

El sistema incluye:
- Timing humanizado con distribuciones gaussianas
- Simulación de comportamientos humanos (fatiga, errores, pausas AFK)
- Movimiento de mouse con curvas de Bézier
- Soporte opcional para Arduino HID
- Sistema de perfiles de comportamiento
- Métricas y validación estadística

## Estructura de Implementación

Las tareas están organizadas en 7 fases incrementales, donde cada fase construye sobre la anterior. Cada tarea incluye referencias específicas a los requisitos que implementa.

Las tareas marcadas con `*` son opcionales y pueden omitirse para un MVP más rápido.

---

## Fase 1: Modelos de Datos y Configuración Base

- [ ] 1. Crear estructura de proyecto y modelos de datos base
  - [ ] 1.1 Crear estructura de directorios del proyecto
    - Crear directorios: `human_input_system/`, `human_input_system/core/`, `human_input_system/config/`, `human_input_system/utils/`, `tests/`
    - Crear archivos `__init__.py` en cada directorio
    - _Requisitos: 10.1, 10.4_

  - [ ] 1.2 Implementar modelos de configuración (dataclasses)
    - Crear `config/models.py` con `TimingConfig`, `BehaviorConfig`, `MouseConfig`, `ArduinoConfig`
    - Implementar métodos `validate()` para cada configuración
    - Crear `BehaviorProfile` y `Configuration` con validación completa
    - _Requisitos: 6.3, 7.4_

  - [ ] 1.3 Implementar modelos de eventos de input
    - Crear `core/events.py` con `InputEvent`, `KeyPressEvent`, `MouseMoveEvent`, `MouseClickEvent`, `AFKPauseEvent`
    - Implementar método `to_serial_command()` para cada tipo de evento
    - _Requisitos: 5.2, 5.7_

  - [ ] 1.4 Implementar modelo de layout de teclado
    - Crear `utils/keyboard_layout.py` con clase `KeyboardLayout`
    - Definir diccionario `LAYOUT` con teclas adyacentes (QWERTY)
    - Implementar métodos `get_adjacent_keys()` y `get_random_adjacent()`
    - _Requisitos: 3.2_


- [ ] 2. Implementar parser y validador de configuración YAML
  - [ ] 2.1 Crear ConfigurationParser con parsing básico
    - Crear `config/parser.py` con clase `ConfigurationParser`
    - Implementar método `parse()` que lee archivo YAML y construye objeto `Configuration`
    - Manejar errores de sintaxis YAML con mensajes descriptivos (línea y columna)
    - _Requisitos: 6.1, 6.2_

  - [ ] 2.2 Implementar validación de rangos y valores por defecto
    - Implementar método `validate_ranges()` que verifica parámetros numéricos
    - Implementar método `apply_defaults()` para parámetros opcionales
    - Validar que error_probabilities sumen 1.0
    - _Requisitos: 6.3, 6.4_

  - [ ] 2.3 Implementar serialización a YAML (pretty printer)
    - Implementar método `to_yaml()` que convierte `Configuration` a string YAML
    - Formatear con indentación correcta y comentarios
    - _Requisitos: 6.5_

  - [ ]* 2.4 Escribir property test para round-trip de configuración
    - **Property 24: Configuration Round-Trip**
    - **Valida: Requisitos 6.6**
    - Verificar que parse → to_yaml → parse produce configuración equivalente

  - [ ]* 2.5 Escribir unit tests para ConfigurationParser
    - Test con archivo YAML inválido (debe retornar error descriptivo)
    - Test con parámetros fuera de rango (debe retornar errores de validación)
    - Test con configuración parcial (debe aplicar defaults)
    - _Requisitos: 6.2, 6.3, 6.4_

- [ ] 3. Crear archivo de configuración de ejemplo
  - [ ] 3.1 Crear config.yaml con todos los parámetros
    - Crear archivo `config.yaml` con estructura completa
    - Incluir secciones: timing, behavior, mouse, arduino, profiles, system
    - Definir perfiles predefinidos: default, novato, experto, cansado
    - Agregar comentarios explicativos para cada parámetro
    - _Requisitos: 6.1, 7.1, 7.5, 7.6, 7.7_

- [ ] 4. Checkpoint - Validar modelos y configuración
  - Ejecutar tests de configuración
  - Verificar que config.yaml se parsea correctamente
  - Asegurar que todos los tests pasan

---

## Fase 2: Componentes Core de Humanización

- [ ] 5. Implementar TimingHumanizer
  - [ ] 5.1 Crear clase TimingHumanizer con generación gaussiana básica
    - Crear `core/timing_humanizer.py` con clase `TimingHumanizer`
    - Implementar `__init__()` que recibe `TimingConfig`
    - Implementar `get_reaction_time()` con distribución N(220, 40) y ajuste por fatiga
    - Implementar `get_key_press_duration()` con distribución N(80, 15) y ajuste por fatiga
    - _Requisitos: 1.1, 1.2, 1.3_

  - [ ] 5.2 Implementar generadores de delays adicionales
    - Implementar `get_micro_pause()` con distribución N(25, 8), rango [10, 50]
    - Implementar `get_movement_duration()` usando aproximación de Fitts's Law
    - Implementar `add_jitter()` para agregar variabilidad a delays base
    - _Requisitos: 1.4, 4.6_

  - [ ]* 5.3 Escribir property tests para TimingHumanizer
    - **Property 1: Gaussian Distribution Correctness** - Verificar media y std dentro de 5%
    - **Property 2: Timing Ranges Compliance** - Verificar rangos de valores generados
    - **Property 3: Statistical Normality** - Test Kolmogorov-Smirnov
    - **Valida: Requisitos 1.1, 1.2, 1.3, 1.4, 1.5, 1.6**

  - [ ]* 5.4 Escribir unit tests para TimingHumanizer
    - Test con fatigue_level = 0.0 (valores base)
    - Test con fatigue_level = 1.0 (valores máximos)
    - Test que reaction times aumentan con fatiga
    - _Requisitos: 1.2, 1.3, 2.2_


- [ ] 6. Implementar BehaviorSimulator
  - [ ] 6.1 Crear clase BehaviorSimulator con gestión de fatiga
    - Crear `core/behavior_simulator.py` con clase `BehaviorSimulator`
    - Implementar `__init__()` que recibe `BehaviorConfig`
    - Implementar `update_fatigue()` que incrementa fatiga con el tiempo
    - Implementar `get_fatigue_level()` que retorna nivel actual
    - Mantener fatiga en rango [0.0, 1.0]
    - _Requisitos: 2.1, 2.6, 2.7_

  - [ ] 6.2 Implementar generación de errores humanos
    - Implementar `should_generate_error()` que retorna tipo de error o None
    - Calcular error_rate ajustado por fatiga
    - Seleccionar tipo de error según probabilidades configuradas
    - _Requisitos: 3.1, 3.6_

  - [ ] 6.3 Implementar aplicación de errores específicos
    - Implementar `apply_wrong_key_error()` usando KeyboardLayout
    - Implementar `apply_double_press_error()` que retorna delay [20-80ms]
    - Implementar `apply_miss_click_offset()` que offset coordenadas [5-25 píxeles]
    - Implementar `apply_hesitation_delay()` que retorna delay [200-800ms]
    - _Requisitos: 3.2, 3.3, 3.4, 3.5_

  - [ ] 6.4 Implementar gestión de pausas AFK
    - Implementar `should_trigger_afk_pause()` con probabilidad ajustada por fatiga
    - Implementar `generate_afk_duration()` con distribución log-normal [30-300s]
    - Implementar `reset_fatigue_after_afk()` que resetea a [0.2-0.4]
    - Implementar `is_in_critical_situation()` (stub por ahora, requiere integración con bot)
    - _Requisitos: 2.4, 2.5, 11.1, 11.2, 11.4, 11.5_

  - [ ]* 6.5 Escribir property tests para BehaviorSimulator
    - **Property 4: Fatigue Monotonic Increase** - Verificar incremento monotónico
    - **Property 5: Fatigue Effects on Performance** - Verificar que f2 > f1 implica tiempos mayores
    - **Property 7: AFK Pause Resets Fatigue** - Verificar reset a [0.2, 0.4]
    - **Property 9: Error Rate Matches Configuration** - Verificar tasa de errores en 1000+ acciones
    - **Valida: Requisitos 2.1, 2.2, 2.3, 2.5, 2.7, 3.1, 3.7**

  - [ ]* 6.6 Escribir unit tests para BehaviorSimulator
    - Test que fatiga incrementa con tiempo
    - Test que fatiga no excede 1.0
    - Test que AFK pause resetea fatiga
    - Test que errores se generan según probabilidades
    - Test que teclas adyacentes son correctas
    - _Requisitos: 2.1, 2.4, 2.5, 2.7, 3.2_

- [ ] 7. Implementar MouseMovementEngine
  - [ ] 7.1 Crear clase MouseMovementEngine con generación de curvas Bézier
    - Crear `core/mouse_movement_engine.py` con clase `MouseMovementEngine`
    - Implementar `__init__()` que recibe `MouseConfig`
    - Implementar `generate_bezier_path()` que genera trayectoria con curvas Bézier cúbicas
    - Calcular puntos de control con offset aleatorio [10-30% de distancia]
    - Generar 50 puntos en la curva usando fórmula de Bézier
    - _Requisitos: 4.1, 4.2_

  - [ ] 7.2 Implementar micro-movimientos y perfil de velocidad
    - Implementar `apply_micro_movements()` que agrega perturbaciones de 1-3 píxeles
    - Implementar `calculate_velocity_profile()` con aceleración-desaceleración (sigmoide)
    - Aplicar micro-movimientos solo a puntos intermedios (no primero ni último)
    - _Requisitos: 4.3, 4.4_

  - [ ] 7.3 Implementar overshooting
    - Implementar `should_overshoot()` que retorna True con probabilidad 0.3
    - Implementar `generate_overshoot_point()` que calcula punto más allá del objetivo
    - Implementar `calculate_approach_vector()` desde últimos 5 puntos de trayectoria
    - Offset de overshoot: [5-15 píxeles]
    - _Requisitos: 4.5_

  - [ ]* 7.4 Escribir property tests para MouseMovementEngine
    - **Property 15: Bézier Path Curvature** - Verificar curvatura > 0.01 en todos los segmentos
    - **Property 19: Overshoot Probability** - Verificar ~30% en 100+ movimientos
    - **Property 21: Target Accuracy** - Verificar llegada dentro de 2 píxeles
    - **Valida: Requisitos 4.1, 4.5, 4.7, 4.8**

  - [ ]* 7.5 Escribir unit tests para MouseMovementEngine
    - Test que trayectoria tiene 50 puntos
    - Test que primer punto es start y último es end (±2 píxeles)
    - Test que puntos de control están offset del camino directo
    - Test que overshooting genera punto más allá del objetivo
    - _Requisitos: 4.1, 4.2, 4.5, 4.7_

- [ ] 8. Checkpoint - Validar componentes core
  - Ejecutar todos los tests de timing, behavior y mouse
  - Verificar que distribuciones estadísticas son correctas
  - Asegurar que todos los tests pasan

---

## Fase 3: Componentes Avanzados


- [ ] 9. Implementar ArduinoHIDController (opcional)
  - [ ] 9.1 Crear clase ArduinoHIDController con detección y conexión
    - Crear `core/arduino_hid_controller.py` con clase `ArduinoHIDController`
    - Implementar `__init__()` que recibe `ArduinoConfig` y `fallback_controller`
    - Implementar `initialize()` que escanea puertos COM y detecta Arduino
    - Enviar comando PING y esperar PONG con timeout 500ms
    - Marcar como disponible si Arduino responde
    - _Requisitos: 5.1, 5.4_

  - [ ] 9.2 Implementar envío de comandos serial
    - Implementar `send_key_press()` que serializa y envía comando KEY_PRESS
    - Implementar `send_mouse_move()` que serializa y envía comando MOUSE_MOVE
    - Implementar `send_mouse_click()` que serializa y envía comando MOUSE_CLICK
    - Esperar ACK con timeout 100ms para cada comando
    - _Requisitos: 5.2, 5.5, 5.7_

  - [ ] 9.3 Implementar fallback automático
    - Implementar `_use_fallback()` que delega al InputController
    - Si Arduino no responde o timeout, usar fallback automáticamente
    - Loguear errores cuando se usa fallback
    - Implementar `is_available()` y `close()`
    - _Requisitos: 5.3, 5.6_

  - [ ]* 9.4 Escribir property test para serialización de comandos
    - **Property 22: Arduino Command Round-Trip**
    - **Valida: Requisitos 5.8**
    - Verificar que serializar → deserializar produce comando equivalente

  - [ ]* 9.5 Escribir unit tests para ArduinoHIDController
    - Test con Arduino no disponible (debe usar fallback)
    - Test con timeout de Arduino (debe usar fallback)
    - Test que comandos se serializan correctamente
    - Mock de puerto serial para testing
    - _Requisitos: 5.3, 5.4, 5.5, 5.6_

- [ ] 10. Implementar ProfileManager
  - [ ] 10.1 Crear clase ProfileManager con carga de perfiles
    - Crear `core/profile_manager.py` con clase `ProfileManager`
    - Implementar `__init__()` que recibe `ConfigurationParser`
    - Implementar `load_profiles()` que carga perfiles desde configuración
    - Cargar perfiles predefinidos y personalizados
    - Validar que todos los perfiles tienen parámetros requeridos
    - _Requisitos: 7.1, 7.4_

  - [ ] 10.2 Implementar cambio de perfil con transición suave
    - Implementar `set_active_profile()` que cambia perfil activo
    - Aplicar transición gradual de parámetros en 5-15 segundos
    - Calcular delta para cada parámetro y aplicar en steps de 100ms
    - Implementar `get_active_profile()` que retorna perfil actual
    - _Requisitos: 7.2, 7.3_

  - [ ] 10.3 Implementar ajustes circadianos
    - Implementar `apply_circadian_adjustments()` que ajusta según hora del día
    - 23:00-06:00: Incrementar fatiga +0.2, reaction times +15-25%
    - 06:00-10:00: Incrementar fatiga +0.1, reaction times +5-10%
    - 10:00-18:00: Parámetros normales
    - 18:00-23:00: Incrementar fatiga +0.05
    - Aplicar transiciones suaves de 10-20 minutos
    - _Requisitos: 8.1, 8.2, 8.3, 8.4, 8.5_

  - [ ] 10.4 Implementar creación de perfiles personalizados
    - Implementar `create_custom_profile()` que crea perfil desde diccionario
    - Implementar `get_profile_parameters()` que retorna parámetros de un perfil
    - Validar parámetros del perfil personalizado
    - _Requisitos: 7.4_

  - [ ]* 10.5 Escribir property tests para ProfileManager
    - **Property 27: Profile Transition Smoothness** - Verificar transición gradual sin saltos >10%
    - **Property 28: Circadian Adjustments Correctness** - Verificar ajustes según hora
    - **Valida: Requisitos 7.3, 8.2, 8.3, 8.4**

  - [ ]* 10.6 Escribir unit tests para ProfileManager
    - Test que perfiles se cargan correctamente
    - Test que cambio de perfil aplica nuevos parámetros
    - Test que ajustes circadianos se aplican según hora
    - Test que perfil personalizado se crea correctamente
    - _Requisitos: 7.1, 7.2, 7.4, 8.2, 8.3_

- [ ] 11. Checkpoint - Validar componentes avanzados
  - Ejecutar tests de Arduino (con mock) y ProfileManager
  - Verificar transiciones suaves de perfiles
  - Asegurar que todos los tests pasan

---

## Fase 4: Orquestador y Métricas


- [ ] 12. Implementar MetricsCollector
  - [ ] 12.1 Crear clase MetricsCollector con registro de métricas
    - Crear `core/metrics_collector.py` con clase `MetricsCollector`
    - Implementar `__init__()` que recibe directorio de logs
    - Implementar `record_key_press()` que registra tecla, duración, reaction time, error
    - Implementar `record_mouse_movement()` que registra movimiento con duración y path length
    - Implementar `record_error()` que registra tipo de error
    - Implementar `record_afk_pause()` que registra duración de pausa
    - Usar estructuras de datos eficientes (deques con maxlen=10000)
    - _Requisitos: 9.1, 9.2, 9.3, 9.4, 9.5_

  - [ ] 12.2 Implementar cálculo de estadísticas
    - Implementar `get_statistics()` que calcula media, mediana, std, min, max, percentiles
    - Calcular estadísticas para reaction times, key press durations, mouse movements
    - Calcular tasas de error por tipo
    - Incluir total de inputs y duración de sesión
    - _Requisitos: 9.2, 9.3, 9.4, 9.5_

  - [ ] 12.3 Implementar generación de reportes y logging
    - Implementar `generate_report()` que exporta estadísticas a JSON
    - Implementar `log_with_timestamp()` con precisión de microsegundos
    - Implementar `rotate_logs()` con rotación diaria automática
    - Formato de log: `[YYYY-MM-DD HH:MM:SS.ffffff] [LEVEL] message`
    - _Requisitos: 9.6, 9.7_

  - [ ]* 12.4 Escribir property tests para MetricsCollector
    - **Property 31: Metrics Accuracy** - Verificar que estadísticas calculadas coinciden con distribución real
    - **Property 32: Metrics Report Format** - Verificar que JSON es válido y parseable
    - **Property 33: Timestamp Precision** - Verificar precisión de microsegundos
    - **Valida: Requisitos 9.2, 9.3, 9.4, 9.5, 9.6, 9.7**

  - [ ]* 12.5 Escribir unit tests para MetricsCollector
    - Test que métricas se registran correctamente
    - Test que estadísticas se calculan correctamente
    - Test que reporte JSON es válido
    - Test que logs tienen formato correcto con microsegundos
    - Test que rotación de logs funciona
    - _Requisitos: 9.1, 9.2, 9.6, 9.7_

- [ ] 13. Implementar HumanInputSystem (orquestador principal)
  - [ ] 13.1 Crear clase HumanInputSystem con inicialización de componentes
    - Crear `core/human_input_system.py` con clase `HumanInputSystem`
    - Implementar `__init__()` que recibe config_path y input_controller
    - Inicializar todos los componentes: TimingHumanizer, BehaviorSimulator, MouseMovementEngine, ArduinoHIDController, ProfileManager, MetricsCollector
    - Inicializar estado: session_start, last_input_time, humanization_enabled
    - Intentar inicializar Arduino si está habilitado en configuración
    - _Requisitos: 10.1, 10.6_

  - [ ] 13.2 Implementar método press_key con humanización completa
    - Implementar `press_key()` que aplica todas las capas de humanización
    - Flujo: actualizar fatiga → verificar AFK → verificar error → generar timing → ejecutar input → registrar métricas
    - Manejar errores: wrong_key, double_press, hesitation
    - Aplicar reaction delay antes de ejecutar
    - Usar Arduino si disponible, sino fallback
    - _Requisitos: 1.1, 1.2, 1.3, 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 3.5, 10.2_

  - [ ] 13.3 Implementar método move_mouse con humanización completa
    - Implementar `move_mouse()` que genera trayectoria Bézier
    - Aplicar miss-click error si corresponde
    - Generar path con MouseMovementEngine
    - Calcular duración basada en distancia
    - Ejecutar movimiento punto por punto con micro-delays
    - Aplicar overshooting si corresponde
    - Registrar métricas
    - _Requisitos: 3.4, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 10.2_

  - [ ] 13.4 Implementar métodos auxiliares y gestión de estado
    - Implementar `click()` que combina move_mouse (opcional) + click
    - Implementar `release_key()` con humanización
    - Implementar `_execute_key_press()` que usa Arduino o fallback
    - Implementar `_handle_afk_pause()` que ejecuta pausa y warm-up
    - Implementar `enable_humanization()` para habilitar/deshabilitar
    - _Requisitos: 10.2, 10.5, 11.2, 11.3_

  - [ ] 13.5 Implementar métodos de gestión y configuración
    - Implementar `set_profile()` que delega a ProfileManager
    - Implementar `reload_config()` que recarga configuración sin reiniciar
    - Implementar `get_metrics()` que retorna métricas del MetricsCollector
    - _Requisitos: 6.7, 7.2, 9.6_

  - [ ]* 13.6 Escribir property tests para HumanInputSystem
    - **Property 34: Humanization Bypass Performance** - Verificar overhead <1ms cuando deshabilitado
    - **Property 35: Humanization Application** - Verificar que se aplica al menos una capa
    - **Property 43: Non-Deterministic Sequences** - Verificar que secuencias son diferentes
    - **Valida: Requisitos 10.2, 10.5, 13.2**

  - [ ]* 13.7 Escribir unit tests para HumanInputSystem
    - Test que press_key aplica humanización cuando habilitado
    - Test que press_key no aplica humanización cuando deshabilitado
    - Test que move_mouse genera trayectoria Bézier
    - Test que errores se aplican correctamente
    - Test que AFK pause se ejecuta y resetea fatiga
    - Test que métricas se registran
    - _Requisitos: 10.2, 10.5, 11.2, 11.3_

- [ ] 14. Checkpoint - Validar orquestador completo
  - Ejecutar tests de MetricsCollector y HumanInputSystem
  - Verificar flujo completo de input con todas las capas
  - Asegurar que todos los tests pasan

---

## Fase 5: Testing Exhaustivo


- [ ] 15. Implementar tests de integración
  - [ ]* 15.1 Escribir test de flujo completo de input
    - Test que verifica flujo: Bot → HumanInputSystem → Timing → Behavior → Arduino/Fallback
    - Verificar que se aplican todas las capas de humanización
    - Verificar que métricas se registran correctamente
    - Usar mocks para InputController y Arduino
    - _Requisitos: 10.1, 10.2, 10.3, 10.4_

  - [ ]* 15.2 Escribir test de cambio de perfil en runtime
    - Test que cambia de perfil "default" a "experto"
    - Verificar transición suave de parámetros
    - Verificar que inputs reflejan nuevo perfil
    - _Requisitos: 7.2, 7.3_

  - [ ]* 15.3 Escribir test de manejo de errores en cascada
    - Simular fallo de Arduino → verificar fallback
    - Simular fallo de configuración → verificar uso de defaults
    - Verificar que sistema continúa operando con funcionalidad reducida
    - _Requisitos: 14.1, 14.2, 14.3, 14.4, 14.5_

  - [ ]* 15.4 Escribir test de sesión larga con fatiga
    - Simular sesión de 2 horas (acelerar tiempo)
    - Verificar incremento gradual de fatiga
    - Verificar que se generan pausas AFK
    - Verificar reset de fatiga después de pausas
    - _Requisitos: 2.1, 2.4, 2.5, 11.1, 11.2_

- [ ] 16. Implementar tests de validación estadística
  - [ ]* 16.1 Crear modo de testing estadístico
    - Implementar flag `enable_statistical_validation` en configuración
    - Cuando habilitado, generar 10,000 samples de cada tipo de delay
    - Ejecutar tests estadísticos: Kolmogorov-Smirnov, Chi-cuadrado
    - _Requisitos: 12.1, 12.2_

  - [ ]* 16.2 Escribir test de distribución de reaction times
    - Generar 10,000 samples de reaction times
    - Verificar que pasa test de normalidad (K-S test, p > 0.05)
    - Verificar que media y std están dentro de 3% de valores configurados
    - _Requisitos: 12.3, 12.4_

  - [ ]* 16.3 Escribir test de distribución de key press durations
    - Generar 10,000 samples de key press durations
    - Verificar normalidad y estadísticas
    - _Requisitos: 12.3_

  - [ ]* 16.4 Escribir test de distribución de errores
    - Generar 10,000 acciones con error_rate configurado
    - Verificar que tasa de errores observada está dentro de 10% de configurada
    - Verificar distribución de tipos de error
    - _Requisitos: 12.4_

  - [ ]* 16.5 Generar gráficos de distribuciones (opcional)
    - Implementar generación de histogramas en PNG
    - Graficar distribuciones de reaction times, key press durations, errores
    - Comparar contra distribuciones teóricas
    - _Requisitos: 12.6_

- [ ] 17. Implementar tests de performance
  - [ ]* 17.1 Escribir test de overhead de humanización
    - Medir tiempo de ejecución con humanización deshabilitada (baseline)
    - Medir tiempo con humanización habilitada (sin delays reales)
    - Verificar que overhead por input es < 5ms
    - _Requisitos: 10.5, NFR: Performance_

  - [ ]* 17.2 Escribir test de throughput
    - Verificar que sistema soporta > 100 inputs/segundo
    - Medir uso de memoria durante ejecución
    - Verificar que uso de memoria < 50MB
    - _Requisitos: NFR: Performance_

- [ ] 18. Implementar tests de manejo de errores
  - [ ]* 18.1 Escribir tests de errores de configuración
    - Test con YAML inválido → debe usar defaults y loguear
    - Test con parámetros fuera de rango → debe usar defaults
    - Test con archivo no encontrado → debe usar defaults
    - _Requisitos: 14.1, 14.3_

  - [ ]* 18.2 Escribir tests de errores de hardware
    - Test con Arduino no detectado → debe usar fallback
    - Test con Arduino desconectado durante operación → debe usar fallback
    - Test con timeout de Arduino → debe usar fallback
    - _Requisitos: 14.1, 14.2_

  - [ ]* 18.3 Escribir tests de recuperación de componentes
    - Test que componente falla y se reintenta hasta 3 veces
    - Test que componente se deshabilita después de 3 fallos
    - Test que sistema continúa con funcionalidad reducida
    - _Requisitos: 14.4, 14.5_

- [ ] 19. Checkpoint - Validar cobertura de tests
  - Ejecutar todos los tests (unit, property, integration)
  - Verificar cobertura de código > 85%
  - Verificar que todas las propiedades de correctitud pasan
  - Asegurar que todos los tests pasan

---

## Fase 6: Integración con Bot Existente


- [ ] 20. Crear wrapper compatible con InputController existente
  - [ ] 20.1 Verificar interfaz del InputController actual
    - Revisar métodos del InputController existente del bot
    - Documentar firma de métodos: press_key, release_key, move_mouse, click
    - Identificar diferencias con interfaz de HumanInputSystem
    - _Requisitos: 10.1, 10.4_

  - [ ] 20.2 Ajustar interfaz de HumanInputSystem para compatibilidad
    - Modificar firmas de métodos si es necesario para compatibilidad total
    - Asegurar que HumanInputSystem puede ser drop-in replacement
    - Mantener compatibilidad con código existente del bot
    - _Requisitos: 10.1, 10.4_

  - [ ] 20.3 Crear script de ejemplo de integración
    - Crear `examples/integration_example.py`
    - Mostrar cómo reemplazar InputController con HumanInputSystem
    - Incluir ejemplo de configuración básica
    - Incluir ejemplo de cambio de perfil
    - _Requisitos: 10.1_

- [ ] 21. Implementar integración con estado del bot
  - [ ] 21.1 Crear interfaz para estado crítico del bot
    - Crear `utils/bot_state_interface.py` con interfaz abstracta
    - Definir métodos: `is_in_combat()`, `get_hp_percentage()`, `is_in_safe_zone()`
    - Documentar cómo el bot debe implementar esta interfaz
    - _Requisitos: 11.4_

  - [ ] 21.2 Integrar verificación de estado crítico en BehaviorSimulator
    - Modificar `is_in_critical_situation()` para usar interfaz de estado
    - Verificar combate, HP bajo, zona peligrosa antes de AFK pause
    - Hacer interfaz opcional (si no está disponible, asumir no crítico)
    - _Requisitos: 11.4_

  - [ ] 21.3 Crear ejemplo de implementación de interfaz de estado
    - Crear `examples/bot_state_implementation.py`
    - Mostrar cómo implementar interfaz de estado en bot existente
    - Incluir ejemplos de verificación de combate y HP
    - _Requisitos: 11.4_

- [ ] 22. Implementar sistema de hot-reload de configuración
  - [ ] 22.1 Implementar detección de cambios en archivo de configuración
    - Usar watchdog o polling para detectar cambios en config.yaml
    - Implementar método `_watch_config_file()` en HumanInputSystem
    - _Requisitos: 6.7_

  - [ ] 22.2 Implementar recarga segura de configuración
    - Implementar `reload_config()` que recarga sin interrumpir operación
    - Validar nueva configuración antes de aplicar
    - Si configuración inválida, mantener configuración actual y loguear error
    - Aplicar cambios gradualmente (transición suave)
    - _Requisitos: 6.7_

  - [ ]* 22.3 Escribir tests de hot-reload
    - Test que detecta cambios en archivo de configuración
    - Test que recarga configuración válida correctamente
    - Test que mantiene configuración actual si nueva es inválida
    - _Requisitos: 6.7_

- [ ] 23. Checkpoint - Validar integración completa
  - Verificar que HumanInputSystem es compatible con InputController
  - Probar integración con bot existente (si disponible)
  - Verificar hot-reload de configuración
  - Asegurar que todos los tests pasan

---

## Fase 7: Documentación y Deployment

- [ ] 24. Crear documentación de usuario
  - [ ] 24.1 Crear README.md principal
    - Descripción general del sistema
    - Características principales
    - Requisitos del sistema (Python 3.11+, dependencias)
    - Instrucciones de instalación
    - Ejemplo de uso básico
    - _Requisitos: Todos_

  - [ ] 24.2 Crear guía de configuración
    - Crear `docs/configuration_guide.md`
    - Documentar todos los parámetros de config.yaml
    - Explicar rangos válidos y valores recomendados
    - Incluir ejemplos de perfiles personalizados
    - Documentar ajustes circadianos
    - _Requisitos: 6.1, 6.3, 7.4, 8.1, 8.2, 8.3_

  - [ ] 24.3 Crear guía de integración
    - Crear `docs/integration_guide.md`
    - Explicar cómo integrar con bot existente
    - Documentar interfaz de estado del bot
    - Incluir ejemplos de código
    - Troubleshooting común
    - _Requisitos: 10.1, 10.4, 11.4_

  - [ ] 24.4 Crear guía de Arduino HID (opcional)
    - Crear `docs/arduino_guide.md`
    - Listar hardware compatible (Leonardo, Micro, Pro Micro)
    - Instrucciones de instalación de firmware
    - Configuración de puerto serial
    - Troubleshooting de Arduino
    - _Requisitos: 5.1, 5.4_

- [ ] 25. Crear documentación técnica
  - [ ] 25.1 Documentar arquitectura del sistema
    - Crear `docs/architecture.md`
    - Incluir diagramas de componentes
    - Explicar flujo de datos
    - Documentar decisiones de diseño
    - _Requisitos: Todos_

  - [ ] 25.2 Documentar API de componentes
    - Generar documentación de API con docstrings
    - Documentar interfaces públicas de cada componente
    - Incluir ejemplos de uso de cada componente
    - _Requisitos: Todos_

  - [ ] 25.3 Documentar propiedades de correctitud
    - Crear `docs/correctness_properties.md`
    - Listar todas las 48 propiedades
    - Explicar qué valida cada propiedad
    - Referenciar tests que verifican cada propiedad
    - _Requisitos: 15.1_


- [ ] 26. Crear firmware de Arduino
  - [ ]* 26.1 Implementar firmware básico de Arduino
    - Crear `arduino/human_input_hid/human_input_hid.ino`
    - Implementar comunicación serial (115200 baud)
    - Implementar parsing de comandos: KEY_PRESS, MOUSE_MOVE, MOUSE_CLICK, PING
    - Implementar respuestas: ACK, PONG, ERROR
    - Usar librerías Keyboard.h y Mouse.h
    - _Requisitos: 5.1, 5.2, 5.7_

  - [ ]* 26.2 Implementar comandos de teclado en Arduino
    - Implementar procesamiento de KEY_PRESS con duración
    - Implementar KEY_RELEASE
    - Mapear códigos de teclas a constantes de Arduino
    - _Requisitos: 5.7_

  - [ ]* 26.3 Implementar comandos de mouse en Arduino
    - Implementar MOUSE_MOVE (relativo y absoluto)
    - Implementar MOUSE_CLICK (left, right, middle)
    - _Requisitos: 5.7_

  - [ ]* 26.4 Crear instrucciones de carga de firmware
    - Crear `arduino/README.md` con instrucciones paso a paso
    - Incluir screenshots de Arduino IDE
    - Documentar selección de board y puerto
    - Incluir troubleshooting
    - _Requisitos: 5.1_

- [ ] 27. Preparar para deployment
  - [ ] 27.1 Crear setup.py para instalación
    - Crear `setup.py` con metadata del paquete
    - Definir dependencias: numpy, scipy, PyYAML, pyserial, hypothesis, pytest
    - Configurar entry points si es necesario
    - Incluir archivos de configuración de ejemplo
    - _Requisitos: NFR: Maintainability_

  - [ ] 27.2 Crear requirements.txt
    - Listar todas las dependencias con versiones
    - Separar dependencias de producción y desarrollo
    - Crear `requirements-dev.txt` para testing
    - _Requisitos: NFR: Compatibility_

  - [ ] 27.3 Crear archivo de configuración de ejemplo
    - Copiar `config.yaml` a `config.example.yaml`
    - Agregar comentarios explicativos extensos
    - Incluir todos los perfiles predefinidos
    - Documentar cada parámetro inline
    - _Requisitos: 6.1_

  - [ ] 27.4 Crear script de instalación
    - Crear `install.sh` (Linux/Mac) y `install.bat` (Windows)
    - Automatizar instalación de dependencias
    - Verificar versión de Python
    - Crear directorios necesarios (logs, etc.)
    - _Requisitos: NFR: Compatibility_

- [ ] 28. Crear tests de aceptación
  - [ ]* 28.1 Crear test de aceptación end-to-end
    - Test que simula sesión completa de bot
    - Verificar que todos los componentes funcionan juntos
    - Verificar que métricas son realistas
    - Generar reporte de sesión
    - _Requisitos: Todos_

  - [ ]* 28.2 Crear test de validación de distribuciones
    - Ejecutar modo de validación estadística
    - Generar 10,000 samples de cada tipo
    - Verificar que todas las distribuciones pasan tests estadísticos
    - Generar gráficos de distribuciones
    - _Requisitos: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6_

  - [ ]* 28.3 Crear test de anti-detección
    - Verificar que nombres de módulos no contienen términos sospechosos
    - Verificar que secuencias no son determinísticas
    - Verificar randomización de inicialización
    - Verificar presencia de jitter en loops
    - _Requisitos: 13.1, 13.2, 13.5, 13.6_

- [ ] 29. Checkpoint final - Validación completa del sistema
  - Ejecutar suite completa de tests (unit, property, integration, acceptance)
  - Verificar cobertura de código > 85%
  - Verificar que todas las 48 propiedades de correctitud pasan
  - Generar reporte de validación estadística
  - Revisar documentación completa
  - Verificar que instalación funciona en sistema limpio

---

## Notas de Implementación

### Orden de Ejecución

Las tareas deben ejecutarse en orden secuencial por fase. Cada fase construye sobre la anterior:

1. **Fase 1** establece la base: modelos de datos y configuración
2. **Fase 2** implementa los componentes core de humanización
3. **Fase 3** agrega componentes avanzados (Arduino, perfiles)
4. **Fase 4** integra todo con el orquestador y métricas
5. **Fase 5** valida exhaustivamente con tests
6. **Fase 6** integra con el bot existente
7. **Fase 7** documenta y prepara para deployment

### Tareas Opcionales

Las tareas marcadas con `*` son opcionales y pueden omitirse para un MVP:

- Todos los property-based tests (pueden agregarse después)
- Todos los unit tests (aunque altamente recomendados)
- Tests de integración y validación estadística
- Implementación completa de Arduino HID
- Generación de gráficos de distribuciones
- Tests de performance detallados

### MVP Mínimo

Para un MVP funcional, implementar:
- Fase 1 completa (modelos y configuración)
- Fase 2 completa (componentes core)
- Fase 3: Solo ProfileManager (omitir Arduino)
- Fase 4 completa (orquestador y métricas)
- Fase 6: Tareas 20.1-20.3 (integración básica)
- Fase 7: Tarea 24.1 (README básico)

### Checkpoints

Los checkpoints son momentos para:
- Ejecutar todos los tests implementados hasta ese punto
- Verificar que no hay regresiones
- Hacer preguntas al usuario si hay dudas
- Validar que la implementación va por buen camino

### Referencias a Requisitos

Cada tarea incluye referencias a los requisitos que implementa (formato: `_Requisitos: X.Y, X.Z_`). Esto permite:
- Trazabilidad completa desde requisitos hasta implementación
- Verificar que todos los requisitos están cubiertos
- Identificar qué requisitos se ven afectados por cambios

### Property-Based Tests

Cada property test debe:
- Referenciar explícitamente su número de propiedad del diseño
- Indicar qué requisitos valida
- Usar formato: `**Property N: Título** - **Valida: Requisitos X.Y**`
- Ejecutar mínimo 100 iteraciones (configurar en Hypothesis)

### Cobertura de Propiedades

El sistema tiene 48 propiedades de correctitud distribuidas así:
- Propiedades 1-3: TimingHumanizer (distribuciones gaussianas)
- Propiedades 4-8: BehaviorSimulator (fatiga)
- Propiedades 9-14: BehaviorSimulator (errores)
- Propiedades 15-21: MouseMovementEngine (curvas Bézier)
- Propiedades 22-23: ArduinoHIDController
- Propiedades 24-26: ConfigurationParser
- Propiedades 27-30: ProfileManager (perfiles y circadiano)
- Propiedades 31-33: MetricsCollector
- Propiedades 34-35: HumanInputSystem (orquestador)
- Propiedades 36-40: AFK pauses
- Propiedades 41: Validación estadística
- Propiedades 42-45: Anti-detección
- Propiedades 46-48: Manejo de errores

Todas estas propiedades deben ser verificadas mediante property-based tests en la Fase 5.

