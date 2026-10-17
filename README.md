# Audio Audit CLI

CLI en Python que utiliza FFmpeg para analizar archivos de audio y registrar los resultados en una base de datos SQLite y un archivo de texto tabulado (TSV).

Está pensado como punto de partida para llevar un historial de mediciones de mezclas y masters, comparar exportaciones y conservar la fecha y hora de cada análisis.

## Características

- Analiza un archivo de audio o todos los archivos compatibles de una carpeta.
- Utiliza `ffprobe` para obtener duración, códec, frecuencia de muestreo y canales.
- Utiliza el filtro `ebur128` de FFmpeg para obtener métricas de sonoridad y true peak.
- Utiliza `astats` para obtener pico de muestra y nivel RMS.
- Guarda cada ejecución en SQLite y agrega una fila al archivo TSV.
- Conserva el nombre y la ruta del archivo, fecha y hora local con zona horaria, estado y posibles errores.
- Permite analizar carpetas de forma recursiva.

## Requisitos

- Python 3.10 o superior.
- FFmpeg y `ffprobe` instalados y disponibles en `PATH`.
- No requiere paquetes Python de terceros.

Comprueba las dependencias desde la terminal:

```powershell
python --version
ffmpeg -version
ffprobe -version
```

## Instalación

1. Descarga o clona este proyecto.
2. Guarda `audio_audit.py` en una carpeta de trabajo.
3. Verifica que Python, FFmpeg y `ffprobe` estén disponibles desde la terminal.

No es necesario crear un entorno virtual ni instalar dependencias adicionales para esta versión.

## Uso

### Analizar un archivo

```powershell
python audio_audit.py "C:\\Audio\\mezcla_final.wav"
```

Por defecto, se crearán en el directorio actual:

- `audio_audit.sqlite`: base de datos SQLite.
- `audio_audit.tsv`: historial tabulado.

### Definir las rutas de salida

```powershell
python audio_audit.py "C:\\Audio\\mezcla_final.wav" `
  --db "C:\\AudioAudit\\mediciones.sqlite" `
  --txt "C:\\AudioAudit\\mediciones.tsv"
```

### Analizar una carpeta

Analiza los archivos compatibles que estén directamente dentro de la carpeta:

```powershell
python audio_audit.py "C:\\Audio\\Album"
```

Para incluir subcarpetas:

```powershell
python audio_audit.py "C:\\Audio\\Album" --recursive
```

Extensiones reconocidas: WAV, FLAC, MP3, M4A, AAC, AIFF, AIF, OGG, OPUS y WMA. La compatibilidad efectiva depende de los decodificadores disponibles en FFmpeg.

### Ver ayuda

```powershell
python audio_audit.py --help
```

## Métricas registradas

| Campo | Descripción | Unidad |
|---|---|---|
| `duration_seconds` | Duración reportada por `ffprobe` | segundos |
| `duration_hms` | Duración legible | `HH:MM:SS.mmm` |
| `sample_peak_dbfs` | Pico de muestra | dBFS |
| `true_peak_dbtp` | Pico verdadero reportado por `ebur128` | dBTP |
| `clips` | Contador de muestras recortadas | muestras |
| `true_peak_clips` | Contador de clips de true peak | eventos/muestras, según implementación |
| `rms_i_dbfs` | Nivel RMS global de `astats` | dBFS |
| `lra_lu` | Loudness Range | LU |
| `lufs_m_max` | Máximo momentáneo | LUFS |
| `lufs_s_max` | Máximo a corto plazo | LUFS |
| `lufs_i` | Sonoridad integrada | LUFS |
| `tested_at` | Fecha y hora de la prueba | ISO 8601 |
| `file_name` | Nombre del archivo | texto |
| `file_path` | Ruta del archivo analizado | texto |

También se guardan frecuencia de muestreo, cantidad de canales, códec, estado y mensaje de error cuando corresponde.

## Archivos de salida

### SQLite

La base de datos contiene la tabla `measurements`. Cada análisis crea un registro nuevo, incluso si se analiza varias veces el mismo archivo, para conservar el historial.

Puedes consultarla con herramientas como [DB Browser for SQLite](https://sqlitebrowser.org/) o desde Python.

Ejemplo de consulta:

```sql
SELECT
    tested_at,
    file_name,
    duration_hms,
    lufs_i,
    true_peak_dbtp,
    lra_lu,
    status
FROM measurements
ORDER BY tested_at DESC;
```

### TSV

El archivo `.tsv` es texto UTF-8 con columnas separadas por tabuladores. Puedes abrirlo con Excel, LibreOffice Calc o procesarlo desde scripts. Se añade una fila por archivo analizado; la cabecera se escribe al crear el archivo.

## Interpretación y limitaciones actuales

Esta versión es una base inicial de registro, no una herramienta de certificación de audio. Hay limitaciones que deben considerarse antes de usar los resultados para decisiones de masterización:

- **`Clips` y `TP Clips`:** actualmente no se calculan; se guardan como valores vacíos (`NULL` en SQLite). No deben interpretarse como cero clips.
- **`LUFS-M max` y `LUFS-S max`:** la extracción depende del formato de salida de la versión de FFmpeg instalada y debe validarse con archivos de prueba. Si el valor no se reconoce, queda vacío.
- **`RMS-I`:** el valor guardado es el RMS global de `astats` en dBFS. No es una métrica LUFS ni equivale a la sonoridad integrada.
- **True peak:** se toma del análisis `ebur128`; la exactitud depende de la configuración y versión de FFmpeg.
- **Duración:** se registra la duración reportada por `ffprobe`, que puede variar respecto de la duración percibida en archivos con cabeceras dañadas o metadatos inusuales.

Los campos no calculados se conservan para mantener el esquema preparado para futuras versiones. Para comparar masters rigurosamente, valida los resultados contra una herramienta de referencia y mantén constantes la versión de FFmpeg y los parámetros de análisis.

## Códigos de salida

- `0`: todos los archivos se analizaron correctamente.
- `1`: no se encontraron archivos compatibles o uno o más análisis fallaron.
- `2`: faltan dependencias o la ruta de entrada no existe.

Los errores se guardan en la base de datos y en el TSV junto con el estado `error`.

## Estructura del proyecto

```text
audio-audit/
├── audio_audit.py
├── README.md
├── audio_audit.sqlite   # se genera al ejecutar
└── audio_audit.tsv      # se genera al ejecutar
```

## Próximas mejoras sugeridas

- Implementar contadores verificables de clipping de muestra y true peak.
- Validar los máximos momentáneo y short-term con pruebas automatizadas.
- Añadir comandos para listar, consultar y comparar mediciones.
- Registrar la versión de FFmpeg y un hash SHA-256 del archivo.
- Incorporar pruebas con archivos de referencia y resultados esperados.

## Licencia

MIT.
