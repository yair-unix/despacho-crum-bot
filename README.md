# Despacho CRUM — Simple Persistente Robusto

Corrección de botones de estado y tarjetas desactualizadas.

## Mejoras
- Los botones consultan el estado real guardado en SQLite.
- Si una tarjeta quedó desactualizada, el bot publica una nueva con el estado actual.
- Si Telegram ya no permite editar una tarjeta antigua, el bot publica una nueva en lugar de fallar.
- Corrige el flujo En traslado → Arribo hospital incluso después de esperar tiempo.
- Conserva /bitacora, /borrarbitacora y /reiniciar.
- Ambulancias 674–695 y vectores V07, V08 y V15.
