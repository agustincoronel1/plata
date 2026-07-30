# Plata — definición de producto

## Problema

El saldo bancario no es dinero disponible.

Cuando alguien abre su aplicación bancaria y ve un número, ese número todavía incluye el
alquiler que vence la semana que viene, las cuotas de la compra de hace tres meses, los
servicios, las suscripciones y cualquier otro compromiso ya asumido. Gastar contra ese
número es gastar contra plata que ya tiene dueño.

Las apps de finanzas personales que existen agravan el problema de dos maneras. Las
simples muestran el saldo y nada más. Las completas piden categorizar, presupuestar y
mantener el sistema al día, y exigen tanto trabajo que se abandonan a las dos semanas.

Ninguna de las dos responde la pregunta que la persona realmente se hace, varias veces
por día, parada frente a una decisión concreta: **¿puedo gastar esto?**

## Usuario inicial

Una persona con ingreso mensual más o menos previsible —sueldo, monotributo, o una
mezcla— que vive en Argentina y tiene gastos fijos y compras en cuotas.

Características que definen el foco:

- No lleva un presupuesto formal y ya intentó llevarlo antes sin éxito.
- Tiene varias compras en cuotas simultáneas y no sabe con precisión cuánto suman.
- Toma decisiones de gasto chicas y frecuentes, no grandes y planificadas.
- Va a abandonar cualquier herramienta que le exija más de unos segundos por día.

No es el usuario inicial: quien busca contabilidad detallada, inversiones, gestión de
patrimonio o finanzas compartidas entre varias personas.

## Propuesta de valor

> No te dice solamente cuánto dinero tenés. Te dice cuánto podés usar.

Plata toma el saldo, resta todo lo que ya está comprometido hasta fin de mes y devuelve
un número accionable: cuánto se puede gastar hoy sin comprometer el resto del mes.

Tres decisiones de producto se derivan de eso:

- **Una respuesta, no un tablero.** La pantalla principal muestra un número, no un
  conjunto de gráficos que hay que interpretar.
- **Registro sin fricción.** Cargar un gasto es escribir una frase. Sin formularios,
  sin categorías obligatorias, sin campos.
- **Mirar hacia adelante.** El valor está en anticipar el impacto de una compra, no en
  reportar lo que ya pasó.

## Alcance del MVP

El MVP se sostiene sobre tres funcionalidades:

**1. Disponible real y límite diario de gasto**

Calcular el dinero efectivamente disponible descontando los compromisos pendientes
hasta fin de mes y un único monto protegido —la reserva de ahorro—, y derivar de ahí un
límite de gasto diario.

**2. Registro de gastos en lenguaje natural con IA**

El usuario escribe una frase (`café 3500`, `super 42 mil`, `nafta ayer 30000`) y el
sistema la interpreta para registrar el gasto: monto, descripción, fecha.

**3. Simulación de compras y cuotas**

Antes de comprar, simular el impacto: cuánto baja el disponible de este mes y de los
meses siguientes si la compra se hace en cuotas.

Además, para que el MVP sea presentable: datos demo cargados, aplicación desplegada y
un frontend que muestre las tres funcionalidades de forma clara.

## Fuera del alcance del MVP

Excluido de forma deliberada, no por falta de tiempo:

- Integración con bancos, tarjetas o scraping de resúmenes.
- Presupuestos por categoría y sistema de categorización manual.
- Reportes históricos, gráficos y analítica.
- Sistema avanzado de metas de ahorro: múltiples objetivos, fechas límite y seguimiento
  de progreso. El MVP sí contempla un único monto protegido —una reserva de ahorro— que
  se resta al calcular el dinero disponible.
- Inversiones.
- Notificaciones, alertas, mails y push.
- Aplicación móvil nativa.
- Múltiples monedas y manejo de inflación proyectada.
- Finanzas compartidas entre varias personas.
- Exportación de datos.

## Saldo, movimientos y compromisos

Dos conceptos que no hay que confundir:

- El **saldo** (`current_balance`) es la plata que la persona tiene hoy. Un **movimiento**
  —un ingreso o un gasto ya ocurrido— lo modifica: registrar un gasto baja el saldo,
  registrar un ingreso lo sube.
- Un **compromiso** es plata que ya tiene dueño pero todavía no se pagó (el alquiler que
  vence, la cuota que se va a debitar). Un compromiso **no** cambia el saldo: es dinero
  comprometido, no dinero que ya salió. Marcar un compromiso como pagado es un cambio de
  estado, no un movimiento; el día que efectivamente se pague, eso se registra como un
  gasto aparte.

De esta distinción nace la propuesta de valor: el **disponible real** es el saldo menos
los compromisos pendientes menos la reserva protegida.

## El motor financiero

El corazón del producto es un cálculo **determinístico**: mismos datos, mismo resultado.
No usa IA, no estima gastos futuros que el usuario no cargó, y nunca muestra un número
inventado. Decisiones de producto que lo guían:

- **Disponible real y límite diario.** El disponible es
  `saldo − compromisos pendientes hasta el próximo ingreso − dinero protegido − margen de
  seguridad`. De ahí sale el número accionable: cuánto se puede gastar por día hasta cobrar,
  **truncando hacia abajo** para pecar de conservador. Si falta la fecha del próximo ingreso,
  Plata lo dice y no calcula un diario poco confiable.

- **Honestidad ante los números feos.** Si los compromisos y las reservas superan el saldo,
  la app no muestra un negativo como "disponible para gastar": muestra cero y explica cuánto
  falta, sin culpabilizar. Un compromiso **vencido pero impago** sigue contando como dinero
  comprometido.

- **Simular antes de gastar.** El usuario ingresa el total final de una compra en cuotas
  (Plata **no** calcula intereses: usa el monto que la persona ya va a pagar) y ve el impacto
  mes a mes, incluido si algún mes rompe sus reservas. Además compara empezar ahora contra
  empezar el mes siguiente. La conclusión es **neutral** —"dentro del margen" o "supera las
  reservas"—: Plata no le dice a nadie que compre o no compre.

- **No es asesoramiento financiero.** Plata es una herramienta de organización y simulación.
  Ayuda a ver, no reemplaza el criterio de la persona ni constituye consejo financiero.

## Restricciones técnicas

- El dinero se representa con `Decimal` en Python y `Numeric` en PostgreSQL. Nunca con
  floats.
- Un solo backend y un solo frontend. Sin microservicios.
- Sin Redis, Celery, Kafka ni servicios externos más allá del proveedor de IA.
- La arquitectura acompaña el alcance: no se agregan abstracciones antes de necesitarlas.

## Decisiones del flujo asistido por IA

- **El modelo propone, la persona dispone.** La IA nunca calcula el saldo, ejecuta SQL, crea
  movimientos ni marca compromisos. Interpreta lenguaje natural y orquesta herramientas
  acotadas; el dinero solo cambia en `transaction_service`, tras confirmación humana explícita.
- **Human-in-the-loop obligatorio.** `parse` produce un borrador editable; nada se persiste
  hasta `confirm`. En el copiloto, las escrituras pausan el grafo (`interrupt_before`) hasta
  aprobar. El saldo cambia exactamente una vez, en un único commit.
- **Confirmación atómica.** Dos confirmaciones simultáneas del mismo borrador crean un solo
  movimiento (claim con UPDATE condicional); la segunda recibe 409. Si la base falla, el
  borrador vuelve a `pending` para reintentar.
- **Determinismo separado del LLM.** Las sumas y proyecciones las hace el motor financiero o
  SQL; el RAG solo *encuentra* movimientos, nunca los suma. Un verificador determinístico
  rechaza montos sin respaldo antes de mostrar una respuesta.
- **Mock por defecto.** Todo (dev, tests, evaluaciones) corre sin API key ni coste con
  proveedores mock deterministas. El proveedor real es opcional y falla solo al usarse sin key.
- **Privacidad y trazabilidad.** No se loguean ni devuelven texto del usuario, montos, prompts,
  respuestas crudas ni API keys por defecto. Las trazas registran metadatos (intención, tools,
  duraciones, verificador, aprobación), no contenido.
- **Alcance acotado.** Solo ARS; sin voz, OCR, PDFs, extractos bancarios ni multiagente.

## Decisiones de la primera experiencia

- **Sin perfil no hay dashboard.** Mientras la API responda `404` en el perfil, se muestra una
  pantalla de bienvenida que explica para qué sirve Plata y ofrece una única acción, que abre
  el formulario de perfil existente. No se renderiza el dashboard con ceros ni errores, y no
  se abre ningún modal automáticamente.
- **Un estado vacío no es un error.** Cada sección explica qué falta y ofrece la acción que ya
  existe para resolverlo, en vez de mostrar una lista en blanco o esconderse.
- **La sección de compromisos no se oculta nunca.** Aunque esté vacía, la persona tiene que
  entender que los compromisos entran en el cálculo del disponible.
- **Simular no gasta.** El estado vacío de simulaciones lo dice explícito: una simulación no
  registra un movimiento ni modifica el saldo.
- **El copiloto sugiere, no actúa.** Sin conversación muestra ejemplos de lo que el backend ya
  sabe resolver; ninguno se envía solo, y los que implican una escritura siguen pasando por la
  aprobación humana.
