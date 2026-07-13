import csv
import html
import importlib
import io
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable, Iterable, Optional

import streamlit as st


st.set_page_config(page_title="SRT to TSV Converter v7", page_icon="📝", layout="wide")


# ---------- Configuration ----------

APP_VERSION = "7.0"

# Language-specific spaCy pipelines used for named-entity recognition.
# The requirements file installs the small pipelines. The loader also accepts
# medium or large pipelines when a deployment already provides them.
SPACY_MODEL_IDS = {
    "en": "en_core_web_sm",
    "es": "es_core_news_sm",
    "pt": "pt_core_news_sm",
}

SPACY_MODEL_CANDIDATES = {
    "en": ("en_core_web_sm", "en_core_web_md", "en_core_web_lg"),
    "es": ("es_core_news_sm", "es_core_news_md", "es_core_news_lg"),
    "pt": ("pt_core_news_sm", "pt_core_news_md", "pt_core_news_lg"),
}

LANGUAGE_NAMES = {
    "en": "English",
    "es": "Español",
    "pt": "Português",
}

PERSON_LABELS = {"PER", "PERSON"}
ORGANIZATION_LABELS = {"ORG", "ORGANIZATION"}
LOCATION_LABELS = {"LOC", "LOCATION", "GPE", "FAC"}

# Required TSV order: timestamps, subtitle text, session title, then entities.
# SRT sequence numbers are deliberately excluded.
TSV_HEADERS = [
    "Start Timestamp (HH:MM:SS)",
    "End Timestamp (HH:MM:SS)",
    "Subtitle Text",
    "Session Title",
    "People, Organizations, and Places",
]

UI_TEXT = {
    "en": {
        "sidebar_title": "Settings",
        "title": "SRT to TSV Converter",
        "intro": (
            "Upload one or more SRT files and convert them into TSV tables. "
            "Milliseconds and SRT sequence numbers are omitted, session titles are carried "
            "forward, and spaCy extracts people, organizations, and places."
        ),
        "session_help_title": "SRT session-title format",
        "session_help": """
A session title may appear directly after a cue number:

```text
1 Opening Session
00:00:01,250 --> 00:00:04,800
Welcome to the conference.

2
00:00:05,000 --> 00:00:08,400
Our first speaker is Ana Silva.
```

`Opening Session` is assigned to cue 1 and all following cues until a different session title appears. The sequence numbers are used only for parsing and are not written to the TSV.
        """,
        "ner_language": "Subtitle language for named-entity recognition",
        "ner_help": (
            "Choose the principal language used in the subtitle text. This setting is "
            "independent of the interface language."
        ),
        "ner_note": (
            "spaCy identifies people, organizations, and locations. Person names are inverted "
            "heuristically to Family name, Given name. One-word entities are preserved when "
            "they are nouns or proper nouns—including countries and place names—but detections "
            "tagged as verbs, auxiliary verbs, or adjectives are excluded. For place names, "
            "leading prepositions and any verb tokens included in the detected span are removed. "
            "NER results and geographic matches should be reviewed."
        ),
        "resolve_places": "Resolve place names to geographic hierarchies",
        "resolve_places_help": (
            "When enabled, detected location names are sent to the OpenStreetMap Nominatim "
            "service and formatted as country--state/department/diocese--city. A country or "
            "state reference omits lower levels; unresolved names remain in their detected form."
        ),
        "privacy_note": (
            "Location resolution requires internet access and sends only spaCy-detected place "
            "names—not the complete subtitle text—to Nominatim."
        ),
        "osm_attribution": (
            "Geographic data © [OpenStreetMap contributors]"
            "(https://www.openstreetmap.org/copyright), used under the ODbL."
        ),
        "mode_label": "Conversion options",
        "simple_mode": "Simple conversion to TSV",
        "merged_mode": "Convert and merge consecutive lines by speaker (character-based blocks)",
        "merge_help_title": "Speaker detection and merging",
        "merge_help": """
In merging mode, the app preserves the original speaker-detection behavior:

- Text after the end timestamp is treated as an explicit speaker name.
- A subtitle beginning with `-`, `–`, or `—` starts a new unnamed speaker turn.
- A cue without either marker continues the previous speaker.
- Cues are never merged across different sessions.

Named entities are identified from the original subtitle cues before merging. Every output row in a session receives the combined entities found anywhere in that same session, but entities do not carry into another session.
        """,
        "target_chars": "Target characters per subtitle block",
        "target_help": "The converter will try to keep merged blocks around this length.",
        "max_chars": "Maximum characters per subtitle block",
        "max_help": "Merged blocks will not exceed this length unless one original cue is already longer.",
        "uploader": "Choose one or more .srt files",
        "spinner_model": "Loading the spaCy named-entity recognition model...",
        "spinner_processing": "Processing uploaded files...",
        "missing_model": "spaCy or its language model could not be loaded.",
        "install_intro": "Install the dependencies and restart the app:",
        "model_loaded": "Using spaCy model: {model}.",
        "model_downloaded": "Downloaded and loaded the missing spaCy model: {model}.",
        "model_attempts": "Attempted models: {models}.",
        "decode_error": "Could not decode {filename} as UTF-8.",
        "no_cues": "No valid SRT cues were found in {filename}.",
        "preview": "Preview of TSV output for {filename} (first 20 lines)",
        "entities_preview": "Entity-list preview for {filename}",
        "download_tsv": "Download TSV for {filename}",
        "download_txt": "Download entity TXT for {filename}",
        "download_all": "Download all TSV and entity TXT files as ZIP",
        "no_results": "No output files were created.",
        "processed_summary": "Processed {files} file(s) and {rows} TSV row(s).",
        "people_heading": "People",
        "organizations_heading": "Organizations",
        "places_heading": "Places",
        "none_identified": "None identified",
    },
    "es": {
        "sidebar_title": "Configuración",
        "title": "Convertidor de SRT a TSV",
        "intro": (
            "Suba uno o más archivos SRT y conviértalos en tablas TSV. Se omiten los "
            "milisegundos y los números de secuencia, se conservan los títulos de sesión y "
            "spaCy extrae personas, organizaciones y lugares."
        ),
        "session_help_title": "Formato del título de sesión en el SRT",
        "session_help": """
Un título de sesión puede aparecer directamente después del número de secuencia:

```text
1 Sesión inaugural
00:00:01,250 --> 00:00:04,800
Bienvenidos al congreso.

2
00:00:05,000 --> 00:00:08,400
Nuestra primera ponente es Ana Silva.
```

`Sesión inaugural` se asigna a la secuencia 1 y a las siguientes hasta que aparezca un título de sesión diferente. Los números solo se usan para analizar el SRT y no se escriben en el TSV.
        """,
        "ner_language": "Idioma de los subtítulos para el reconocimiento de entidades",
        "ner_help": (
            "Seleccione el idioma principal del texto de los subtítulos. Esta opción es "
            "independiente del idioma de la interfaz."
        ),
        "ner_note": (
            "spaCy identifica personas, organizaciones y lugares. Los nombres de personas se "
            "invierten de manera heurística a Apellido, Nombre. Se conservan las entidades de una "
            "sola palabra cuando son sustantivos o nombres propios, incluidos países y lugares, "
            "pero se excluyen las detecciones etiquetadas como verbos, verbos auxiliares o "
            "adjetivos. En los nombres de lugar se eliminan las preposiciones iniciales y cualquier "
            "verbo incluido dentro del segmento detectado. Se deben revisar los resultados y las "
            "coincidencias geográficas."
        ),
        "resolve_places": "Resolver los lugares como jerarquías geográficas",
        "resolve_places_help": (
            "Al activarse, los lugares detectados se envían al servicio OpenStreetMap Nominatim "
            "y se formatean como país--estado/departamento/diócesis--ciudad. Una referencia a un "
            "país o estado omite los niveles inferiores; los lugares no resueltos conservan su forma detectada."
        ),
        "privacy_note": (
            "La resolución geográfica requiere internet y solo envía a Nominatim los nombres de "
            "lugar detectados por spaCy, no el texto completo de los subtítulos."
        ),
        "osm_attribution": (
            "Datos geográficos © [colaboradores de OpenStreetMap]"
            "(https://www.openstreetmap.org/copyright), utilizados bajo la licencia ODbL."
        ),
        "mode_label": "Opciones de conversión",
        "simple_mode": "Conversión simple a TSV",
        "merged_mode": "Convertir y unir líneas consecutivas por hablante (bloques por caracteres)",
        "merge_help_title": "Detección de hablantes y unión",
        "merge_help": """
En el modo de unión, la aplicación conserva el comportamiento original de detección de hablantes:

- El texto después de la marca de tiempo final se interpreta como nombre explícito del hablante.
- Un subtítulo que comienza con `-`, `–` o `—` inicia un nuevo turno de hablante sin nombre.
- Una secuencia sin ninguno de esos indicadores continúa con el hablante anterior.
- Nunca se unen secuencias de distintas sesiones.

Las entidades se identifican en las secuencias originales antes de unirlas. Cada fila de salida de una sesión recibe todas las entidades encontradas en esa misma sesión, pero no se trasladan a otra sesión.
        """,
        "target_chars": "Número objetivo de caracteres por bloque de subtítulos",
        "target_help": "El convertidor intentará mantener los bloques unidos cerca de esta extensión.",
        "max_chars": "Número máximo de caracteres por bloque de subtítulos",
        "max_help": "Los bloques no superarán esta extensión, salvo que una secuencia original ya sea más larga.",
        "uploader": "Seleccione uno o más archivos .srt",
        "spinner_model": "Cargando el modelo de reconocimiento de entidades de spaCy...",
        "spinner_processing": "Procesando los archivos subidos...",
        "missing_model": "No se pudo cargar spaCy o su modelo de idioma.",
        "install_intro": "Instale las dependencias y reinicie la aplicación:",
        "model_loaded": "Modelo de spaCy en uso: {model}.",
        "model_downloaded": "Se descargó y cargó el modelo de spaCy faltante: {model}.",
        "model_attempts": "Modelos probados: {models}.",
        "decode_error": "No se pudo decodificar {filename} como UTF-8.",
        "no_cues": "No se encontraron secuencias SRT válidas en {filename}.",
        "preview": "Vista previa del TSV de {filename} (primeras 20 líneas)",
        "entities_preview": "Vista previa de la lista de entidades de {filename}",
        "download_tsv": "Descargar el TSV de {filename}",
        "download_txt": "Descargar el TXT de entidades de {filename}",
        "download_all": "Descargar todos los TSV y TXT de entidades como ZIP",
        "no_results": "No se creó ningún archivo de salida.",
        "processed_summary": "Se procesaron {files} archivo(s) y {rows} fila(s) TSV.",
        "people_heading": "Personas",
        "organizations_heading": "Organizaciones",
        "places_heading": "Lugares",
        "none_identified": "Ninguno identificado",
    },
    "pt": {
        "sidebar_title": "Configurações",
        "title": "Conversor de SRT para TSV",
        "intro": (
            "Envie um ou mais arquivos SRT e converta-os em tabelas TSV. Os milissegundos e "
            "os números de sequência são omitidos, os títulos das sessões são mantidos e o "
            "spaCy extrai pessoas, organizações e lugares."
        ),
        "session_help_title": "Formato do título da sessão no SRT",
        "session_help": """
Um título de sessão pode aparecer diretamente depois do número da sequência:

```text
1 Sessão de abertura
00:00:01,250 --> 00:00:04,800
Bem-vindos ao congresso.

2
00:00:05,000 --> 00:00:08,400
Nossa primeira palestrante é Ana Silva.
```

`Sessão de abertura` é atribuída à sequência 1 e às seguintes até aparecer um título de sessão diferente. Os números são usados apenas para analisar o SRT e não são escritos no TSV.
        """,
        "ner_language": "Idioma das legendas para o reconhecimento de entidades",
        "ner_help": (
            "Selecione o idioma principal do texto das legendas. Esta opção é independente "
            "do idioma da interface."
        ),
        "ner_note": (
            "O spaCy identifica pessoas, organizações e lugares. Os nomes de pessoas são "
            "invertidos de forma heurística para Sobrenome, Nome. Entidades de uma única palavra "
            "são preservadas quando são substantivos ou nomes próprios, incluindo países e "
            "lugares, mas detecções marcadas como verbos, verbos auxiliares ou adjetivos são "
            "excluídas. Nos nomes de lugar, preposições iniciais e quaisquer verbos incluídos no "
            "trecho detectado são removidos. Os resultados e as correspondências geográficas "
            "devem ser revisados."
        ),
        "resolve_places": "Resolver lugares como hierarquias geográficas",
        "resolve_places_help": (
            "Quando ativado, os lugares detectados são enviados ao serviço OpenStreetMap "
            "Nominatim e formatados como país--estado/departamento/diocese--cidade. Uma referência "
            "a país ou estado omite níveis inferiores; lugares não resolvidos mantêm a forma detectada."
        ),
        "privacy_note": (
            "A resolução geográfica requer internet e envia ao Nominatim apenas os nomes de "
            "lugares detectados pelo spaCy, não o texto completo das legendas."
        ),
        "osm_attribution": (
            "Dados geográficos © [colaboradores do OpenStreetMap]"
            "(https://www.openstreetmap.org/copyright), utilizados sob a licença ODbL."
        ),
        "mode_label": "Opções de conversão",
        "simple_mode": "Conversão simples para TSV",
        "merged_mode": "Converter e unir linhas consecutivas por falante (blocos por caracteres)",
        "merge_help_title": "Detecção de falantes e união",
        "merge_help": """
No modo de união, o aplicativo preserva o comportamento original de detecção de falantes:

- O texto depois do tempo final é tratado como nome explícito do falante.
- Uma legenda iniciada por `-`, `–` ou `—` começa um novo turno de falante sem nome.
- Uma sequência sem nenhum desses indicadores continua com o falante anterior.
- Sequências de sessões diferentes nunca são unidas.

As entidades são identificadas nas sequências originais antes da união. Cada linha de saída de uma sessão recebe todas as entidades encontradas nessa mesma sessão, sem transferi-las para outra sessão.
        """,
        "target_chars": "Meta de caracteres por bloco de legendas",
        "target_help": "O conversor tentará manter os blocos unidos próximos deste tamanho.",
        "max_chars": "Máximo de caracteres por bloco de legendas",
        "max_help": "Os blocos não ultrapassarão este tamanho, salvo quando uma sequência original já for maior.",
        "uploader": "Escolha um ou mais arquivos .srt",
        "spinner_model": "Carregando o modelo de reconhecimento de entidades do spaCy...",
        "spinner_processing": "Processando os arquivos enviados...",
        "missing_model": "Não foi possível carregar o spaCy ou seu modelo de idioma.",
        "install_intro": "Instale as dependências e reinicie o aplicativo:",
        "model_loaded": "Modelo spaCy em uso: {model}.",
        "model_downloaded": "O modelo spaCy ausente foi baixado e carregado: {model}.",
        "model_attempts": "Modelos testados: {models}.",
        "decode_error": "Não foi possível decodificar {filename} como UTF-8.",
        "no_cues": "Nenhuma sequência SRT válida foi encontrada em {filename}.",
        "preview": "Prévia do TSV de {filename} (primeiras 20 linhas)",
        "entities_preview": "Prévia da lista de entidades de {filename}",
        "download_tsv": "Baixar o TSV de {filename}",
        "download_txt": "Baixar o TXT de entidades de {filename}",
        "download_all": "Baixar todos os TSV e TXT de entidades como ZIP",
        "no_results": "Nenhum arquivo de saída foi criado.",
        "processed_summary": "Foram processados {files} arquivo(s) e {rows} linha(s) TSV.",
        "people_heading": "Pessoas",
        "organizations_heading": "Organizações",
        "places_heading": "Lugares",
        "none_identified": "Nenhum identificado",
    },
}


# ---------- Data structures ----------

@dataclass(frozen=True)
class SRTCue:
    sequence_number: str
    session_id: int
    session_title: str
    start: str
    end: str
    speaker_name: Optional[str]
    text: str


@dataclass(frozen=True)
class SpeakerSegment:
    sequence_number: str
    session_id: int
    session_title: str
    start_td: timedelta
    end_td: timedelta
    speaker_label: str
    text: str


@dataclass(frozen=True)
class OutputRecord:
    session_id: int
    session_title: str
    start: str
    end: str
    text: str


@dataclass(frozen=True)
class EntityBundle:
    people: tuple[str, ...] = ()
    organizations: tuple[str, ...] = ()
    places: tuple[str, ...] = ()

    def combined(self) -> str:
        values = [*self.people, *self.organizations, *self.places]
        return " | ".join(values)


# ---------- Time and text helpers ----------

TIME_PATTERN = re.compile(
    r"^\s*(\d{1,3}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*"
    r"(\d{1,3}:\d{2}:\d{2}[,.]\d{1,3})"
    r"(?:\s+(.*?))?\s*$"
)

SEQUENCE_PATTERN = re.compile(r"^\s*(\d+)(?:[\t ]+(.+?))?\s*$")
SRT_SETTING_PATTERN = re.compile(r"\b(?:align|line|position|size|vertical):", re.IGNORECASE)

DIOCESE_PATTERN = re.compile(
    r"^\s*(?:the\s+)?(?:archdiocese|diocese|arquidi[oó]cesis|di[oó]cesis|"
    r"arquidiocese|diocese)\s+(?:of|de|do|da)\s+(.+?)\s*$",
    re.IGNORECASE,
)

ADMINISTRATIVE_PLACE_PATTERN = re.compile(
    r"^\s*(?:state|province|department|departamento|département|estado|provincia|"
    r"regi[aã]o|region|región|diocese|archdiocese|diócesis|arquidiócesis|"
    r"diocese|arquidiocese)\b",
    re.IGNORECASE,
)

# Lexical fallback for leading prepositions when a language model does not assign
# the expected ADP part-of-speech tag. POS tagging remains the primary test.
LEADING_LOCATION_PREPOSITIONS = {
    # English
    "at", "by", "from", "in", "into", "near", "of", "on", "to", "toward", "towards",
    # Spanish
    "a", "al", "ante", "bajo", "cabe", "con", "contra", "de", "del", "desde", "en",
    "entre", "hacia", "hasta", "para", "por", "según", "sin", "sobre", "tras",
    # Portuguese
    "à", "ao", "aos", "às", "da", "das", "de", "do", "dos", "em", "na", "nas",
    "no", "nos", "para", "pela", "pelas", "pelo", "pelos", "por", "sob", "sobre",
}

LOCATION_VERB_POS = {"VERB", "AUX"}


LocationResolver = Callable[[str, str], str]


def normalize_whitespace(value: str) -> str:
    return " ".join((value or "").replace("\t", " ").split())


def clean_entity_value(value: str) -> str:
    """Keep the pipe character reserved as the TSV entity separator."""
    return normalize_whitespace(value).replace("|", "/").strip(" ,;:")


def time_to_hhmmss(time_str: str) -> str:
    """Convert HH:MM:SS,mmm or HH:MM:SS.mmm to HH:MM:SS."""
    return re.split(r"[,.]", time_str.strip(), maxsplit=1)[0]


def hhmmss_to_timedelta(hhmmss: str) -> timedelta:
    hours, minutes, seconds = hhmmss.split(":")
    return timedelta(hours=int(hours), minutes=int(minutes), seconds=int(seconds))


def timedelta_to_hhmmss(value: timedelta) -> str:
    total_seconds = int(value.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def looks_like_cue_start(lines: list[str], index: int) -> bool:
    """Return True when lines[index] is a cue-number line followed by a timecode."""
    if index >= len(lines) or not SEQUENCE_PATTERN.match(lines[index].strip()):
        return False

    next_index = index + 1
    while next_index < len(lines) and not lines[next_index].strip():
        next_index += 1

    return next_index < len(lines) and bool(TIME_PATTERN.match(lines[next_index].strip()))


# ---------- SRT parsing ----------

def parse_srt(file_content: str) -> list[SRTCue]:
    """
    Parse standard SRT cues plus the extended cue-number syntax:

        1 Session title
        00:00:01,000 --> 00:00:04,000
        Subtitle text

    A non-empty session title persists until a different title is supplied. Each
    contiguous session receives an internal session_id so entities never leak into
    another session, even when a title is reused later in the file.
    """
    normalized_content = file_content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized_content.split("\n")

    cues: list[SRTCue] = []
    current_session_title = ""
    current_session_id = 0
    next_session_id = 1
    index = 0

    while index < len(lines):
        raw_line = lines[index]
        sequence_match = SEQUENCE_PATTERN.match(raw_line.strip())

        if not sequence_match:
            index += 1
            continue

        sequence_number = sequence_match.group(1)
        supplied_session_title = normalize_whitespace(sequence_match.group(2) or "")
        if supplied_session_title and supplied_session_title != current_session_title:
            current_session_title = supplied_session_title
            current_session_id = next_session_id
            next_session_id += 1

        index += 1
        while index < len(lines) and not lines[index].strip():
            index += 1

        if index >= len(lines):
            break

        time_match = TIME_PATTERN.match(lines[index].strip())
        if not time_match:
            # This numeric line was not actually the beginning of a valid SRT cue.
            continue

        start = time_to_hhmmss(time_match.group(1))
        end = time_to_hhmmss(time_match.group(2))
        trailing_text = normalize_whitespace(time_match.group(3) or "")
        speaker_name = (
            trailing_text
            if trailing_text and not SRT_SETTING_PATTERN.search(trailing_text)
            else None
        )

        index += 1
        text_lines: list[str] = []

        while index < len(lines):
            current_line = lines[index]

            if not current_line.strip():
                index += 1
                break

            # Also supports SRT files that omit blank lines between cues.
            if looks_like_cue_start(lines, index):
                break

            text_lines.append(current_line.strip())
            index += 1

        subtitle_text = normalize_whitespace(" ".join(text_lines))
        cues.append(
            SRTCue(
                sequence_number=sequence_number,
                session_id=current_session_id,
                session_title=current_session_title,
                start=start,
                end=end,
                speaker_name=speaker_name,
                text=subtitle_text,
            )
        )

    return cues


# ---------- Speaker segmentation and merging ----------

def build_speaker_segments(cues: Iterable[SRTCue]) -> list[SpeakerSegment]:
    """Apply the original speaker-detection rules to parsed SRT cues."""
    segments: list[SpeakerSegment] = []
    last_speaker_label: Optional[str] = None
    last_session_id: Optional[int] = None
    anonymous_speaker_count = 0

    for cue in cues:
        if last_session_id is not None and cue.session_id != last_session_id:
            last_speaker_label = None
            anonymous_speaker_count = 0
        last_session_id = cue.session_id

        raw_text = cue.text or ""
        cleaned_text = raw_text.strip()

        if cue.speaker_name:
            speaker_label = cue.speaker_name.strip()
            last_speaker_label = speaker_label
        elif cleaned_text.startswith(("-", "–", "—")):
            anonymous_speaker_count += 1
            speaker_label = f"Speaker {anonymous_speaker_count}"
            cleaned_text = cleaned_text[1:].strip()
            last_speaker_label = speaker_label
        elif last_speaker_label is not None:
            speaker_label = last_speaker_label
        else:
            speaker_label = "Unknown"
            last_speaker_label = speaker_label

        segments.append(
            SpeakerSegment(
                sequence_number=cue.sequence_number,
                session_id=cue.session_id,
                session_title=cue.session_title,
                start_td=hhmmss_to_timedelta(cue.start),
                end_td=hhmmss_to_timedelta(cue.end),
                speaker_label=speaker_label,
                text=normalize_whitespace(cleaned_text),
            )
        )

    return segments


def merge_segments_by_speaker(
    segments: Iterable[SpeakerSegment],
    target_chars: int = 250,
    max_chars: int = 300,
) -> list[OutputRecord]:
    """Merge consecutive segments from the same speaker and internal session."""
    segment_list = list(segments)
    if not segment_list:
        return []

    merged: list[OutputRecord] = []
    current_session_id: Optional[int] = None
    current_session_title = ""
    current_speaker: Optional[str] = None
    current_start: Optional[timedelta] = None
    current_end: Optional[timedelta] = None
    current_text_parts: list[str] = []

    def flush_current() -> None:
        nonlocal current_session_id, current_session_title, current_speaker
        nonlocal current_start, current_end, current_text_parts

        if (
            current_session_id is not None
            and current_speaker is not None
            and current_start is not None
            and current_end is not None
        ):
            merged.append(
                OutputRecord(
                    session_id=current_session_id,
                    session_title=current_session_title,
                    start=timedelta_to_hhmmss(current_start),
                    end=timedelta_to_hhmmss(current_end),
                    text=normalize_whitespace(" ".join(current_text_parts)),
                )
            )

        current_session_id = None
        current_session_title = ""
        current_speaker = None
        current_start = None
        current_end = None
        current_text_parts = []

    for segment in segment_list:
        if current_speaker is None:
            current_session_id = segment.session_id
            current_session_title = segment.session_title
            current_speaker = segment.speaker_label
            current_start = segment.start_td
            current_end = segment.end_td
            current_text_parts = [segment.text]
            continue

        speaker_changed = segment.speaker_label != current_speaker
        session_changed = segment.session_id != current_session_id

        if speaker_changed or session_changed:
            flush_current()
            current_session_id = segment.session_id
            current_session_title = segment.session_title
            current_speaker = segment.speaker_label
            current_start = segment.start_td
            current_end = segment.end_td
            current_text_parts = [segment.text]
            continue

        current_text = normalize_whitespace(" ".join(current_text_parts))
        candidate_length = len(current_text) + (1 if current_text and segment.text else 0) + len(segment.text)

        if candidate_length <= max_chars:
            current_text_parts.append(segment.text)
            current_end = segment.end_td
        else:
            # target_chars remains the preferred size; max_chars is the merge boundary.
            _ = target_chars
            flush_current()
            current_session_id = segment.session_id
            current_session_title = segment.session_title
            current_speaker = segment.speaker_label
            current_start = segment.start_td
            current_end = segment.end_td
            current_text_parts = [segment.text]

    flush_current()
    return merged


def cues_to_simple_records(cues: Iterable[SRTCue]) -> list[OutputRecord]:
    return [
        OutputRecord(
            session_id=cue.session_id,
            session_title=cue.session_title,
            start=cue.start,
            end=cue.end,
            text=cue.text,
        )
        for cue in cues
    ]


# ---------- Named-entity recognition with spaCy ----------

def _prepare_spacy_ner_pipeline(nlp: Any) -> Any:
    """Keep NER and part-of-speech components needed for precision filtering."""
    required_components = {
        "ner",
        "tok2vec",
        "transformer",
        "tagger",
        "morphologizer",
        "attribute_ruler",
    }
    unused_components = [
        component for component in nlp.pipe_names if component not in required_components
    ]
    if unused_components:
        nlp.disable_pipes(*unused_components)
    return nlp


@st.cache_resource(show_spinner=False)
def load_ner_model(language_code: str) -> tuple[Any, str, bool]:
    """
    Load an installed spaCy NER pipeline without crashing on a missing package.

    The small model listed in requirements.txt is tried first. Medium and large
    variants are accepted as fallbacks for deployments that already include them.
    If no candidate is installed, the function makes one best-effort download of
    the small model and then retries it.

    Returns: (nlp_pipeline, loaded_model_name, downloaded_during_this_run).
    """
    import spacy

    if language_code not in SPACY_MODEL_CANDIDATES:
        raise ValueError(f"Unsupported NER language: {language_code}")

    candidates = SPACY_MODEL_CANDIDATES[language_code]
    load_errors: list[str] = []

    for model_name in candidates:
        try:
            nlp = spacy.load(model_name)
            return _prepare_spacy_ner_pipeline(nlp), model_name, False
        except (OSError, IOError, ImportError) as error:
            load_errors.append(f"{model_name}: {error}")

    # requirements.txt should normally install the preferred small model before
    # Streamlit starts. This fallback helps when only app_v7.py was copied into a
    # deployment or when the model dependency was accidentally omitted.
    preferred_model = SPACY_MODEL_IDS[language_code]
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "spacy", "download", preferred_model],
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if completed.returncode == 0:
            importlib.invalidate_caches()
            nlp = spacy.load(preferred_model)
            return _prepare_spacy_ner_pipeline(nlp), preferred_model, True

        detail = (completed.stderr or completed.stdout or "download failed").strip()
        load_errors.append(f"{preferred_model} download: {detail}")
    except (OSError, subprocess.SubprocessError, TimeoutError) as error:
        load_errors.append(f"{preferred_model} download: {error}")
    except (OSError, IOError, ImportError) as error:
        load_errors.append(f"{preferred_model} reload: {error}")

    attempted = ", ".join(candidates)
    details = " | ".join(load_errors[-4:])
    raise RuntimeError(
        f"No trained spaCy NER model could be loaded. Attempted: {attempted}. "
        f"Details: {details}"
    )


def clean_text_for_ner(text: str) -> str:
    without_ass_tags = re.sub(r"\{\\[^{}]*\}", " ", text or "")
    without_html_tags = re.sub(r"<[^>]+>", " ", without_ass_tags)
    return normalize_whitespace(html.unescape(without_html_tags))


def format_person_name(name: str) -> str:
    """Heuristically invert a detected person name to Family name, Given name."""
    cleaned = clean_entity_value(name)
    if not cleaned:
        return ""

    # Preserve an entity that the source already presents in inverted form.
    if "," in cleaned:
        family, given = (part.strip() for part in cleaned.split(",", 1))
        return f"{family}, {given}" if given else family

    tokens = cleaned.split()
    honorifics = {
        "mr", "mrs", "ms", "miss", "dr", "prof", "sir", "dame",
        "sr", "sra", "srta", "dra", "professor", "professora",
        "dom", "dona", "don", "doña",
    }
    while len(tokens) > 1 and tokens[0].casefold().rstrip(".") in honorifics:
        tokens.pop(0)

    if len(tokens) == 1:
        return tokens[0]

    suffixes = {"jr", "sr", "ii", "iii", "iv", "filho", "neto", "júnior", "junior"}
    family_start = len(tokens) - 1
    if tokens[-1].casefold().rstrip(".,") in suffixes and len(tokens) >= 3:
        family_start = len(tokens) - 2

    surname_particles = {
        "da", "das", "de", "del", "della", "der", "di", "do", "dos",
        "du", "la", "las", "le", "los", "van", "von", "y",
    }
    while family_start > 0 and tokens[family_start - 1].casefold().strip(".,") in surname_particles:
        family_start -= 1

    given_names = " ".join(tokens[:family_start]).strip()
    family_names = " ".join(tokens[family_start:]).strip()

    if not given_names:
        return family_names
    return f"{family_names}, {given_names}"


def deduplicate_preserving_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []

    for value in values:
        normalized = clean_entity_value(value)
        key = normalized.casefold()
        if normalized and key not in seen:
            seen.add(key)
            output.append(normalized)

    return output


def entity_word_tokens(entity: Any) -> list[Any]:
    """Return non-space, non-punctuation tokens from a spaCy entity span."""
    return [
        token
        for token in entity
        if not token.is_space and not token.is_punct and token.text.strip()
    ]


def clean_place_entity(entity: Any) -> str:
    """Clean a spaCy location span without discarding legitimate one-word places.

    Rules:
    - Remove prepositions only when they occur at the beginning of the detected span.
    - Remove any tokens spaCy tags as VERB or AUX, wherever they occur in the span.
    - Preserve the remaining punctuation and spacing as closely as possible.
    - Return an empty string when cleanup leaves no substantive place-name token.

    Examples:
        "en México" -> "México"
        "de Nueva York" -> "Nueva York"
        "visitó Madrid" (if wholly tagged as LOC) -> "Madrid"
    """
    tokens = [token for token in entity if not token.is_space and token.text.strip()]
    if not tokens:
        return ""

    # Remove opening punctuation before evaluating the first lexical token.
    while tokens and tokens[0].is_punct:
        tokens.pop(0)

    # Strip one or more leading prepositions. The lexical set is a fallback for
    # occasional tagging errors in multilingual subtitle text.
    while tokens:
        first = tokens[0]
        first_key = first.text.casefold().strip(".,;:!?¡¿()[]{}\"'“”‘’")
        first_pos = str(getattr(first, "pos_", "") or "").upper()
        if first_pos == "ADP" or first_key in LEADING_LOCATION_PREPOSITIONS:
            tokens.pop(0)
            while tokens and tokens[0].is_punct:
                tokens.pop(0)
            continue
        break

    retained = [
        token
        for token in tokens
        if str(getattr(token, "pos_", "") or "").upper() not in LOCATION_VERB_POS
    ]

    # A verb removed from the beginning may expose a preposition (for example,
    # "visitó en Madrid"). Strip that newly exposed leading preposition too.
    while retained:
        while retained and retained[0].is_punct:
            retained.pop(0)
        if not retained:
            break
        first = retained[0]
        first_key = first.text.casefold().strip(".,;:!?¡¿()[]{}\"'“”‘’")
        first_pos = str(getattr(first, "pos_", "") or "").upper()
        if first_pos == "ADP" or first_key in LEADING_LOCATION_PREPOSITIONS:
            retained.pop(0)
            continue
        break

    # Remove punctuation stranded at either edge after cleanup.
    while retained and retained[0].is_punct:
        retained.pop(0)
    while retained and retained[-1].is_punct:
        retained.pop()

    if not any(not token.is_punct for token in retained):
        return ""

    reconstructed = "".join(
        getattr(token, "text_with_ws", token.text + " ") for token in retained
    ).strip()
    reconstructed = re.sub(r"\s+([,.;:!?])", r"\1", reconstructed)
    reconstructed = re.sub(r"([\(\[\{])\s+", r"\1", reconstructed)
    reconstructed = re.sub(r"\s+([\)\]\}])", r"\1", reconstructed)
    return clean_entity_value(reconstructed)


def should_keep_entity(entity: Any, label: str, entity_text: str) -> bool:
    """Apply label-aware precision rules to a spaCy entity.

    All multi-word people, organizations, and places are retained. For a
    single-word entity, only geographic entities are retained. Single-word
    people and organizations are discarded, regardless of their part of
    speech. This preserves locations such as "México", "Brasil", "Quito",
    and "Lisboa" while removing common one-word PERSON/ORG false positives.

    A diocese or an explicitly named administrative unit is treated as a
    place even when the spaCy model labels it as ORG.
    """
    word_tokens = entity_word_tokens(entity)
    if not word_tokens:
        return False
    if len(word_tokens) >= 2:
        return True

    normalized_label = str(label or "").upper()
    if normalized_label in LOCATION_LABELS:
        return True

    if normalized_label in ORGANIZATION_LABELS and (
        DIOCESE_PATTERN.match(entity_text)
        or ADMINISTRATIVE_PLACE_PATTERN.match(entity_text)
    ):
        return True

    # Single-word PERSON and ORG entities are intentionally excluded.
    return False


def extract_raw_entities_batch(texts: Iterable[str], nlp: Any) -> list[EntityBundle]:
    """Run spaCy NER and return precision-filtered named entities for each cue."""
    cleaned_texts = [clean_text_for_ner(text) for text in texts]
    results: list[EntityBundle] = []

    # nlp.pipe batches the subtitles efficiently while preserving input order,
    # including empty cues.
    for doc in nlp.pipe(cleaned_texts, batch_size=64):
        people: list[str] = []
        organizations: list[str] = []
        places: list[str] = []

        for entity in doc.ents:
            label = str(entity.label_).upper()
            original_entity_text = clean_entity_value(entity.text)
            if not original_entity_text:
                continue

            organization_is_place = label in ORGANIZATION_LABELS and (
                DIOCESE_PATTERN.match(original_entity_text)
                or ADMINISTRATIVE_PLACE_PATTERN.match(original_entity_text)
            )
            is_place_entity = label in LOCATION_LABELS or organization_is_place

            # Place spans receive additional cleanup: initial prepositions are
            # removed and verb/AUX tokens are omitted before storage, resolution,
            # session propagation, and TXT generation.
            entity_text = (
                clean_place_entity(entity) if is_place_entity else original_entity_text
            )
            if not entity_text:
                continue

            # Keep one-word locations, but remove every one-word person or
            # organization before session propagation and TXT generation.
            if not should_keep_entity(entity, label, entity_text):
                continue

            if label in PERSON_LABELS:
                formatted_person = format_person_name(entity_text)
                if formatted_person:
                    people.append(formatted_person)
            elif is_place_entity:
                places.append(entity_text)
            elif label in ORGANIZATION_LABELS:
                organizations.append(entity_text)

        results.append(
            EntityBundle(
                people=tuple(deduplicate_preserving_order(people)),
                organizations=tuple(deduplicate_preserving_order(organizations)),
                places=tuple(deduplicate_preserving_order(places)),
            )
        )

    return results


# ---------- Geographic hierarchy resolution ----------

@st.cache_resource(show_spinner=False)
def get_geocode_rate_limiter() -> Any:
    from geopy.extra.rate_limiter import RateLimiter
    from geopy.geocoders import Nominatim

    geocoder = Nominatim(
        user_agent="srt-to-tsv-spacy-ner/7.0",
        timeout=12,
    )
    return RateLimiter(
        geocoder.geocode,
        min_delay_seconds=1.1,
        max_retries=1,
        error_wait_seconds=2.0,
        swallow_exceptions=True,
    )


def first_nonempty(mapping: dict[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        value = clean_entity_value(str(mapping.get(key, "")))
        if value:
            return value
    return ""


def first_display_component(location: Any) -> str:
    address = clean_entity_value(getattr(location, "address", ""))
    return clean_entity_value(address.split(",", 1)[0]) if address else ""


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24 * 30)
def resolve_location_hierarchy(location_name: str, language_code: str) -> str:
    """
    Resolve a spaCy-detected location to country--state/department/diocese--city.

    The hierarchy stops at the type of place referenced: countries omit state and
    city; first-level administrative units omit city; populated places include all
    available levels. If no match is found, the detected location is returned.
    """
    original = clean_entity_value(location_name)
    if not original:
        return ""

    diocese_match = DIOCESE_PATTERN.match(original)
    query = clean_entity_value(diocese_match.group(1)) if diocese_match else original

    try:
        geocode = get_geocode_rate_limiter()
        location = geocode(
            query,
            exactly_one=True,
            addressdetails=True,
            language=language_code,
        )
    except Exception:
        return original

    if location is None:
        return original

    raw = getattr(location, "raw", {}) or {}
    address = raw.get("address", {}) or {}
    feature_type = clean_entity_value(
        str(raw.get("addresstype") or raw.get("type") or "")
    ).casefold()

    country = first_nonempty(address, ["country"])
    state = first_nonempty(
        address,
        ["state", "province", "region", "state_district", "department"],
    )
    city = first_nonempty(
        address,
        ["city", "town", "village", "municipality", "hamlet", "locality", "borough"],
    )
    display_component = first_display_component(location)

    if diocese_match:
        # A diocese occupies the middle hierarchy level. Do not invent a city level.
        parts = [country, original]
        return "--".join(part for part in deduplicate_preserving_order(parts) if part) or original

    country_types = {"country"}
    administrative_types = {
        "state", "province", "region", "state_district", "department", "administrative",
    }
    populated_place_types = {
        "city", "town", "village", "municipality", "hamlet", "locality", "borough",
        "suburb", "quarter", "neighbourhood",
    }

    if feature_type in country_types:
        parts = [country or display_component]
    elif feature_type in administrative_types:
        parts = [country, state or display_component]
    elif feature_type in populated_place_types or city:
        parts = [country, state, city or display_component]
    else:
        # For landmarks and other location types, use the available hierarchy and
        # keep the referenced feature as the most specific level when appropriate.
        most_specific = city or display_component
        parts = [country, state, most_specific]

    formatted = "--".join(part for part in deduplicate_preserving_order(parts) if part)
    return formatted or original


# ---------- Session-level entity aggregation ----------

def aggregate_session_entities(
    cues: Iterable[SRTCue],
    nlp: Any,
    language_code: str,
    resolve_places: bool = True,
    location_resolver: LocationResolver = resolve_location_hierarchy,
) -> tuple[dict[int, EntityBundle], EntityBundle]:
    """
    Aggregate entities per internal session and for the complete file.

    Each TSV row later receives the complete bundle for its own session_id. This is
    intentionally based on original cues rather than merged output rows.
    """
    cue_list = list(cues)
    raw_entities = extract_raw_entities_batch((cue.text for cue in cue_list), nlp)

    session_people: dict[int, list[str]] = {}
    session_organizations: dict[int, list[str]] = {}
    session_places: dict[int, list[str]] = {}

    all_people: list[str] = []
    all_organizations: list[str] = []
    all_places: list[str] = []
    resolved_location_cache: dict[str, str] = {}

    for cue, cue_entities in zip(cue_list, raw_entities):
        session_people.setdefault(cue.session_id, [])
        session_organizations.setdefault(cue.session_id, [])
        session_places.setdefault(cue.session_id, [])

        for person in cue_entities.people:
            session_people[cue.session_id].append(person)
            all_people.append(person)

        for organization in cue_entities.organizations:
            session_organizations[cue.session_id].append(organization)
            all_organizations.append(organization)

        for raw_place in cue_entities.places:
            cache_key = raw_place.casefold()
            if resolve_places:
                if cache_key not in resolved_location_cache:
                    resolved_location_cache[cache_key] = location_resolver(raw_place, language_code)
                place = resolved_location_cache[cache_key]
            else:
                place = raw_place

            if place:
                session_places[cue.session_id].append(place)
                all_places.append(place)

    session_ids = {
        *session_people.keys(),
        *session_organizations.keys(),
        *session_places.keys(),
    }
    session_bundles: dict[int, EntityBundle] = {}
    for session_id in session_ids:
        session_bundles[session_id] = EntityBundle(
            people=tuple(deduplicate_preserving_order(session_people.get(session_id, []))),
            organizations=tuple(
                deduplicate_preserving_order(session_organizations.get(session_id, []))
            ),
            places=tuple(deduplicate_preserving_order(session_places.get(session_id, []))),
        )

    file_bundle = EntityBundle(
        people=tuple(deduplicate_preserving_order(all_people)),
        organizations=tuple(deduplicate_preserving_order(all_organizations)),
        places=tuple(deduplicate_preserving_order(all_places)),
    )
    return session_bundles, file_bundle


# ---------- TSV and TXT generation ----------

def records_to_tsv(
    records: Iterable[OutputRecord],
    session_entities: dict[int, EntityBundle],
) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(TSV_HEADERS)

    for record in records:
        entities = session_entities.get(record.session_id, EntityBundle()).combined()
        writer.writerow(
            [
                record.start,
                record.end,
                normalize_whitespace(record.text),
                normalize_whitespace(record.session_title),
                entities,
            ]
        )

    return output.getvalue()


def entities_to_txt(
    entities: EntityBundle,
    people_heading: str = "People",
    organizations_heading: str = "Organizations",
    places_heading: str = "Places",
    none_identified: str = "None identified",
) -> str:
    sections = [
        (people_heading, entities.people),
        (organizations_heading, entities.organizations),
        (places_heading, entities.places),
    ]

    lines: list[str] = []
    for index, (heading, values) in enumerate(sections):
        if index:
            lines.append("")
        lines.append(heading)
        lines.append("=" * len(heading))
        if values:
            lines.extend(values)
        else:
            lines.append(none_identified)

    return "\n".join(lines) + "\n"


def convert_srt(
    file_content: str,
    nlp: Any,
    language_code: str,
    resolve_places: bool = True,
    merge_by_speaker: bool = False,
    target_chars: int = 250,
    max_chars: int = 300,
    location_resolver: LocationResolver = resolve_location_hierarchy,
    txt_headings: Optional[dict[str, str]] = None,
) -> tuple[str, str, int]:
    cues = parse_srt(file_content)
    if not cues:
        return "", "", 0

    if merge_by_speaker:
        records = merge_segments_by_speaker(
            build_speaker_segments(cues),
            target_chars=target_chars,
            max_chars=max_chars,
        )
    else:
        records = cues_to_simple_records(cues)

    session_entities, file_entities = aggregate_session_entities(
        cues,
        nlp=nlp,
        language_code=language_code,
        resolve_places=resolve_places,
        location_resolver=location_resolver,
    )

    headings = txt_headings or {}
    tsv_text = records_to_tsv(records, session_entities)
    txt_text = entities_to_txt(
        file_entities,
        people_heading=headings.get("people", "People"),
        organizations_heading=headings.get("organizations", "Organizations"),
        places_heading=headings.get("places", "Places"),
        none_identified=headings.get("none", "None identified"),
    )
    return tsv_text, txt_text, len(records)


# ---------- Streamlit interface ----------

st.sidebar.title("Settings / Configuración / Configurações")
interface_language = st.sidebar.selectbox(
    "Interface language / Idioma de la interfaz / Idioma da interface",
    options=list(LANGUAGE_NAMES),
    format_func=lambda code: LANGUAGE_NAMES[code],
)
T = UI_TEXT[interface_language]

st.sidebar.caption(T["sidebar_title"])

st.title(f"{T["title"]} — v{APP_VERSION}")
st.write(T["intro"])

with st.expander(T["session_help_title"], expanded=False):
    st.markdown(T["session_help"])

ner_language = st.selectbox(
    T["ner_language"],
    options=list(LANGUAGE_NAMES),
    index=list(LANGUAGE_NAMES).index(interface_language),
    format_func=lambda code: LANGUAGE_NAMES[code],
    help=T["ner_help"],
)
st.caption(T["ner_note"])

resolve_places_option = st.checkbox(
    T["resolve_places"],
    value=True,
    help=T["resolve_places_help"],
)
if resolve_places_option:
    st.caption(T["privacy_note"])
    st.markdown(T["osm_attribution"])

mode = st.radio(
    T["mode_label"],
    options=["simple", "merged"],
    format_func=lambda value: T["simple_mode"] if value == "simple" else T["merged_mode"],
)

with st.expander(T["merge_help_title"], expanded=False):
    st.markdown(T["merge_help"])

if mode == "merged":
    target_chars = st.number_input(
        T["target_chars"],
        min_value=50,
        max_value=2000,
        value=250,
        step=10,
        help=T["target_help"],
    )
    max_chars = st.number_input(
        T["max_chars"],
        min_value=int(target_chars),
        max_value=4000,
        value=max(300, int(target_chars * 1.2)),
        step=10,
        help=T["max_help"],
    )
else:
    target_chars = 250
    max_chars = 300

uploaded_files = st.file_uploader(
    T["uploader"],
    type=["srt", "txt"],
    accept_multiple_files=True,
)

if uploaded_files:
    try:
        with st.spinner(T["spinner_model"]):
            ner_model, loaded_model_name, downloaded_model = load_ner_model(ner_language)
    except Exception as error:
        attempted_models = ", ".join(SPACY_MODEL_CANDIDATES[ner_language])
        st.error(f"{T['missing_model']} ({SPACY_MODEL_IDS[ner_language]})")
        st.write(T["install_intro"])
        st.code(
            "pip install -r requirements.txt\n"
            "python -m spacy download en_core_web_sm\n"
            "python -m spacy download es_core_news_sm\n"
            "python -m spacy download pt_core_news_sm",
            language="bash",
        )
        st.caption(T["model_attempts"].format(models=attempted_models))
        st.caption(str(error))
        st.stop()

    if downloaded_model:
        st.info(T["model_downloaded"].format(model=loaded_model_name))
    else:
        st.caption(T["model_loaded"].format(model=loaded_model_name))

    results: list[tuple[str, str, str, int]] = []
    txt_headings = {
        "people": T["people_heading"],
        "organizations": T["organizations_heading"],
        "places": T["places_heading"],
        "none": T["none_identified"],
    }

    with st.spinner(T["spinner_processing"]):
        for uploaded_file in uploaded_files:
            file_bytes = uploaded_file.read()
            try:
                file_text = file_bytes.decode("utf-8-sig")
            except UnicodeDecodeError:
                st.error(T["decode_error"].format(filename=uploaded_file.name))
                continue

            tsv_text, entity_txt, row_count = convert_srt(
                file_text,
                nlp=ner_model,
                language_code=ner_language,
                resolve_places=resolve_places_option,
                merge_by_speaker=(mode == "merged"),
                target_chars=int(target_chars),
                max_chars=int(max_chars),
                txt_headings=txt_headings,
            )

            if not tsv_text:
                st.warning(T["no_cues"].format(filename=uploaded_file.name))
                continue

            results.append((uploaded_file.name, tsv_text, entity_txt, row_count))

    if not results:
        st.error(T["no_results"])
        st.stop()

    total_rows = sum(result[3] for result in results)
    st.success(T["processed_summary"].format(files=len(results), rows=total_rows))

    first_name, first_tsv, first_txt, _ = results[0]
    st.subheader(T["preview"].format(filename=first_name))
    st.text("\n".join(first_tsv.splitlines()[:20]))

    with st.expander(T["entities_preview"].format(filename=first_name), expanded=False):
        st.text(first_txt)

    if len(results) == 1:
        base_name = first_name.rsplit(".", 1)[0]
        left_column, right_column = st.columns(2)
        with left_column:
            st.download_button(
                label=T["download_tsv"].format(filename=first_name),
                data=first_tsv.encode("utf-8-sig"),
                file_name=base_name + ".tsv",
                mime="text/tab-separated-values",
            )
        with right_column:
            st.download_button(
                label=T["download_txt"].format(filename=first_name),
                data=first_txt.encode("utf-8-sig"),
                file_name=base_name + "_entities.txt",
                mime="text/plain",
            )
    else:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for original_name, tsv_text, entity_txt, _ in results:
                base_name = original_name.rsplit(".", 1)[0]
                zip_file.writestr(base_name + ".tsv", tsv_text.encode("utf-8-sig"))
                zip_file.writestr(
                    base_name + "_entities.txt",
                    entity_txt.encode("utf-8-sig"),
                )

        zip_buffer.seek(0)
        st.download_button(
            label=T["download_all"],
            data=zip_buffer,
            file_name="converted_srt_outputs.zip",
            mime="application/zip",
        )
