# (HTML) in‑app documentation for the many parameters (unchanged)
PARAMETER_DOC = """
<h1>Parameter Reference</h1>

<h2>Document Chunking</h2>
<ul>
  <li><b>Batch size (pages):</b>
    <br>How many PDF pages you group into each processing batch.
    <br><i>Example:</i> With 3 pages and batch size 3, you send all pages in one call; with size 1 you call the LLM three times.</li>
  <li><b>Combine pages:</b>
    <br>Within each batch, how many pages to merge into a single prompt excerpt.
    <br><i>Example:</i> Batch 5 + combine 2 → pages [1–2], [3–4], [5].</li>
</ul>

<h2>Test-Case Generation</h2>
<ul>
  <li><b>Cases per prompt:</b>
    <br>How many independent test cases the LLM should return for each excerpt.
    <br><i>Tip:</i> Too many can dilute detail; too few may under-utilize the prompt.</li>
  <li><b>Parallel prompts:</b>
    <br>How many identical requests to fire concurrently for each excerpt.
    <br><i>Note:</i> Speeds up throughput only if your local LLM server supports parallel calls.</li>
</ul>

<h2>Sampling Controls</h2>
<ul>
  <li><b>Temperature (0–2):</b>
    <br>0 = deterministic (always top choice), 1 = balanced, 2 = high creativity.
    <br><i>Use ~0.4–0.8 for consistent, yet varied JSON outputs.</i></li>
  <li><b>Top-p (0–1):</b>
    <br>Nucleus sampling: only consider the smallest token set whose cumulative probability ≥ p.
    <br><i>Set to 0.8–0.9 for good variety without odd tails.</i></li>
  <li><b>Top-k (1–1000):</b>
    <br>Restrict sampling to the k highest-probability tokens.
    <br><i>Use k≈50–200 for a balanced mix of safety and diversity.</i></li>
</ul>

<h2>Hardware Acceleration</h2>
<ul>
  <li><b>Enable GPU acceleration:</b>
    <br>When checked, the app asks Ollama to keep inference on the GPU (`num_gpu &gt;= 1`).
    <br>Disable to force CPU-only mode.</li>
  <li><b>GPUs to use:</b>
    <br>How many GPUs Ollama may offload to. Set to <em>Auto</em> (−1) to let Ollama decide.</li>
  <li><b>Primary GPU:</b>
    <br>Select which GPU index owns the main KV cache when multiple devices are present. <em>Auto</em> lets Ollama pick.</li>
  <li><b>GPU layers:</b>
    <br>Limit how many transformer layers stay resident on the GPU. Use <em>Auto</em> to load as many as will fit.</li>
  <li><b>CPU threads:</b>
    <br>Override the number of CPU worker threads when running in CPU mode. Leave on <em>Auto</em> to use Ollama defaults.</li>
</ul>

<h2>Context Enhancements</h2>
<ul>
  <li><b>Enable retrieval augmentation:</b>
    <br>When checked, the application builds a TF–IDF index of the current document
    and appends the most relevant extra snippets to each LLM prompt.</li>
  <li><b>Related chunks:</b>
    <br>How many similar snippets to append to the current excerpt.
    <br><i>Tip:</i> Start with 1–2 to keep prompts compact.</li>
  <li><b>Chunk overlap:</b>
    <br>Controls how many pages slide into neighbouring chunks to improve retrieval quality.</li>
</ul>

<h2>Performance & Cache</h2>
<ul>
  <li><b>Enable disk cache:</b>
    <br>Skips fresh LLM calls for chunks that have already been generated recently.
    <br><i>Tip:</i> Turn this on when you are iterating on exports or doing QA reviews.</li>
  <li><b>Max age (hours):</b>
    <br>Time-to-live for cached responses. Set to <em>Unlimited</em> to keep them indefinitely.</li>
  <li><b>Max entries:</b>
    <br>Upper bound on the number of chunk responses stored on disk. Older entries are pruned first.</li>
  <li><b>Location:</b>
    <br>Where responses are written. Use a fast SSD for the best load times.</li>
</ul>

<h2>Run Feedback</h2>
<ul>
  <li><b>Show CLI progress:</b>
    <br>Enable the optional <code>tqdm</code> bar when running from scripts for a mirrored progress indicator.</li>
  <li><b>Stream log updates:</b>
    <br>Provide a <code>logger</code> callback (for example <code>print</code>) to see “starting chunk” and “chunk complete” status messages in real time.</li>
</ul>

<h2>Output Length</h2>
<ul>
  <li><b>Max tokens:</b>
    <br>Maximum number of tokens the model will generate <em>beyond</em> your prompt.
    <br><i>256–1024</i> is usually enough for several test cases; bump up if you need more detail.</li>
</ul>

<h2>Quick Example (“Sweet Spot” for a 3-Page PDF)</h2>
<ol>
  <li>Batch size = 3, Combine pages = 3 → one single prompt of all pages.</li>
  <li>Cases per prompt = 6 → yields ~6 test cases in one shot.</li>
  <li>Parallel prompts = 1 → a single, focused request.</li>
  <li>Sampling: Temp = 0.6, Top-p = 0.9, Top-k = 100, Max tokens = 1024.</li>
</ol>

<p style="font-size:0.9em; color:gray;">
You can tweak these sliders to balance <strong>speed</strong> (bigger batches, more parallelism) versus 
<strong>quality</strong> (smaller excerpts, fewer cases/prompt, conservative sampling).
</p>
"""
