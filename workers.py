# ─────────────────────────────────────────────────────────────────────────────
# file: workers.py
# ─────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

import traceback

from pathlib import Path
from typing import Any, Dict, List

from PyPDF2 import PdfReader
from PySide6.QtCore import QObject, QRunnable, Signal

from config_models import AppConfig
from core import AgentLocal, PromptCache, generate_test_cases, load_prompt_file


class _Signals(QObject):
    """Qt signals container so we can emit from the worker thread."""

    progress = Signal(int)          # 0‑100 %
    message = Signal(str)           # log lines
    finished = Signal(list)         # list[dict] with test‑case objects
    error = Signal(str)


class GeneratorTask(QRunnable):
    """Runs test‑case generation in a background thread.

    Parameters
    ----------
    pdf : pathlib.Path | str  – requirements PDF to parse
    cfg : dict                – full config dict assembled by GUI (model + generation)
    """

    def __init__(self, pdf: Path | str, cfg: dict[str, Any]):
        super().__init__()
        self.pdf = Path(pdf)
        self.cfg = cfg
        self.signals = _Signals()

    # ------------------------------------------------------------------
    # Qt entry‑point
    # ------------------------------------------------------------------
    def run(self):
        try:
            self.signals.message.emit("👊 Parsing PDF…")
            # Count pages in the PDF
            try:
                reader = PdfReader(str(self.pdf))
                num_pages = len(reader.pages)
            except Exception:
                num_pages = "unknown"
            self.signals.message.emit(f"📑 Parsed {num_pages} pages, starting generation…")

            # Build agent configuration
            cfg = AppConfig.from_dict(self.cfg)
            model_cfg = cfg.model
            prompt_role = load_prompt_file(cfg.paths.prompt_path)

            agent = AgentLocal(
                model=model_cfg.name,
                ollama_url=model_cfg.url,
                temperature=model_cfg.temperature,
                top_p=model_cfg.top_p,
                top_k=model_cfg.top_k,
                max_tokens=model_cfg.max_tokens,
                http_timeout=model_cfg.http_timeout,
                system_prompt=prompt_role,
                use_gpu=model_cfg.use_gpu,
                num_gpu=model_cfg.num_gpu,
                main_gpu=model_cfg.main_gpu,
                gpu_layers=model_cfg.gpu_layers,
                num_thread=model_cfg.num_thread,
            )

            if agent.ensure_server():
                self.signals.message.emit("🌐 Ollama endpoint reachable")
            else:
                self.signals.message.emit(
                    "⚠️ Ollama HTTP endpoint unavailable, CLI fallback may be used"
                )

            gen_cfg = cfg.generation
            rag_cfg = cfg.rag

            cache = None
            cache_cfg = cfg.cache
            if cache_cfg.enabled and cache_cfg.directory:
                try:
                    cache = PromptCache(
                        cache_cfg.directory,
                        max_entries=cache_cfg.max_entries,
                        max_age_hours=cache_cfg.max_age_hours,
                    )
                    self.signals.message.emit(
                        f"💾 Using cache at {cache.path}"
                    )
                except Exception as exc:
                    cache = None
                    self.signals.message.emit(
                        f"⚠️ Cache disabled due to error: {exc}"
                    )

            cases: List[Dict] = generate_test_cases(
                pdf_path=str(self.pdf),
                agent=agent,
                n_cases=gen_cfg.cases_per_prompt,
                combine=gen_cfg.combine_pages,
                prompt_role=prompt_role,
                use_rag=rag_cfg.enabled,
                rag_top_k=rag_cfg.top_k,
                chunk_overlap=rag_cfg.chunk_overlap,
                progress_cb=self.signals.progress.emit,
                logger=self.signals.message.emit,
                cache=cache,
            )

            # Finished
            self.signals.finished.emit(cases)
        except Exception as exc:
            self.signals.error.emit(str(exc))
            self.signals.message.emit(traceback.format_exc())