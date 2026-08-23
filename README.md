# Bot de despacho de ambulancias para Telegram

MVP funcional para controlar el estado de unidades desde Telegram.

## Funciones

- Tablero de ambulancias con estados:
  - 🟢 Disponible
  - 🟡 Fuera de servicio
  - 🔴 En servicio
- Selección de ambulancia con botones.
- Despacho de una unidad.
- Captura de ubicación enviada desde Telegram.
- Guarda coordenadas y genera enlace de Google Maps.
- Regreso automático de una unidad a disponible.
- Registro de movimientos.
- Base de datos SQLite local.
- Restricción opcional por Telegram User ID.

## Instalación

1. Crea un bot con @BotFather.
2. Copia el token.
3. Instala Python 3.11 o superior.
4. En esta carpeta:

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

Linux/macOS:

```bash
source .venv/bin/activate
```

Instala dependencias:

```bash
pip install -r requirements.txt
```

Copia `.env.example` a `.env` y coloca el token:

```text
TELEGRAM_BOT_TOKEN=123456:ABCDEF...
```

Ejecuta:

```bash
python bot.py
```

## Uso

En Telegram abre el bot y escribe:

```text
/start
```

Comandos disponibles:

- `/start` — abre el tablero
- `/ambulancias` — muestra el tablero
- `/historial` — últimos movimientos
- `/miid` — muestra tu Telegram User ID

## Nota sobre ubicación

Cuando despachas una ambulancia, el bot solicita una ubicación. Puedes usar el botón
"📍 Enviar mi ubicación" o adjuntar manualmente una ubicación desde Telegram.

El botón automático de ubicación está disponible en chats privados. Para una versión
con selección libre de destino sobre Google Maps dentro de la interfaz, se recomienda
crear una Telegram Mini App.
