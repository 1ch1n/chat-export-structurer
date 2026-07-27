<div align="center">
  <a href="https://mychatarchive.com">
    <img src="assets/mychatarchive-book-logo.png" alt="MyChatArchive" width="200">
  </a>

  <h1>MyChatArchive</h1>

  <p><strong>Tu historial de conversaciones con IA, buscable localmente por significado.</strong></p> 

  <p> 
    <a href="https://www.gnu.org/licenses/agpl-3.0">
      <img alt="License: AGPL-3.0" src="https://img.shields.io/badge/License-AGPL--3.0-blue.svg">
    </a>
    <a href="https://www.python.org/downloads/">
      <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10+-blue.svg"> 
    </a>
    <a href="https://github.com/astral-sh/ruff">
      <img alt="Code style: ruff" src="https://img.shields.io/badge/code%20style-ruff-000000.svg"> 
    </a> 
    <a href="https://github.com/1ch1n/mychatarchive/stargazers">
      <img alt="GitHub stars" src="https://img.shields.io/github/stars/1ch1n/mychatarchive?style=social"> 
    </a> 
  </p> 

  <p> 
    <a href="https://github.com/1ch1n/mychatarchive/issues">
      <img alt="PRs Welcome" src="https://img.shields.io/badge/PRs-welcome-brightgreen.svg"> 
    </a> 
    <a href="https://github.com/1ch1n/mychatarchive"> 
      <img alt="Platform" src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey.svg"> 
    </a> 
    <a href="https://mychatarchive.com"> 
      <img alt="Website" src="https://img.shields.io/badge/web-mychatarchive.com-blue"> 
    </a> 
  </p> 

  <h4> 
    <a href="#quick-start">Inicio Rápido</a> &nbsp;·&nbsp; 
    <a href="#what-you-get">Herramientas MCP</a> &nbsp;·&nbsp; 
    <a href="#the-pipeline">Pipeline</a> &nbsp;·&nbsp; 
    <a href="#connecting-to-ai-tools">Conexión</a> &nbsp;·&nbsp; 
    <a href="ROADMAP.md">Roadmap</a> &nbsp;·&nbsp; 
    <a href="https://mychatarchive.com">Sitio Web</a> 
  </h4>
</div> 

--- 

Importa tus exportaciones de chat de ChatGPT, Claude, Grok, Claude Code y Cursor. Genera embeddings vectoriales localmente con sentence-transformers. Expón todo a través de un servidor MCP que cualquier herramienta de IA puede consultar. 

> **Open core:** El pipeline local (importación, embedding, resumen, búsqueda, servidor MCP) es gratuito y tiene licencia AGPL. La sincronización en la nube y el MCP alojado están en el roadmap en [mychatarchive.com](https://mychatarchive.com). 

--- 

## Por qué MyChatArchive 

**Tu archivo, buscable.** Arrastra tus exportaciones de ChatGPT, Claude o Grok a la carpeta de importación, o deja que MyChatArchive autodetecte las sesiones de Claude Code y Cursor de tu máquina. Transcripciones completas, embeddings locales, servidor MCP. No se requiere la nube. 

| | |
|---|---|
| **Sin pérdidas** | Transcripciones completas de los mensajes, no resúmenes extraídos. Siempre puedes buscar el original. |
| **Local-first** | Un único archivo SQLite. Los embeddings se ejecutan en tu máquina. El núcleo no necesita claves de API. | 
| **Nativo para desarrolladores** | Autodetecta sesiones de Claude Code y conversaciones de Cursor desde tu máquina local desde el primer día. | 
| **Servidor MCP** | Claude Desktop, Cursor, Claude Code y cualquier cliente MCP pueden buscar en tu archivo. | 

--- 

## Inicio Rápido 

```bash
git clone https://github.com/1ch1n/mychatarchive.git
cd mychatarchive
pip install . 

# 1. Configuración (crea carpeta de importación, configura autodescubrimiento)
mychatarchive init 

# 2. Importar todo en un solo comando
#    Autodetecta Claude Code + Cursor, escanea tu carpeta de importación
mychatarchive sync 

# 3. (Opcional) Generar resúmenes de hilos para una recuperación de contexto más rica
#    Requiere una clave de API: establece OPENROUTER_API_KEY o ANTHROPIC_API_KEY
mychatarchive summarize 

# 4. Generar embeddings locales
mychatarchive embed 

# 5. Iniciar el servidor MCP
mychatarchive serve
``` 

Luego conéctate desde Claude Desktop o Cursor: ejecuta `mychatarchive mcp-config` y añade el resultado a la configuración de tu cliente. Eso es todo. 

--- 

## Herramientas MCP 

Una vez que el servidor MCP esté en funcionamiento, cualquier herramienta de IA conectada puede llamar a: 

| Herramienta | Qué hace |
|------|-------------|
| `search_brain` | Búsqueda semántica por significado en todas las conversaciones | 
| `search_recent` | Conversaciones recientes y pensamientos capturados por rango de tiempo | 
| `get_context` | Paquete de contexto completo para un tema: hilos relacionados, resúmenes de LLM, pensamientos | 
| `capture_thought` | Guarda un pensamiento o nota con auto-embedding para recuperación futura | 
| `get_profile` | Instantánea de tus áreas de enfoque recientes, resúmenes de hilos y pensamientos | 
| `get_current_datetime` | Fecha y hora UTC actual, inyectada en cada respuesta de herramienta | 

Todas las herramientas de búsqueda admiten filtrado por plataforma, rango de tiempo (`hours_back`, `since`) y grupo de hilos. Ordena por relevancia o recencia. 

**Ejemplo:** Pregunta a Claude "¿Qué decidí sobre la arquitectura de la base de datos el mes pasado?" y buscará semánticamente en tu historial de conversaciones real. 

--- 

## Instalación 

### Desde el código fuente (recomendado por ahora) 

```bash
git clone https://github.com/1ch1n/mychatarchive.git
cd mychatarchive
pip install . 
``` 

### Instalación para desarrollo 

```bash
pip install -e ".[dev]" 
``` 

### Requisitos 

- Python 3.10+ 
- ~500MB de disco para el modelo de embedding (se descarga una vez, se ejecuta localmente) 
- No se necesitan claves de API para: sync, embed, search, serve 
- `summarize` utiliza una API de LLM para resúmenes de hilos (opcional pero recomendado para `get_profile`) 

--- 

## El Pipeline 

**Flujo de trabajo completo:** 

```bash
mychatarchive sync           # importar desde todas las fuentes
mychatarchive summarize      # resúmenes de hilos vía LLM (opcional, requiere clave de API)
mychatarchive embed          # generar embeddings vectoriales localmente
mychatarchive serve          # iniciar servidor MCP 
``` 

**Atajo:** 

```bash
mychatarchive sync --embed   # sync + embed en un solo paso
mychatarchive serve 
``` 

El pipeline es incremental. Ejecuta `sync` en cualquier momento; la deduplicación SHA1 hace que siempre sea seguro. Los nuevos mensajes se integran en la siguiente ejecución de `embed` sin necesidad de `--force`. 

--- 

## Sincronización (Sync) 

```bash
mychatarchive sync           # importar desde todas las fuentes
mychatarchive sync --embed   # sync + generar embeddings en un solo paso 
``` 

`sync` importa en tres capas: 

1. **Autodescubrimiento** -- Sesiones de Claude Code (`~/.claude/projects/`) y conversaciones de Cursor desde bases de datos locales. Activado por defecto, configurable en `init`. 
2. **Carpeta de importación (Drop folder)** -- cualquier cosa en `~/.mychatarchive/imports/`. Deja aquí tus JSON de exportación de ChatGPT, Claude o Grok; el formato se detecta automáticamente. Se escanean subdirectorios recursivamente. 
3. **Fuentes nombradas** -- rutas personalizadas o carpetas compartidas de NAS que hayas configurado con `mychatarchive sources add`. 

Las tres capas se deduplican en el mismo archivo mediante hashing SHA1. 

> **Nota:** El autodescubrimiento cubre Claude Code (el agente de terminal) y Cursor. Las conversaciones de la web, móvil y aplicación de escritorio de Claude requieren una exportación manual desde los ajustes de Anthropic; deja el archivo en tu carpeta de importaciones y ejecuta `sync`. 

--- 

## Resumen (Summarize) 

Genera resúmenes de hilos mediante LLM para una recuperación de contexto más rica y para la herramienta MCP `get_profile`. 

```bash
mychatarchive summarize                              # modelo predeterminado vía OpenRouter
mychatarchive summarize --model gpt-4o-mini          # especificar modelo 
mychatarchive summarize --key sk-...                 # pasar clave de API en línea 
mychatarchive summarize --limit 50                   # procesar los primeros 50 hilos (para pruebas) 
``` 

Los resúmenes se almacenan en SQLite, se integran en su propio índice vectorial y son expuestos por `get_context` y `get_profile`. Sin resúmenes, `get_profile` utiliza fragmentos de mensajes recientes. 

**Clave de API:** Establece `OPENROUTER_API_KEY` (por defecto) o `ANTHROPIC_API_KEY`, o pasa `--key` en línea. 

--- 

## Grupos de Hilos 

Organiza los hilos en grupos nombrados para búsquedas y recuperación de contexto delimitadas. Útil cuando tu archivo mezcla conversaciones personales, trabajo de programación y hilos de proyectos: puedes delimitar la búsqueda exactamente a lo que sea relevante. 

```bash
# Crear grupos
mychatarchive groups create jarvis --description "Chats personales diarios"
mychatarchive groups create coding --description "Trabajo de desarrollo e hilos técnicos" 

# Examinar hilos para encontrar IDs
mychatarchive groups show jarvis 

# Añadir hilos
mychatarchive groups add jarvis <thread_id> <thread_id> 

# Delimitar búsqueda a un grupo 
mychatarchive search "qué decidí" --group jarvis 

# En herramientas MCP: search_brain(query="...", group="jarvis") 
``` 

El filtro `group` funciona en `search_brain`, `get_context`, `get_profile` y en la CLI de `search`. 

--- 

## Buscar desde la CLI 

```bash
mychatarchive search "decisiones de arquitectura de base de datos" 
mychatarchive search "manejo de errores en python" --mode keyword 
mychatarchive search "flujo de autenticación" --platform claude_code --group coding 
mychatarchive search "qué construí" --hours 168 --sort time 
mychatarchive search "diseño de api" --since 2026-01-01 
``` 

El modo predeterminado es semántico (búsqueda vectorial). Admite: `--mode keyword` para FTS (búsqueda de texto completo), `--platform` para filtro de fuente, `--hours` / `--since` para filtro de tiempo, `--sort time` para los más recientes primero, `--group` para filtro de grupo. 

--- 

## Exportar 

```bash
mychatarchive export archive.json           # exportación estructurada completa 
mychatarchive export archive.csv            # compatible con hojas de cálculo 
mychatarchive export archive.db             # copia completa de SQLite con embeddings 
mychatarchive export chatgpt.json --platform chatgpt 
mychatarchive export everything.json --include-thoughts 
``` 

--- 

## Conectando a Herramientas de IA 

### Claude Desktop 

```bash
mychatarchive mcp-config --client claude-desktop 
``` 

Añade el resultado a tu archivo de configuración: 

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json` 
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json` 

### Cursor 

```bash
mychatarchive mcp-config --client cursor 
``` 

Añade el resultado a los ajustes de MCP de Cursor. 

### Acceso remoto vía SSE 

Para acceso móvil o multi-dispositivo, ejecuta el servidor en un NAS o una máquina siempre encendida: 

```bash
mychatarchive serve --transport sse --port 8420 
``` 

Conéctate vía Tailscale o WireGuard desde cualquier dispositivo. Funciona con Claude móvil y cualquier cliente MCP que soporte servidores remotos. 

--- 

## Verificar Estadísticas del Archivo 

```bash
mychatarchive info 
``` 

```
MyChatArchive - ~/.mychatarchive/archive.db
----------------------------------------
  Messages:    47,832
  Threads:     1,204 
  Summaries:   1,204
  Embedded:    51,388 chunks 
  Thoughts:    12 
  Groups:      3 
  Platforms: 
    chatgpt: 38,541
    anthropic: 8,291 
    grok: 1,000 
``` 

--- 

## Cómo Funciona 

```
Autodescubrimiento (Claude Code, Cursor)  --+
Carpeta Drop (ChatGPT, Claude, Grok)      --+--> Parse + SHA1 dedup --> SQLite (FTS5)
Fuentes nombradas (NAS, rutas custom)     --+              |
                                                       v
                                           sentence-transformers (local)
                                                       | 
                                                       v 
                                           sqlite-vec (cosine KNN) 
                                                       | 
                                                       v
                                           MCP server (stdio / SSE) 
                                                       | 
                                          Claude Desktop / Cursor / 
                                          Claude Code / Claude Mobile 
``` 

### Stack 

| Componente | Tecnología | 
|-----------|-----------| 
| **Almacenamiento** | SQLite + FTS5 (texto completo) + sqlite-vec (vectores) | 
| **Embeddings** | sentence-transformers `all-MiniLM-L6-v2` (384 dim, local) | 
| **Resumen** | Cualquier API compatible con OpenAI (OpenRouter por defecto, Anthropic como alternativa) | 
| **Interfaz** | Servidor MCP (transporte stdio + SSE) | 
| **Deduplicación** | IDs de mensaje estables basados en SHA1 | 
| **CLI** | Python argparse + rich | 

### Los datos permanecen locales 

- Los embeddings se ejecutan localmente. Sin OpenAI, sin nube. 
- La base de datos es un único archivo SQLite en `~/.mychatarchive/archive.db`. 
- El servidor MCP se ejecuta sobre stdio por defecto (tubería local, sin red). 
- `summarize` es el único paso que realiza llamadas API externas. Es opcional. 

--- 

## Referencia de la CLI 

| Comando | Descripción | 
|---------|-------------| 
| `mychatarchive init` | Configuración interactiva (carpeta drop, autodescubrimiento, backends) | 
| `mychatarchive sync` | Importar desde todas las fuentes (auto + carpeta drop + nombradas) | 
| `mychatarchive sync --embed` | Sync + generar embeddings en un solo paso | 
| `mychatarchive import <file\|dir>` | Importar un solo archivo o directorio | 
| `mychatarchive import --from <name>` | Importar desde una fuente nombrada | 
| `mychatarchive sources add <name> <path>` | Añadir una fuente de importación nombrada | 
| `mychatarchive sources list` | Mostrar todas las fuentes (auto + drop + nombradas) | 
| `mychatarchive sources remove <name>` | Eliminar una fuente | 
| `mychatarchive sources rename <old> <new>` | Renombrar una fuente | 
| `mychatarchive summarize` | Generar resúmenes de hilos vía LLM (requiere clave de API) | 
| `mychatarchive groups list` | Listar todos los grupos de hilos | 
| `mychatarchive groups create <name>` | Crear un grupo de hilos | 
| `mychatarchive groups add <group> <ids...>` | Añadir hilos a un grupo | 
| `mychatarchive groups show <name>` | Mostrar hilos en un grupo | 
| `mychatarchive groups delete <name>` | Eliminar un grupo (los hilos no se eliminan) | 
| `mychatarchive embed` | Generar embeddings vectoriales | 
| `mychatarchive export <output>` | Exportar a JSON, CSV o copia de SQLite | 
| `mychatarchive serve` | Iniciar servidor MCP | 
| `mychatarchive search <query>` | Buscar desde la terminal | 
| `mychatarchive info` | Mostrar estadísticas del archivo | 
| `mychatarchive mcp-config` | Imprimir configuración del cliente MCP | 

Todos los comandos aceptan `--db /ruta/a/archive.db` para anular la ubicación predeterminada de la base de datos. 

--- 

## Estructura del Proyecto 

``` 
mychatarchive/ 
+-- src/mychatarchive/ 
|   +-- cli.py              # CLI Unificada 
|   +-- config.py           # Rutas, constantes, gestión de config 
|   +-- db.py               # Capa de acceso a datos (delega a backends) 
|   +-- embeddings.py       # Pipeline de embedding local 
|   +-- chunker.py          # Fragmentación de mensajes para embeddings 
|   +-- ingest.py           # Motor de importación con deduplicación SHA1 
|   +-- summarizer.py       # Pipeline de resumen de hilos vía LLM 
|   +-- parsers/ 
|   |   +-- chatgpt.py      # ChatGPT conversations.json 
|   |   +-- anthropic.py    # Formato de exportación de Claude 
|   |   +-- grok.py         # Formato de exportación de Grok/X.AI 
|   |   +-- claude_code.py  # Sesiones JSONL de Claude Code 
|   |   +-- cursor.py       # Bases de datos SQLite de Cursor IDE 
|   +-- backends/           # Almacenamiento, embeddings, transporte enchufables 
|   +-- mcp/ 
|       +-- server.py       # Servidor MCP (6 herramientas) 
+-- tests/ 
+-- pyproject.toml 
+-- ROADMAP.md 
``` 

--- 

## Añadiendo un Nuevo Parser 

Crea `src/mychatarchive/parsers/tuplataforma.py`: 

```python
from typing import Iterator 

def parse(input_path: str) -> Iterator[dict]: 
    """Yield normalized messages.""" 
    yield { 
        "thread_id": "unique-thread-id", 
        "thread_title": "Conversation Title", 
        "role": "user", 
        "content": "Message text", 
        "created_at": 1700000000.0, 
    } 
``` 

Regístralo en `src/mychatarchive/parsers/__init__.py`. 

--- 

## Ubicación de Datos Predeterminada 

``` 
~/.mychatarchive/ 
+-- archive.db          # Base de datos SQLite (mensajes + vectores + pensamientos) 
+-- config.json         # Configuración de backend + fuentes 
+-- imports/            # Carpeta drop para archivos de exportación 
``` 

Anula con `--db /ruta/a/tu.db` en cualquier comando, o establece una ruta de carpeta drop personalizada en `init`. 

--- 

## Roadmap 

- [x] Importación multi-plataforma (ChatGPT, Claude, Grok, Claude Code, Cursor) 
- [x] Embeddings vectoriales locales (sentence-transformers, sin API) 
- [x] Servidor MCP: search_brain, search_recent, get_context, capture_thought, get_profile, get_current_datetime 
- [x] Resúmenes de hilos vía cualquier API compatible con OpenAI (`mychatarchive summarize`) 
- [x] Grupos de hilos con búsqueda delimitada por grupo (`mychatarchive groups`) 
- [x] Filtros de plataforma, tiempo y grupo en búsquedas y todas las herramientas MCP 
- [x] Arquitectura de backend enchufable (almacenamiento, embeddings, transporte) 
- [x] Exportación (JSON, CSV, copia SQLite) 
- [x] Transporte SSE para acceso MCP remoto 
- [x] Sincronización en un comando con autodescubrimiento + carpeta drop + fuentes nombradas 
- [ ] Parsers adicionales (Gemini, Perplexity, Copilot) 
- [ ] UI de agrupación (examinar hilos y asignarlos a grupos sin conocer los IDs de los hilos) 
- [ ] Motor de análisis (prompts profundos contra todo tu archivo) 
- [ ] Auto-sincronización (sin necesidad de exportaciones manuales) 
- [ ] Publicación en PyPI 
- [ ] Panel web + opción alojada en [mychatarchive.com](https://mychatarchive.com) 
- [ ] Imagen de Docker para auto-alojamiento en un comando 

Consulta [ROADMAP.md](ROADMAP.md) para el plan completo por fases. 

--- 

## Open Core 

| | Nivel | 
|-|------| 
| Importación, embedding, resumen, grupos, servidor MCP (stdio) | Gratis / local (AGPL-3.0) | 
| Transporte SSE con auth, sync en nube, MCP alojado, equipos | Planificado en [mychatarchive.com](https://mychatarchive.com) | 

El principio: cualquier cosa que se ejecute en tu máquina es gratis. Cualquier cosa que requiera infraestructura es de pago. 

**Licencia:** El uso local y auto-alojado es gratuito bajo AGPL-3.0. El uso comercial o el ofrecimiento de MyChatArchive como un servicio alojado requiere una licencia comercial. Contacta con [channing@mychatarchive.com](mailto:channing@mychatarchive.com) para licencias comerciales. 

--- 

## Licencia 

AGPL-3.0 -- ver [LICENSE](LICENSE). 

--- 

<div align="center"> 
  <strong>Construido por <a href="https://github.com/1ch1n">Channing Chasko</a></strong> 
  <br> 
  <a href="https://mychatarchive.com">mychatarchive.com</a> 
  <br><br> 
  <a href="https://github.com/1ch1n/mychatarchive/stargazers"> 
    <img src="https://img.shields.io/github/stars/1ch1n/mychatarchive?style=social" alt="Star on GitHub"> 
  </a> 
</div>
