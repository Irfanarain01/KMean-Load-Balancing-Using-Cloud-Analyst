# 7-agent research paper pipeline

`python pipeline.py "your topic" --max-iterations 3 --out output` (needs `OPENROUTER_API_KEY`, or `OPENAI_API_KEY` for OpenAI directly; optional `PAPER_MODEL` such as
`anthropic/claude-sonnet-4.5` or `openai/gpt-4o` (default),
`SEMANTIC_SCHOLAR_API_KEY`; `pandoc` for Word, `pdflatex`/`tectonic` for PDF).

1. Supervisor (start): turns the topic into a brief, research questions and search queries.
2. Literature: arXiv + Semantic Scholar, 2021-2026 only; BibTeX is built from API metadata, not by the LLM.
3. Gap analyst: limitations / future work of the retrieved papers; picks the gap to attack.
4. Methodology: new method, equations, open-source tools.
5. Simulation: writes, runs and auto-debugs (3 tries) a Python simulation; results come from real output.
6. Writer: abstract, introduction, literature review, methodology, results, discussion, future work, conclusion, references.
7. Final reviewer (end): scores the paper. Agents 1 and 7 loop (rerunning from the earliest stage that needs work)
   until both accept or `--max-iterations` is hit.

Output (`output/`): `paper.tex`, `references.bib`, `paper.docx`, `paper.pdf`, `figures/`, `simulation.py`,
`simulation_output.txt`, `review.txt`.

Safeguards: unknown `\cite` keys force a rejection and are stripped on export; a failed simulation or empty
literature search blocks approval. Generated code runs in a temp dir with a timeout but is not sandboxed, so run
the pipeline inside a container. Gap analysis uses abstracts only. Always check the references and results yourself
before submitting anything.
