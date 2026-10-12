"""Seven-agent research-paper pipeline (LangGraph).

Agent 1  Supervisor        (start + loop)  refines the topic, signs off or orders a revision
Agent 2  Literature        searches arXiv + Semantic Scholar, 2021-2026 only
Agent 3  Gap Analyst       extracts limitations / future work and picks the gap to attack
Agent 4  Methodology       designs the new method (equations, open-source tools)
Agent 5  Simulation        writes, runs and debugs a Python simulation; collects real output
Agent 6  Writer            writes every section in LaTeX, citing only retrieved papers
Agent 7  Final Reviewer    (end + loop)    scores the paper; Agent 1 and 7 loop until both accept

Output: LaTeX (+ .bib), Word (.docx via pandoc) and PDF (pdflatex/tectonic if installed, else pandoc).

Usage:  export OPENAI_API_KEY=...   (optional: PAPER_MODEL, SEMANTIC_SCHOLAR_API_KEY)
        python pipeline.py "your topic" [--max-iterations 3] [--out output]
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from typing import List, Literal, Optional, TypedDict

import requests
from pydantic import BaseModel, Field

from langchain_core.messages import HumanMessage
from langgraph.graph import END, StateGraph

YEAR_FROM, YEAR_TO = 2021, 2026
DEFAULT_TOPIC = ("Machine-learning-based load balancing for cloud computing: "
                 "a neural-network approach evaluated by simulation")
STAGES = ["literature", "gaps", "methodology", "simulation", "writer"]


# ----------------------------------------------------------------- LLM access
_llm = None


def get_llm():
    global _llm
    if _llm is None:
        from langchain_openai import ChatOpenAI
        _llm = ChatOpenAI(model=os.environ.get("PAPER_MODEL", "gpt-4o"), temperature=0.2)
    return _llm


def ask(prompt: str) -> str:
    return get_llm().invoke([HumanMessage(content=prompt)]).content


def ask_struct(schema, prompt: str):
    return get_llm().with_structured_output(schema).invoke([HumanMessage(content=prompt)])


# --------------------------------------------------------------- data schemas
class Paper(TypedDict):
    key: str
    title: str
    authors: List[str]
    year: int
    venue: str
    abstract: str
    url: str
    source: str


class State(TypedDict, total=False):
    topic: str
    refined_topic: str
    research_questions: str
    search_queries: List[str]
    papers: List[Paper]
    literature_notes: str
    gap_analysis: str
    chosen_gap: str
    methodology: str
    sim_code: str
    sim_output: str
    sim_ok: bool
    figures: List[str]
    results_analysis: str
    paper: dict
    review: dict
    unknown_cites: List[str]
    revision_instructions: str
    rerun_from: str
    approved: bool
    iteration: int
    max_iterations: int
    out_dir: str


class SupervisorDecision(BaseModel):
    refined_topic: str = Field(description="Precise topic: domain, baselines, metrics, simulation goal.")
    research_questions: str = Field(description="2-4 concrete research questions / hypotheses.")
    search_queries: List[str] = Field(description="4-6 SHORT keyword queries (3-6 words) for paper search.")
    approved: bool = Field(description="True only if the reviewed paper is publication quality AND matches the brief.")
    rerun_from: Literal["literature", "gaps", "methodology", "simulation", "writer"] = Field(
        description="Earliest stage that must be redone to fix the reviewer's critique.")
    revision_instructions: str = Field(description="Concrete orders for the agents that rerun. Empty if approved.")


class Review(BaseModel):
    novelty: int = Field(ge=1, le=10)
    rigor: int = Field(ge=1, le=10)
    clarity: int = Field(ge=1, le=10)
    reproducibility: int = Field(ge=1, le=10)
    literature_coverage: int = Field(ge=1, le=10)
    accept: bool = Field(description="True only if every score >= 7 and no fabricated claims are present.")
    critique: str = Field(description="Specific, section-by-section problems to fix.")
    suggested_rerun_from: Literal["literature", "gaps", "methodology", "simulation", "writer"]


class PaperSections(BaseModel):
    title: str
    keywords: List[str]
    abstract: str = Field(description="LaTeX text, 150-250 words, no \\section.")
    introduction: str
    literature_review: str
    methodology: str
    results: str
    discussion: str
    future_work: str
    conclusion: str


# ------------------------------------------------------------- search tools
def _clean(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def search_arxiv(query: str, n: int = 8) -> List[Paper]:
    q = f"all:{query} AND submittedDate:[{YEAR_FROM}01010000 TO {YEAR_TO}12312359]"
    try:
        r = requests.get("http://export.arxiv.org/api/query",
                         params={"search_query": q, "max_results": n, "sortBy": "relevance"}, timeout=20)
        r.raise_for_status()
        ns = {"a": "http://www.w3.org/2005/Atom"}
        out = []
        for e in ET.fromstring(r.text).findall("a:entry", ns):
            year = int(e.findtext("a:published", "0000", ns)[:4])
            out.append(Paper(
                key="", title=_clean(e.findtext("a:title", "", ns)),
                authors=[_clean(a.findtext("a:name", "", ns)) for a in e.findall("a:author", ns)],
                year=year, venue="arXiv preprint", abstract=_clean(e.findtext("a:summary", "", ns)),
                url=_clean(e.findtext("a:id", "", ns)), source="arXiv"))
        return out
    except Exception as ex:
        print(f"  [arXiv] '{query}': {ex}")
        return []


def search_semantic_scholar(query: str, n: int = 8) -> List[Paper]:
    headers = {"x-api-key": os.environ["SEMANTIC_SCHOLAR_API_KEY"]} if os.environ.get("SEMANTIC_SCHOLAR_API_KEY") else {}
    params = {"query": query, "year": f"{YEAR_FROM}-{YEAR_TO}", "limit": n,
              "fields": "title,authors,year,abstract,venue,url,externalIds"}
    for attempt in range(2):
        try:
            r = requests.get("https://api.semanticscholar.org/graph/v1/paper/search",
                             params=params, headers=headers, timeout=20)
            if r.status_code == 429 and attempt == 0:
                time.sleep(3)
                continue
            r.raise_for_status()
            out = []
            for p in r.json().get("data") or []:
                doi = (p.get("externalIds") or {}).get("DOI")
                out.append(Paper(
                    key="", title=_clean(p.get("title")),
                    authors=[a.get("name", "") for a in p.get("authors") or []],
                    year=p.get("year") or 0, venue=_clean(p.get("venue")) or "Semantic Scholar",
                    abstract=_clean(p.get("abstract")),               # may be None in the API
                    url=f"https://doi.org/{doi}" if doi else (p.get("url") or ""), source="SemanticScholar"))
            return out
        except Exception as ex:
            print(f"  [SemanticScholar] '{query}': {ex}")
            return []
    return []


def make_keys(papers: List[Paper]) -> List[Paper]:
    used = set()
    for p in papers:
        last = re.sub(r"[^a-z]", "", (p["authors"][0].split()[-1] if p["authors"] else "anon").lower()) or "anon"
        word = re.sub(r"[^a-z]", "", (re.findall(r"[A-Za-z]{4,}", p["title"]) or ["paper"])[0].lower())
        base, key, i = f"{last}{p['year']}{word}", None, 0
        key = base
        while key in used:
            i += 1
            key = f"{base}{chr(96 + i)}"
        used.add(key)
        p["key"] = key
    return papers


def bib_escape(s: str) -> str:
    for a, b in (("\\", " "), ("&", r"\&"), ("%", r"\%"), ("#", r"\#"), ("_", r"\_"), ("$", r"\$")):
        s = s.replace(a, b)
    return s


def to_bibtex(papers: List[Paper]) -> str:
    out = []
    for p in papers:
        out.append("@misc{%s,\n  title = {{%s}},\n  author = {%s},\n  year = {%s},\n  howpublished = {%s},\n  url = {%s}\n}\n" % (
            p["key"], bib_escape(p["title"]), bib_escape(" and ".join(p["authors"]) or "Anonymous"),
            p["year"], bib_escape(p["venue"]), p["url"].replace("%", r"\%")))
    return "\n".join(out)


# ------------------------------------------------------- simulation sandbox
def run_python(code: str, timeout: int = 300):
    """Run generated code in a throw-away directory. NOT a security sandbox: run the pipeline in a container."""
    work = tempfile.mkdtemp(prefix="sim_")
    path = os.path.join(work, "sim.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    try:
        r = subprocess.run([sys.executable, path], cwd=work, capture_output=True, text=True, timeout=timeout)
        ok, text = r.returncode == 0, (r.stdout if r.returncode == 0 else r.stdout + "\n" + r.stderr)
    except subprocess.TimeoutExpired:
        ok, text = False, f"Timed out after {timeout}s"
    return ok, text[-6000:], work


def extract_code(text: str) -> str:
    m = re.search(r"```(?:python)?\s*(.*?)```", text, re.DOTALL)
    return (m.group(1) if m else text).strip()


# ===================================================================== AGENTS
def supervisor(state: State):
    first = not state.get("review")
    print(f"\n[Agent 1: Supervisor] {'defining the brief' if first else 'judging the review'} "
          f"(round {state.get('iteration', 0)}/{state['max_iterations']})")
    if first:
        prompt = f"""You are the Principal Research Supervisor. The author wants a publishable paper on:
"{state['topic']}"
Fields in scope: cloud computing, neural networks, machine learning. Literature window: {YEAR_FROM}-{YEAR_TO}.
Never block on vagueness: state your assumptions in the refined topic (baselines, metrics, simulation goal).
Set approved=False, rerun_from='literature', revision_instructions=''."""
    else:
        prompt = f"""You are the Principal Research Supervisor judging whether the paper is ready.
Brief: {state['refined_topic']}
Research questions: {state['research_questions']}
Reviewer scores/critique: {state['review']}
Unverifiable citation keys found in the draft: {state.get('unknown_cites') or 'none'}
Simulation ran successfully: {state.get('sim_ok')}
Approve ONLY if the reviewer accepted, the paper answers the research questions, and the simulation really ran.
Otherwise approved=False, choose the earliest stage needing rework and give concrete orders.
Keep refined_topic/research_questions/search_queries as they are: {state['refined_topic']} | {state['research_questions']} | {state.get('search_queries')}"""
    d = ask_struct(SupervisorDecision, prompt)
    upd = {"approved": d.approved, "rerun_from": d.rerun_from, "revision_instructions": d.revision_instructions}
    if first:
        upd.update(refined_topic=d.refined_topic, research_questions=d.research_questions,
                   search_queries=d.search_queries, approved=False, rerun_from="literature")
    elif d.approved and not state["review"].get("accept"):
        upd["approved"] = False                      # reviewer veto
    return upd


def literature_agent(state: State):
    print("\n[Agent 2: Literature] searching arXiv + Semantic Scholar, 2021-2026")
    found: List[Paper] = []
    for q in state["search_queries"]:
        found += search_arxiv(q) + search_semantic_scholar(q)
        time.sleep(1)
    seen, papers = set(), []
    for p in found:
        t = re.sub(r"\W+", "", p["title"].lower())
        if t and t not in seen and YEAR_FROM <= p["year"] <= YEAR_TO and p["abstract"]:
            seen.add(t)
            papers.append(p)
    papers = make_keys(papers[:40])
    print(f"  kept {len(papers)} papers")
    if not papers:
        return {"papers": [], "literature_notes": "NO PAPERS RETRIEVED - network/API failure."}
    listing = "\n\n".join(f"[{p['key']}] {p['title']} ({p['year']}, {p['venue']})\n{p['abstract'][:700]}" for p in papers)
    notes = ask(f"""You are a literature analyst. Topic: {state['refined_topic']}
Using ONLY the papers below, write structured notes: themes, state of the art, methods, datasets/benchmarks, reported
results. Cite each claim with its [key]. Do not invent papers.\n\n{listing}""")
    return {"papers": papers, "literature_notes": notes}


def gap_agent(state: State):
    print("\n[Agent 3: Gap Analyst] extracting limitations and future work")
    extra = f"\nRevision orders: {state['revision_instructions']}" if state.get("revision_instructions") else ""
    listing = "\n\n".join(f"[{p['key']}] {p['title']}: {p['abstract'][:700]}" for p in state["papers"])
    res = ask(f"""You are a research gap analyst. Topic: {state['refined_topic']}{extra}
From the abstracts below (only abstracts are available, so say when a limitation is inferred rather than stated),
list for each relevant paper its limitations and the future work it suggests, cite with [key]. Then:
1. Cluster the recurring limitations.
2. Choose ONE tractable gap that can be attacked by a simulation, and justify it.
End with a line starting 'CHOSEN GAP:'.\n\n{listing}\n\nNotes:\n{state['literature_notes']}""")
    m = re.search(r"CHOSEN GAP:\s*(.+)", res, re.DOTALL)
    return {"gap_analysis": res, "chosen_gap": (m.group(1) if m else res[-800:]).strip()}


def methodology_agent(state: State):
    print("\n[Agent 4: Methodology] designing the new method")
    extra = f"\nRevision orders: {state['revision_instructions']}" if state.get("revision_instructions") else ""
    res = ask(f"""You are an algorithmic systems architect. Topic: {state['refined_topic']}
Research questions: {state['research_questions']}
Gap to address: {state['chosen_gap']}{extra}
Design a NOVEL method addressing the gap. Provide: problem formulation with equations (LaTeX), the algorithm in
pseudo-code, complexity, system architecture, baselines to compare against, evaluation metrics, and the open-source
tools/languages to use (Python, NumPy, SciPy, PyTorch/scikit-learn, SimPy, CloudSim, ...). Be implementable in a
single-machine simulation. Cite related work with [key] where relevant.
Literature notes:\n{state['literature_notes'][:6000]}""")
    return {"methodology": res}


def simulation_agent(state: State):
    print("\n[Agent 5: Simulation] writing, running and debugging the experiment")
    extra = f"\nRevision orders: {state['revision_instructions']}" if state.get("revision_instructions") else ""
    prompt = f"""Write ONE self-contained Python 3 script implementing and evaluating this methodology against the stated
baselines. Use only numpy, scipy, pandas, matplotlib, scikit-learn (and torch only if already needed). Requirements:
fixed random seeds, at least 10 independent runs per configuration, print mean +/- std (or 95% CI) for every metric
as a plain-text table to stdout, save figures as PNG files in the current directory, finish in under 4 minutes.
Do not fabricate results: every printed number must be computed.{extra}
Methodology:\n{state['methodology']}
Return only a ```python block."""
    code, ok, out, work = "", False, "", None
    for attempt in range(1, 4):
        code = extract_code(ask(prompt))
        ok, out, work = run_python(code)
        print(f"  attempt {attempt}: {'OK' if ok else 'FAILED'}")
        if ok:
            break
        prompt = f"The script below failed. Fix it and return the full corrected ```python block.\n\nError:\n{out}\n\nScript:\n```python\n{code}\n```"
    figs = []
    if ok:
        os.makedirs(os.path.join(state["out_dir"], "figures"), exist_ok=True)
        for f in sorted(os.listdir(work)):
            if f.lower().endswith(".png"):
                shutil.copy(os.path.join(work, f), os.path.join(state["out_dir"], "figures", f))
                figs.append(f"figures/{f}")
    analysis = ask(f"""You are a data scientist. Write the analysis of these REAL simulation results. Quote only numbers
that appear in the output; if the run failed, say so plainly and do not invent numbers.
Methodology:\n{state['methodology'][:3000]}\nRun succeeded: {ok}\nOutput:\n{out}\nFigures: {figs}
Produce: a LaTeX results table (booktabs) built from the output, interpretation vs baselines, threats to validity.""")
    return {"sim_code": code, "sim_output": out, "sim_ok": ok, "figures": figs, "results_analysis": analysis}


def writer_agent(state: State):
    print("\n[Agent 6: Writer] writing the full paper in LaTeX")
    keys = ", ".join(p["key"] for p in state["papers"])
    fb = ""
    if state.get("review"):
        fb = f"\nREVISION: fix this reviewer critique and these orders.\nCritique: {state['review']['critique']}\nOrders: {state.get('revision_instructions')}\nPrevious draft title: {state['paper'].get('title')}"
    p = ask_struct(PaperSections, f"""You are a senior academic author. Write a complete journal-style paper (LaTeX body text, no
preamble, no \\section commands - the template adds headings) on: {state['refined_topic']}
Research questions: {state['research_questions']}
HARD RULES:
- Cite with \\cite{{key}} using ONLY these keys: {keys}. Never invent a reference.
- Report only numbers present in the simulation output. State clearly that results come from simulation.
- Include figures only from this list, with \\includegraphics[width=\\linewidth]{{path}}: {state.get('figures')}.
- If the simulation failed or no papers were retrieved, say so in Limitations rather than inventing content.
- Sections: abstract, introduction (problem, motivation, contributions), literature review (themes, gaps),
  methodology (equations, algorithm), results (tables/figures), discussion (limitations), future work, conclusion.{fb}
INPUTS
[Literature notes] {state['literature_notes']}
[Gap analysis] {state['gap_analysis'][:5000]}
[Methodology] {state['methodology']}
[Results analysis] {state['results_analysis']}
[Simulation output] {state['sim_output'][:3000]}""")
    return {"paper": p.model_dump()}


SECTION_ORDER = [("introduction", "Introduction"), ("literature_review", "Literature Review"),
                 ("methodology", "Methodology"), ("results", "Results"), ("discussion", "Discussion"),
                 ("future_work", "Future Work"), ("conclusion", "Conclusion")]


def unknown_cites(paper: dict, papers: List[Paper]):
    known = {p["key"] for p in papers}
    text = " ".join(str(v) for v in paper.values())
    used = {k.strip() for grp in re.findall(r"\\cite[a-z]*\{([^}]*)\}", text) for k in grp.split(",")}
    return sorted(used - known)


def reviewer_agent(state: State):
    print(f"\n[Agent 7: Final Reviewer] reviewing draft {state.get('iteration', 0) + 1}")
    bad = unknown_cites(state["paper"], state["papers"])
    sections = "\n\n".join(f"## {n}\n{state['paper'][k]}" for k, n in [("abstract", "Abstract")] + SECTION_ORDER)
    r = ask_struct(Review, f"""You are a strict peer reviewer for an IEEE/ACM-level journal. Brief: {state['refined_topic']}
Check: contributions are novel w.r.t. the cited 2021-2026 work; methodology is sound; every number in Results matches the
simulation output below; all required sections exist; no claim lacks support; references are real.
Citation keys NOT in the retrieved bibliography (fabricated): {bad or 'none'}
Simulation succeeded: {state.get('sim_ok')}
Simulation output:\n{state['sim_output'][:3000]}
PAPER\n{sections}""")
    review = r.model_dump()
    if bad or not state.get("sim_ok") or not state["papers"]:
        review["accept"] = False
    return {"review": review, "unknown_cites": bad, "iteration": state.get("iteration", 0) + 1,
            "revision_instructions": state.get("revision_instructions", "")}


# ------------------------------------------------------------------- export
LATEX_TEMPLATE = r"""\documentclass[11pt,a4paper]{article}
\usepackage[utf8]{inputenc}\usepackage[T1]{fontenc}\usepackage{amsmath,amssymb,booktabs,graphicx,hyperref,algorithm,algpseudocode,url}
\usepackage[margin=1in]{geometry}
\title{%(title)s}
\author{Generated by a multi-agent pipeline}
\date{\today}
\begin{document}
\maketitle
\begin{abstract}
%(abstract)s
\end{abstract}
\noindent\textbf{Keywords:} %(keywords)s
%(body)s
\bibliographystyle{plain}
\bibliography{references}
\end{document}
"""


def export_agent(state: State):
    out = state["out_dir"]
    os.makedirs(out, exist_ok=True)
    p = dict(state["paper"])
    bad = set(unknown_cites(p, state["papers"]))
    if bad:                                           # never ship fabricated citation keys
        for k in p:
            if isinstance(p[k], str):
                p[k] = re.sub(r"\\cite[a-z]*\{([^}]*)\}", lambda m: (
                    "\\cite{%s}" % ",".join(x.strip() for x in m.group(1).split(",") if x.strip() not in bad)
                    if any(x.strip() not in bad for x in m.group(1).split(",")) else ""), p[k])
    body = "\n\n".join(f"\\section{{{n}}}\n{p[k]}" for k, n in SECTION_ORDER)
    tex = LATEX_TEMPLATE % {"title": p["title"], "abstract": p["abstract"],
                            "keywords": ", ".join(p["keywords"]), "body": body}
    w = lambda name, s: open(os.path.join(out, name), "w", encoding="utf-8").write(s)
    w("paper.tex", tex)
    w("references.bib", to_bibtex(state["papers"]))
    w("simulation.py", state.get("sim_code", ""))
    w("simulation_output.txt", state.get("sim_output", ""))
    w("review.txt", str(state.get("review")))
    made = ["paper.tex", "references.bib"]
    pandoc = shutil.which("pandoc")
    if pandoc:
        r = subprocess.run([pandoc, "paper.tex", "--citeproc", "--bibliography=references.bib", "-o", "paper.docx"],
                           cwd=out, capture_output=True, text=True)
        if r.returncode == 0:
            made.append("paper.docx")
        else:
            print("  docx failed:", r.stderr[:300])
    if shutil.which("pdflatex"):
        for cmd in (["pdflatex"], ["bibtex"], ["pdflatex"], ["pdflatex"]):
            subprocess.run(cmd + ["-interaction=nonstopmode", "paper"] if cmd[0] == "pdflatex" else cmd + ["paper"],
                           cwd=out, capture_output=True)
    elif shutil.which("tectonic"):
        subprocess.run(["tectonic", "paper.tex"], cwd=out, capture_output=True)
    elif pandoc:
        subprocess.run([pandoc, "paper.tex", "--citeproc", "--bibliography=references.bib", "-o", "paper.pdf"],
                       cwd=out, capture_output=True)
    if os.path.exists(os.path.join(out, "paper.pdf")):
        made.append("paper.pdf")
    else:
        print("  PDF not produced (install texlive or tectonic, or compile paper.tex on Overleaf).")
    status = "APPROVED" if state.get("approved") else "NOT APPROVED (iteration limit reached) - treat as a draft"
    print(f"\n[Export] {status}. Files in {os.path.abspath(out)}: {', '.join(made)}")
    return {}


# -------------------------------------------------------------------- graph
def route_after_supervisor(state: State):
    if state.get("iteration", 0) == 0:
        return "literature"
    if state["approved"] or state["iteration"] >= state["max_iterations"]:
        return "export"
    return state["rerun_from"]


def build_graph():
    g = StateGraph(State)
    for name, fn in [("supervisor", supervisor), ("literature", literature_agent), ("gaps", gap_agent),
                     ("methodology", methodology_agent), ("simulation", simulation_agent),
                     ("writer", writer_agent), ("reviewer", reviewer_agent), ("export", export_agent)]:
        g.add_node(name, fn)
    g.set_entry_point("supervisor")
    g.add_conditional_edges("supervisor", route_after_supervisor, {s: s for s in STAGES + ["export"]})
    for a, b in zip(STAGES, STAGES[1:] + ["reviewer"]):
        g.add_edge(a, b)
    g.add_edge("reviewer", "supervisor")
    g.add_edge("export", END)
    return g.compile()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("topic", nargs="?", default=DEFAULT_TOPIC)
    ap.add_argument("--max-iterations", type=int, default=3)
    ap.add_argument("--out", default="output")
    a = ap.parse_args()
    build_graph().invoke({"topic": a.topic, "iteration": 0, "max_iterations": a.max_iterations, "out_dir": a.out},
                         {"recursion_limit": 100})


if __name__ == "__main__":
    main()
