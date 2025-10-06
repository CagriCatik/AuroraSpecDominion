"""Core utilities for the PDF → LLM → test-specification workflow."""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
import subprocess
import time
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Callable, Dict, List, Sequence

try:  # pragma: no cover - import guard for optional progress dependency
    from tqdm.auto import tqdm
except Exception:  # pragma: no cover - keep working without tqdm installed
    tqdm = None  # type: ignore[assignment]

import fitz
import pandas as pd
import requests
from openpyxl.utils import get_column_letter

from config_models import (
    DEFAULT_GPU_LAYERS,
    DEFAULT_MAIN_GPU,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL_NAME,
    DEFAULT_NUM_GPU,
    DEFAULT_NUM_THREAD,
    DEFAULT_OLLAMA_URL,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_K,
    DEFAULT_TOP_P,
    DEFAULT_USE_GPU,
)

# ------------------------------ prompt role ---------------------------------
PROMPT_FILE = Path(__file__).parent / "prompts" / "Prompt_Expert_Test_Architect.txt"
PROMPT_ROLE = PROMPT_FILE.read_text(encoding="utf-8").strip()

# ------------------------------ constants -----------------------------------
DEFAULTS = dict(
    ollama_url=DEFAULT_OLLAMA_URL,
    model=DEFAULT_MODEL_NAME,
    temperature=DEFAULT_TEMPERATURE,
    top_p=DEFAULT_TOP_P,
    top_k=DEFAULT_TOP_K,
    max_tokens=DEFAULT_MAX_TOKENS,
    http_timeout=None,  # disable hard timeout; let stream decide
    use_gpu=DEFAULT_USE_GPU,
    num_gpu=DEFAULT_NUM_GPU,
    main_gpu=DEFAULT_MAIN_GPU,
    gpu_layers=DEFAULT_GPU_LAYERS,
    num_thread=DEFAULT_NUM_THREAD,
)

ALLOWED_KEYS = [
    "testcase_name",
    "objective",
    "linked_requirements",
    "preconditions",
    "actions",
    "expected_results",
    "postconditions",
]
REQUIRED = {"objective", "actions", "expected_results", "postconditions"}

__all__ = [
    "AgentLocal",
    "generate_test_cases",
    "save_csv",
    "save_xlsx",
    "DEFAULTS",
    "parse_cases",
    "strip_code_fences",
    "chunk_document",
    "KeywordRetriever",
    "load_prompt_file",
    "PromptCache",
    "summarise_cases",
]


# ------------------------------ agent ---------------------------------------
class AgentLocal:
    """HTTP-first, CLI-fallback wrapper around a local Ollama REST API."""

    def __init__(self, **kwargs):
        cfg = {**DEFAULTS, **{k: v for k, v in kwargs.items() if v is not None}}
        # Backwards compatibility for alternative key names
        model = kwargs.get("model") or kwargs.get("model_name")
        if model:
            cfg["model"] = model
        url = kwargs.get("url") or kwargs.get("ollama_url")
        if url:
            cfg["ollama_url"] = url

        self.model = cfg["model"]
        self.url = cfg["ollama_url"].rstrip("/")
        self.use_gpu = bool(cfg["use_gpu"])

        num_gpu = cfg["num_gpu"]
        if num_gpu is None:
            num_gpu = -1 if self.use_gpu else 0

        self.opt = {
            "temperature": cfg["temperature"],
            "top_p": cfg["top_p"],
            "top_k": cfg["top_k"],
            "max_output_tokens": cfg["max_tokens"],
            "num_gpu": num_gpu,
        }
        if cfg["main_gpu"] is not None:
            self.opt["main_gpu"] = cfg["main_gpu"]
        if cfg["gpu_layers"] is not None:
            self.opt["gpu_layers"] = cfg["gpu_layers"]
        if cfg["num_thread"] is not None:
            self.opt["num_thread"] = cfg["num_thread"]
        self.http_timeout = cfg["http_timeout"]
        self.system_prompt = kwargs.get("system_prompt") or PROMPT_ROLE

    # --- internals ---------------------------------------------------------
    def _http_generate(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": True,
            "options": self.opt,
        }
        r = requests.post(
            f"{self.url}/api/generate", json=payload, timeout=self.http_timeout
        )
        r.raise_for_status()

        chunks: list[str] = []
        for line in r.iter_lines():
            if not line:
                continue
            part = json.loads(line)
            if txt := part.get("response") or part.get("content"):
                chunks.append(txt)
        return "".join(chunks).strip()

    def _cli_generate(self, prompt: str) -> str:
        cmd = ["ollama", "run", self.model]
        return subprocess.check_output(
            cmd, input=prompt, encoding="utf8", stderr=subprocess.STDOUT
        ).strip()

    # --- utilities ---------------------------------------------------------
    def ensure_server(self, timeout: float = 2.0) -> bool:
        """Return True when the Ollama server responds, False otherwise."""
        try:
            r = requests.get(f"{self.url}/api/version", timeout=timeout)
            r.raise_for_status()
            return True
        except Exception:
            return False

    def build_prompt(self, user_content: str, system_prompt: str | None = None) -> str:
        system = system_prompt or self.system_prompt
        return f"[SYSTEM]\n{system}\n\n[USER]\n{user_content}".strip()

    def generate(self, content: str, *, system_prompt: str | None = None) -> str:
        prompt = self.build_prompt(content, system_prompt)
        try:
            return self._http_generate(prompt)
        except Exception as exc:
            logging.warning("HTTP failed (%s) – falling back to CLI", exc)
            return self._cli_generate(prompt)


# ------------------------------ helpers -------------------------------------
def strip_code_fences(text: str) -> str:
    if text.lstrip().startswith("```") and "```" in text:
        inner = text.split("```", 2)[1]
        if "\n" in inner:
            inner = inner.split("\n", 1)[1]
        return inner.strip()
    return text.strip()


def parse_cases(raw: str) -> List[Dict]:
    cleaned = strip_code_fences(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        try:
            start, end = cleaned.index("["), cleaned.rindex("]") + 1
            data = json.loads(cleaned[start:end])
        except Exception:
            return []
    if not isinstance(data, list):
        return []
    return [
        {k: item.get(k, "") for k in ALLOWED_KEYS if isinstance(item, dict)}
        for item in data
    ]


def pdf_to_text_pages(pdf_path: str) -> List[str]:
    with fitz.open(pdf_path) as doc:
        return [p.get_text() for p in doc]


def _missing_fields(case: dict[str, str]) -> list[str]:
    return [k for k in REQUIRED if not str(case.get(k)).strip()]


def load_prompt_file(path: Path | str | None) -> str | None:
    """Load an optional prompt file, returning ``None`` when unavailable."""
    if not path:
        return None
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        logging.warning("Could not read prompt file %s: %s", path, exc)
        return None


def chunk_document(pages: Sequence[str], combine: int, *, overlap: int = 0) -> list[str]:
    """Combine *pages* into textual chunks with optional sliding-window overlap."""
    if combine < 1:
        raise ValueError("combine must be >= 1")
    if overlap >= combine:
        raise ValueError("overlap must be smaller than combine")

    step = max(1, combine - overlap)
    chunks: list[str] = []
    for start in range(0, len(pages), step):
        part = pages[start : start + combine]
        if not part:
            continue
        if len(part) < combine and start != 0:
            # Already covered by previous overlapping chunk
            continue
        chunks.append("\n\n".join(part))
    return chunks


class KeywordRetriever:
    """Very small TF–IDF retriever to support RAG without external services."""

    def __init__(self, texts: Sequence[str]):
        if not texts:
            raise ValueError("Cannot build retriever with no texts")
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
        except Exception as exc:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "scikit-learn is required for retrieval augmentation"
            ) from exc

        self.texts = list(texts)
        self.vectorizer = TfidfVectorizer(stop_words="english")
        self.matrix = self.vectorizer.fit_transform(self.texts)

    def top_k(self, query: str, k: int, *, exclude_idx: int | None = None) -> list[str]:
        if not query.strip():
            return []
        vec = self.vectorizer.transform([query])
        scores = (self.matrix @ vec.T).toarray().ravel()
        indexed = sorted(
            enumerate(scores), key=lambda item: item[1], reverse=True
        )
        out: list[str] = []
        for idx, _score in indexed:
            if exclude_idx is not None and idx == exclude_idx:
                continue
            out.append(self.texts[idx])
            if len(out) >= k:
                break
        return out


def _log(msg: str, logger: Callable[[str], None] | None) -> None:
    if logger:
        logger(msg)
    else:
        logging.info(msg)


class _ProgressReporter:
    """Fan out progress updates to Qt callbacks and optional tqdm bars."""

    def __init__(
        self,
        *,
        total: int,
        callback: Callable[[int], None] | None,
        show_bar: bool,
    ) -> None:
        self.total = max(total, 0)
        self.callback = callback
        self._bar = None
        self._current = 0
        self._percent = 0

        if show_bar and tqdm is not None and self.total > 0:
            self._bar = tqdm(total=self.total, desc="Chunks", unit="chunk")

        self._emit(0)

    def _emit(self, percent: int) -> None:
        bounded = max(0, min(100, percent))
        self._percent = bounded
        if self.callback:
            self.callback(bounded)
        if self._bar is not None:
            self._bar.refresh()

    def advance(self) -> None:
        if self.total:
            self._current = min(self.total, self._current + 1)
            fraction = self._current / self.total
            percent = int(fraction * 100)
            if percent < self._percent:
                percent = self._percent
            if self._current == self.total:
                percent = 100
        else:
            percent = 100

        if self._bar is not None:
            self._bar.update(1)

        self._emit(percent)

    def pulse(self, index: int) -> None:
        """Emit an in-progress heartbeat for the ``index``-th chunk (1-based)."""

        if self.total <= 0:
            self._emit(min(99, self._percent + 1))
            return

        # Reserve a small fraction of progress to indicate an active chunk without
        # overshooting the eventual completion point (index / total).
        baseline = (index - 1) / self.total
        target = index / self.total
        heartbeat = baseline + max(0.02, 0.25 / self.total)
        heartbeat = min(target - 1e-3, heartbeat)
        heartbeat = max(baseline, heartbeat)
        percent_float = max(heartbeat, 0.0) * 100
        percent = int(percent_float)
        if percent <= self._percent:
            percent = min(99, self._percent + 1)
        self._emit(percent)

    def describe(self, text: str) -> None:
        if self._bar is not None:
            self._bar.set_description_str(text)
            self._bar.refresh()

    def close(self) -> None:
        if self._bar is not None:
            self._bar.close()


# ------------------------------ core ----------------------------------------
class PromptCache:
    """Persist LLM responses keyed by prompt/model combinations."""

    def __init__(
        self,
        directory: Path | str,
        *,
        max_entries: int = 256,
        max_age_hours: float | None = None,
    ) -> None:
        self.directory = Path(directory).expanduser()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "responses.json"
        self.max_entries = max_entries
        self.max_age_seconds = None if max_age_hours is None else max(0, max_age_hours) * 3600
        self._entries: dict[str, dict] = {}
        self._load()

    # internal helpers -------------------------------------------------
    def _load(self) -> None:
        if not self.path.exists():
            self._entries = {}
            return
        try:
            self._entries = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self._entries = {}

    def _normalise_cases(self, cases: Sequence[Dict]) -> list[Dict]:
        return json.loads(json.dumps(list(cases)))  # deep copy via JSON

    def _prune(self) -> None:
        if self.max_entries <= 0:
            self._entries.clear()
            return
        if len(self._entries) <= self.max_entries:
            return
        sorted_items = sorted(
            self._entries.items(), key=lambda item: item[1].get("ts", 0), reverse=True
        )
        self._entries = dict(sorted_items[: self.max_entries])

    def _expired(self, entry: dict) -> bool:
        if not entry:
            return True
        if self.max_age_seconds is None:
            return False
        ts = entry.get("ts")
        if ts is None:
            return True
        return (time.time() - float(ts)) > self.max_age_seconds

    # public API -------------------------------------------------------
    def get(self, key: str) -> list[Dict] | None:
        entry = self._entries.get(key)
        if not entry or self._expired(entry):
            if entry and self._expired(entry):
                self._entries.pop(key, None)
            return None
        return self._normalise_cases(entry.get("cases", []))

    def set(self, key: str, cases: Sequence[Dict]) -> None:
        self._entries[key] = {"cases": self._normalise_cases(cases), "ts": time.time()}
        self._prune()

    def flush(self) -> None:
        if not self._entries:
            # Remove cache file entirely when empty to save disk space
            if self.path.exists():
                try:
                    self.path.unlink()
                except OSError:
                    pass
            return
        try:
            self.path.write_text(json.dumps(self._entries, ensure_ascii=False), encoding="utf-8")
        except OSError:
            logging.warning("Failed to persist cache at %s", self.path)


def _cache_key(prompt: str, agent: AgentLocal, *, system_prompt: str | None) -> str:
    payload = {
        "prompt": prompt,
        "system": system_prompt or agent.system_prompt,
        "model": agent.model,
        "options": agent.opt,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def generate_test_cases(
    pdf_path: str,
    agent: AgentLocal,
    n_cases: int = 6,
    combine: int = 3,
    *,
    prompt_role: str | None = None,
    use_rag: bool = False,
    rag_top_k: int = 2,
    chunk_overlap: int = 0,
    progress_cb: Callable[[int], None] | None = None,
    show_progress: bool = False,
    logger: Callable[[str], None] | None = None,
    cache: PromptCache | None = None,
) -> List[Dict]:
    """Extract pages, chunk, call LLM, repair missing fields if needed.

    Parameters
    ----------
    pdf_path:
        Path to the requirements PDF.
    agent:
        Configured :class:`AgentLocal` (or drop-in replacement) used to call the
        LLM.
    n_cases:
        Target number of cases per chunk. The LLM ultimately decides but this
        is embedded into the system instructions.
    combine:
        Number of PDF pages combined into a single prompt chunk.
    prompt_role:
        Optional override for the system prompt. Defaults to the built-in
        expert test architect instructions.
    use_rag / rag_top_k / chunk_overlap:
        Retrieval-augmentation controls. When enabled the current chunk will be
        enriched with the ``rag_top_k`` most similar chunks selected via
        TF–IDF. ``chunk_overlap`` controls sliding-window reuse of pages when
        chunking the PDF.
    progress_cb:
        Optional callback that receives an integer ``0-100`` progress update.
    show_progress:
        When ``True`` and :mod:`tqdm` is installed, render a console progress
        bar mirroring the callback updates. Useful for CLI/scripts that do not
        hook into the Qt GUI.
    logger:
        Optional callable (``str -> None``) receiving human-readable log lines.
    """
    pages = pdf_to_text_pages(pdf_path)
    if not pages:
        raise RuntimeError(f"No text extracted from {pdf_path!s}")

    chunks = chunk_document(pages, combine, overlap=chunk_overlap)
    _log(
        f"Loaded {len(pages)} page(s); processing {len(chunks)} chunk(s) with combine={combine} and overlap={chunk_overlap}",
        logger,
    )
    retriever: KeywordRetriever | None = None
    if use_rag and len(chunks) > 1:
        try:
            retriever = KeywordRetriever(chunks)
            _log("RAG retriever initialised (TF–IDF)", logger)
        except Exception as exc:
            _log(f"⚠️  Failed to initialise retriever: {exc}", logger)
            retriever = None

    sys_instr = (
        f"Generate {n_cases} independent test cases following the JSON schema, "
        "including the 'linked_requirements' field."
    )

    all_cases: List[Dict] = []
    progress = _ProgressReporter(
        total=len(chunks),
        callback=progress_cb,
        show_bar=show_progress,
    )

    cache_hits = 0
    cache_misses = 0

    try:
        for idx, chunk in enumerate(chunks, 1):
            progress.describe(f"Chunk {idx}/{len(chunks)}")
            progress.pulse(idx)
            _log(f"⏳ Generating chunk {idx}/{len(chunks)}…", logger)
            prompt = (
                f"{sys_instr}\n-----\nRequirements excerpt ({idx}/{len(chunks)}):\n"
                f"{chunk}\n-----"
            )
            if retriever:
                related = retriever.top_k(chunk, rag_top_k, exclude_idx=idx - 1)
                if related:
                    prompt += (
                        "\nRelated context (auto-selected):\n"
                        + "\n-----\n".join(related)
                    )
            cache_key = None
            cases: List[Dict] | None = None
            if cache:
                cache_key = _cache_key(prompt, agent, system_prompt=prompt_role)
                cases = cache.get(cache_key)
                if cases is not None:
                    cache_hits += 1
                    _log(
                        f"💾 Cache hit for chunk {idx}/{len(chunks)} (reusing {len(cases)} cases)",
                        logger,
                    )

            t0 = time.perf_counter()
            if cases is None:
                raw = agent.generate(prompt, system_prompt=prompt_role)
                cases = parse_cases(raw)

                if not cases:
                    retry_prompt = (
                        f"{prompt}\n\nThe answer must be a JSON array with the required keys."
                    )
                    raw_retry = agent.generate(retry_prompt, system_prompt=prompt_role)
                    cases = parse_cases(raw_retry)

                cache_misses += 1

            # --- self-repair if missing fields --------------------------------
            incomplete = [c for c in cases if _missing_fields(c)]
            if incomplete:
                repair_prompt = (
                    "The following cases have empty fields. Rewrite them with every "
                    "field filled. Schema unchanged.\n\n"
                    f"{json.dumps(incomplete, ensure_ascii=False, indent=2)}"
                )
                fixed_raw = agent.generate(repair_prompt, system_prompt=prompt_role)
                fix = parse_cases(fixed_raw)
                fixes_iter = iter(fix)
                for i, c in enumerate(cases):
                    if _missing_fields(c):
                        cases[i] = next(fixes_iter, c)

            if cache and cache_key:
                cache.set(cache_key, cases)

            dt = time.perf_counter() - t0
            logging.info(
                "Chunk %d/%d → %d cases (%.1fs)", idx, len(chunks), len(cases), dt
            )
            all_cases.extend(cases)

            progress.advance()
            _log(
                f"✅ Chunk {idx}/{len(chunks)} complete – {len(cases)} cases in {dt:.1f}s",
                logger,
            )
    finally:
        progress.close()
        if cache:
            cache.flush()
            if cache_hits or cache_misses:
                _log(
                    f"💾 Cache stats – hits: {cache_hits}, misses: {cache_misses}",
                    logger,
                )

    _log(
        f"Generated {len(all_cases)} total test case(s) from {len(chunks)} chunk(s)",
        logger,
    )

    return all_cases


def _normalise_list(val) -> list[str]:
    if isinstance(val, list):
        return [str(v).strip() for v in val if str(v).strip()]
    if isinstance(val, str):
        parts = [p.strip() for p in re.split(r"[,;\n]+", val) if p.strip()]
        return parts
    return []


def _count_steps(value) -> int:
    if isinstance(value, list):
        return len([v for v in value if str(v).strip()])
    if isinstance(value, str):
        return len([v for v in re.split(r"\n+|;", value) if v.strip()])
    return 0


def summarise_cases(cases: Sequence[Dict]) -> Dict[str, object]:
    """Produce simple analytics about generated cases for quick QA."""

    total = len(cases)
    missing_counts = {field: 0 for field in ALLOWED_KEYS}
    complete_cases = 0
    requirement_counter: Counter[str] = Counter()
    name_counter: Counter[str] = Counter()
    action_lengths: list[int] = []
    result_lengths: list[int] = []

    for case in cases:
        missing = False
        for field in ALLOWED_KEYS:
            value = case.get(field, "")
            if not str(value).strip():
                missing_counts[field] += 1
                if field in REQUIRED:
                    missing = True
        if not missing:
            complete_cases += 1

        for req in _normalise_list(case.get("linked_requirements", [])):
            requirement_counter[req] += 1

        name = str(case.get("testcase_name", "")).strip()
        if name:
            name_counter[name] += 1

        action_lengths.append(_count_steps(case.get("actions")))
        result_lengths.append(_count_steps(case.get("expected_results")))

    duplicates = [name for name, count in name_counter.items() if count > 1]
    avg_actions = mean([n for n in action_lengths if n]) if any(action_lengths) else 0
    avg_results = mean([n for n in result_lengths if n]) if any(result_lengths) else 0

    return {
        "total_cases": total,
        "complete_cases": complete_cases,
        "missing_fields": missing_counts,
        "unique_requirements": len(requirement_counter),
        "top_requirements": requirement_counter.most_common(5),
        "duplicate_names": duplicates,
        "avg_actions": round(avg_actions, 2) if avg_actions else 0,
        "avg_expected_results": round(avg_results, 2) if avg_results else 0,
    }


# ------------------------------ writers -------------------------------------
def _serialise(val):
    if isinstance(val, list):
        return "\n".join(map(str, val))
    return str(val)


def save_csv(cases: List[Dict], path: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=ALLOWED_KEYS)
        writer.writeheader()
        for row in cases:
            writer.writerow({k: _serialise(v) for k, v in row.items()})


def save_xlsx(cases: List[Dict], path: str) -> None:
    df = pd.DataFrame(
        [{k: _serialise(v) for k, v in r.items()} for r in cases],
        columns=ALLOWED_KEYS,
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        df.to_excel(xl, index=False, sheet_name="TestCases")
        ws = xl.sheets["TestCases"]
        for idx, col in enumerate(df.columns, 1):
            values = [str(v) for v in df[col] if str(v).strip()]
            if not values:
                continue
            maxlen = max(max(map(len, values)), len(col)) + 2
            ws.column_dimensions[get_column_letter(idx)].width = maxlen
