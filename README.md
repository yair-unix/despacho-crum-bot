# Despacho CRUM — Simple Full States

Versión simple en SQLite. No usa PostgreSQL ni SQLAlchemy.

## Ambulancias
674–695

Flujo:
Disponible → Despachada → En labor

Con traslado:
En labor → En traslado → Arribo hospital → Salida hospital → Disponible / No disponible

Sin traslado:
En labor → Fin del servicio → Disponible / No disponible

## Vectores
- V07
- V08
- V15

Flujo:
Disponible → Despachado → En labor → Fin del servicio → Disponible / No disponible

Los vectores no muestran:
- En traslado
- Arribo hospital
- Salida hospital

## Funciones
- Link Google Maps al despachar
- Motivo libre de No disponible
- Historial básico
- SQLite
- Sin SQLAlchemy
