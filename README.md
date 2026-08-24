# Despacho CRUM — Simple con Bitácora

Versión simple en SQLite.

## Unidades
- Ambulancias 674–695
- Vectores V07, V08 y V15

## Comandos
- /start
- /ambulancias
- /bitacora
- /bitacora 684
- /bitacora V07

## Ambulancias
Disponible → Despachada → En labor

Con traslado:
En labor → En traslado → Arribo hospital → Salida hospital → Disponible / No disponible

Sin traslado:
En labor → Fin del servicio → Disponible / No disponible

## Vectores
Disponible → Despachado → En labor → Fin del servicio → Disponible / No disponible

## Bitácora
Registra:
- fecha/hora
- unidad
- estado
- motivo de No disponible
- destino de Google Maps al despacho
- usuario de Telegram cuando está disponible
