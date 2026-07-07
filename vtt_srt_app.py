import io
import re
import zipfile
from typing import Dict, List, Optional, Tuple

import streamlit as st


# ---------- Interface text ----------

TEXT: Dict[str, Dict[str, str]] = {
    "English": {
        "language_label": "Interface language",
        "title": "SRT to TSV Converter",
        "intro": (
            "Upload one or more SRT files and convert each subtitle cue into a TSV table "
            "with sequence number, session, start timestamp, end timestamp, and subtitle text."
        ),
        "format_note": (
            "Timestamps are exported as HH:MM:SS. Milliseconds are removed. "
            "If text appears on the same line as the SRT sequence number, it is exported in the Session column."
        ),
        "details_title": "Expected SRT structure",
        "details_body": """
A standard cue looks like this:

```text
1 Session 01
00:00:01,000 --> 00:00:04,500
Subtitle text goes here.
```

The TSV output will be:

```text
SRT sequence number    Session       Start timestamp    End timestamp    Subtitle text
1                      Session 01    00:00:01           00:00:04         Subtitle text goes here.
```

The session value is optional. If the sequence line is only `1`, the Session column will be blank.
""",
        "uploader": "Choose one or more .srt files",
        "decode_error": "Could not decode file {name}. Try saving it as UTF-8 and uploading it again.",
        "no_cues": "No valid SRT cues were found in {name}.",
        "converted": "Converted {count} cue(s) from {name}.",
        "preview": "Preview of TSV output for {name} (first 20 lines)",
        "download_one": "Download TSV for {name}",
        "download_zip": "Download all TSV files as ZIP",
        "success": "Conversion complete.",
    },
    "Español": {
        "language_label": "Idioma de la interfaz",
        "title": "Conversor de SRT a TSV",
        "intro": (
            "Sube uno o más archivos SRT y convierte cada bloque de subtítulos en una tabla TSV "
            "con número de secuencia, sesión, marca de tiempo inicial, marca de tiempo final y texto del subtítulo."
        ),
        "format_note": (
            "Las marcas de tiempo se exportan como HH:MM:SS. Se eliminan los milisegundos. "
            "Si hay texto en la misma línea que el número de secuencia del SRT, se exporta en la columna Session."
        ),
        "details_title": "Estructura SRT esperada",
        "details_body": """
Un bloque estándar se ve así:

```text
1 Sesión 01
00:00:01,000 --> 00:00:04,500
Aquí va el texto del subtítulo.
```

La salida TSV será:

```text
SRT sequence number    Session       Start timestamp    End timestamp    Subtitle text
1                      Sesión 01     00:00:01           00:00:04         Aquí va el texto del subtítulo.
```

El valor de sesión es opcional. Si la línea de secuencia solo contiene `1`, la columna Session quedará en blanco.
""",
        "uploader": "Elige uno o más archivos .srt",
        "decode_error": "No se pudo decodificar el archivo {name}. Guárdalo como UTF-8 y súbelo de nuevo.",
        "no_cues": "No se encontraron bloques SRT válidos en {name}.",
        "converted": "Se convirtieron {count} bloque(s) de {name}.",
        "preview": "Vista previa de la salida TSV para {name} (primeras 20 líneas)",
        "download_one": "Descargar TSV para {name}",
        "download_zip": "Descargar todos los TSV como ZIP",
        "success": "Conversión completa.",
    },
    "Português": {
        "language_label": "Idioma da interface",
        "title": "Conversor de SRT para TSV",
        "intro": (
            "Envie um ou mais arquivos SRT e converta cada bloco de legenda em uma tabela TSV "
            "com número de sequência, sessão, timestamp inicial, timestamp final e texto da legenda."
        ),
        "format_note": (
            "Os timestamps são exportados como HH:MM:SS. Os milissegundos são removidos. "
            "Se houver texto na mesma linha do número de sequência do SRT, ele será exportado na coluna Session."
        ),
        "details_title": "Estrutura SRT esperada",
        "details_body": """
Um bloco padrão se parece com isto:

```text
1 Sessão 01
00:00:01,000 --> 00:00:04,500
O texto da legenda aparece aqui.
```

A saída TSV será:

```text
SRT sequence number    Session       Start timestamp    End timestamp    Subtitle text
1                      Sessão 01     00:00:01           00:00:04         O texto da legenda aparece aqui.
```

O valor de sessão é opcional. Se a linha de sequência contiver apenas `1`, a coluna Session ficará em branco.
""",
        "uploader": "Escolha um ou mais arquivos .srt",
        "decode_error": "Não foi possível decodificar o arquivo {name}. Salve-o como UTF-8 e envie novamente.",
        "no_cues": "Nenhum bloco SRT válido foi encontrado em {name}.",
        "converted": "Foram convertidos {count} bloco(s) de {name}.",
        "preview": "Prévia da saída TSV para {name} (primeiras 20 linhas)",
        "download_one": "Baixar TSV de {name}",
        "download_zip": "Baixar todos os TSV como ZIP",
        "success": "Conversão concluída.",
    },
}

TSV_HEADER = [
    "SRT sequence number",
    "Session",
    "Start timestamp",
    "End timestamp",
    "Subtitle text",
]

SRTCue = Tuple[str, str, str, str, str]


# ---------- SRT parsing helpers ----------

def decode_uploaded_file(file_bytes: bytes) -> Optional[str]:
    """
    Decode uploaded subtitle files. UTF-8 with BOM is preferred, but common
    fallbacks are included for SRT files exported by different tools.
    """
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "latin-1"):
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def timestamp_to_hhmmss(timestamp: str) -> str:
    """
    Convert SRT timestamps such as '00:01:02,345' or '00:01:02.345'
    to 'HH:MM:SS'. Milliseconds are truncated.
    """
    match = re.match(r"^\s*(\d{1,3}):(\d{2}):(\d{2})(?:[,.]\d+)?\s*$", timestamp)
    if not match:
        return timestamp.strip().split(",")[0].split(".")[0]

    hours, minutes, seconds = match.groups()
    return f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}"


def clean_tsv_cell(value: str) -> str:
    """
    Keep each TSV field on one line by replacing tabs/newlines with spaces
    and collapsing repeated whitespace.
    """
    return " ".join((value or "").replace("\t", " ").split())


def parse_srt(file_content: str) -> List[SRTCue]:
    """
    Parse SRT content into rows:
        SRT sequence number | Session | Start timestamp | End timestamp | Subtitle text

    The sequence line may be either:
        1
    or:
        1 Session name

    In the second case, all text after the sequence number is stored as Session.
    """
    normalized = file_content.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    lines = normalized.split("\n")

    sequence_pattern = re.compile(r"^\s*(\d+)\s*(.*?)\s*$")
    time_pattern = re.compile(
        r"^\s*"
        r"(\d{1,3}:\d{2}:\d{2}(?:[,.]\d+)?)"
        r"\s*-->\s*"
        r"(\d{1,3}:\d{2}:\d{2}(?:[,.]\d+)?)"
        r"(?:\s+.*)?$"
    )

    cues: List[SRTCue] = []
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        if not line:
            i += 1
            continue

        sequence_match = sequence_pattern.match(line)
        if not sequence_match:
            i += 1
            continue

        sequence_number = sequence_match.group(1)
        session = clean_tsv_cell(sequence_match.group(2))

        i += 1

        # Skip accidental blank lines between sequence and timestamp.
        while i < len(lines) and not lines[i].strip():
            i += 1

        if i >= len(lines):
            break

        time_match = time_pattern.match(lines[i].strip())
        if not time_match:
            # Not a valid SRT cue. Continue scanning from the next line.
            continue

        start_timestamp = timestamp_to_hhmmss(time_match.group(1))
        end_timestamp = timestamp_to_hhmmss(time_match.group(2))

        i += 1
        subtitle_lines: List[str] = []

        while i < len(lines):
            current_line = lines[i]

            if not current_line.strip():
                i += 1
                break

            # Be tolerant of SRT files that omit blank lines between cues.
            next_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if sequence_pattern.match(current_line.strip()) and time_pattern.match(next_line):
                break

            subtitle_lines.append(current_line.strip())
            i += 1

        subtitle_text = clean_tsv_cell(" ".join(subtitle_lines))
        cues.append((sequence_number, session, start_timestamp, end_timestamp, subtitle_text))

    return cues


def cues_to_tsv(cues: List[SRTCue]) -> str:
    """
    Convert parsed SRT cue rows to TSV text.
    """
    output = io.StringIO()
    output.write("\t".join(TSV_HEADER) + "\n")

    for sequence_number, session, start_timestamp, end_timestamp, subtitle_text in cues:
        output.write(
            "\t".join(
                [
                    clean_tsv_cell(sequence_number),
                    clean_tsv_cell(session),
                    clean_tsv_cell(start_timestamp),
                    clean_tsv_cell(end_timestamp),
                    clean_tsv_cell(subtitle_text),
                ]
            )
            + "\n"
        )

    return output.getvalue()


def convert_srt_to_tsv(file_content: str) -> Tuple[str, int]:
    """
    Convert SRT text into TSV text and return the TSV plus cue count.
    """
    cues = parse_srt(file_content)
    return cues_to_tsv(cues), len(cues)


# ---------- Streamlit UI ----------

st.set_page_config(page_title="SRT to TSV Converter", page_icon="📝", layout="centered")

language = st.selectbox(
    "Interface language / Idioma de la interfaz / Idioma da interface",
    options=list(TEXT.keys()),
    index=0,
)
t = TEXT[language]

st.title(t["title"])
st.write(t["intro"])
st.info(t["format_note"])

with st.expander(t["details_title"], expanded=False):
    st.markdown(t["details_body"])

uploaded_files = st.file_uploader(
    t["uploader"],
    type=["srt"],
    accept_multiple_files=True,
)

if uploaded_files:
    tsv_results: List[Tuple[str, str, int]] = []

    for uploaded_file in uploaded_files:
        file_text = decode_uploaded_file(uploaded_file.read())
        if file_text is None:
            st.error(t["decode_error"].format(name=uploaded_file.name))
            continue

        tsv_text, cue_count = convert_srt_to_tsv(file_text)

        if cue_count == 0:
            st.warning(t["no_cues"].format(name=uploaded_file.name))
            continue

        st.success(t["converted"].format(count=cue_count, name=uploaded_file.name))
        tsv_results.append((uploaded_file.name, tsv_text, cue_count))

    if tsv_results:
        st.success(t["success"])

        # Show preview of the first successfully converted file.
        first_name, first_tsv, _ = tsv_results[0]
        st.subheader(t["preview"].format(name=first_name))
        preview_lines = "\n".join(first_tsv.splitlines()[:20])
        st.text(preview_lines)

        if len(tsv_results) == 1:
            base_name = first_name.rsplit(".", 1)[0] + ".tsv"
            st.download_button(
                label=t["download_one"].format(name=first_name),
                data=first_tsv.encode("utf-8"),
                file_name=base_name,
                mime="text/tab-separated-values",
            )
        else:
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
                for original_name, tsv_text, _ in tsv_results:
                    tsv_name = original_name.rsplit(".", 1)[0] + ".tsv"
                    zipf.writestr(tsv_name, tsv_text)

            zip_buffer.seek(0)
            st.download_button(
                label=t["download_zip"],
                data=zip_buffer,
                file_name="converted_srt_tsv_files.zip",
                mime="application/zip",
            )
