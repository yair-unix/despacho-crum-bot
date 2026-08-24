# Despacho CRUM — Simple Persistente

Versión simple SQLite diseñada para uso compartido en grupos de Telegram.

## Comportamiento de mensajes

- El tablero permanece visible.
- Seleccionar una unidad crea una tarjeta nueva.
- Despachar crea una tarjeta operativa persistente.
- La tarjeta de la unidad conserva sus botones para que otro integrante continúe el flujo.
- "Ver tablero" crea un tablero nuevo y no modifica la tarjeta operativa.
- Consultar la bitácora crea un mensaje nuevo y no elimina botones ni tarjetas.

## Unidades
Ambulancias 674–695
Vectores V07, V08 y V15

## Ambulancias
Disponible → Despachada → En labor

Con traslado:
En labor → En traslado → Arribo hospital → Salida hospital → Disponible / No disponible

Sin traslado:
En labor → Fin del servicio → Disponible / No disponible

## Vectores
Disponible → Despachado → En labor → Fin del servicio → Disponible / No disponible

## Bitácora
/bitacora
/bitacora 684
/bitacora V07
