from pathlib import Path
from zipfile import ZipFile


FLOW_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_ROOT = FLOW_ROOT / "samples"


def test_transcript_samples_have_at_least_two_hundred_utterances() -> None:
    transcript_names = [
        "historical_transcript_01.txt",
        "historical_transcript_02.txt",
        "current_transcript.txt",
    ]
    for name in transcript_names:
        text = (SAMPLE_ROOT / name).read_text(encoding="utf-8")
        utterances = [line for line in text.splitlines() if line[:1] == "[" and line[1:4].isdigit()]
        assert len(utterances) >= 200, f"{name}의 발언 수가 200개보다 적습니다."
        assert utterances[0].startswith("[001]")
        assert utterances[-1].startswith("[220]")
        assert "[녹취 시작]" in text
        assert "[녹취 종료]" in text


def test_historical_samples_form_two_ordered_pairs_with_full_minutes() -> None:
    for index in (1, 2):
        transcript = SAMPLE_ROOT / f"historical_transcript_{index:02d}.txt"
        minutes = SAMPLE_ROOT / f"historical_minutes_{index:02d}.txt"
        assert transcript.exists() and minutes.exists()
        minutes_text = minutes.read_text(encoding="utf-8")
        assert len(minutes_text) >= 3_500, f"{minutes.name}이 Word 약 2쪽을 학습하기에 너무 짧습니다."
        assert "## 3. 의사결정" in minutes_text
        assert "## 5. 후속 조치" in minutes_text or "## 4. 후속 조치" in minutes_text
        assert "| 담당 | 조치 | 기한 |" in minutes_text


def test_historical_minutes_include_real_docx_upload_examples() -> None:
    for index in (1, 2):
        word_sample = SAMPLE_ROOT / f"historical_minutes_{index:02d}.docx"
        assert word_sample.exists(), f"{word_sample.name} Word 예시가 없습니다."
        assert word_sample.stat().st_size >= 10_000
        with ZipFile(word_sample) as archive:
            names = set(archive.namelist())
            assert "[Content_Types].xml" in names
            assert "word/document.xml" in names
            document_xml = archive.read("word/document.xml").decode("utf-8")
        assert "회의록" in document_xml
        assert "후속 조치" in document_xml
