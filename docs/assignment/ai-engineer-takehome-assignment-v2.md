# AI Engineer Take-Home Assignment
## Document Intelligence Agent

### Context

Axmed runs a medicines marketplace connecting buyers (governments, NGOs, health systems) with suppliers across emerging markets. A constant operational bottleneck is that supplier quotations, product specs, and order confirmations arrive in wildly inconsistent formats: clean PDFs, raw JSON exports, WhatsApp/email negotiation threads, and low-quality scanned or photographed documents.

We want to explore how an AI-powered pipeline could ingest these heterogeneous sources, extract structured data, and hand it to a human reviewer for approval before it touches our systems of record.

### The task

Build a solution that:

1. **Ingests** a folder of mixed documents (PDFs, JSON exports, email, images).
2. **Extracts** a defined set of fields from each document into a common schema.
3. **Writes results to a table** (CSV, SQLite, or Postgres, your choice) suitable for human review.
4. **Flags uncertainty**. Every record should indicate confidence and/or which fields the model wasn't sure about, so a reviewer knows where to look twice.

We want to see how you reason about a genuinely messy real-world extraction problem, and how you build something that will still work when the inputs change next week.

### Target schema

This is a suggested starting point, not a strict contract. Extract the fields below wherever present, and feel free to adjust the shape if a document calls for it (explain any changes in your write-up):

```json
{
  "quotation": {
    "product_name": "string",
    "inn": "string",
    "price_per_uom": "number",
    "currency": "string",
    "source_document": "string"
  }
}
```

### Provided materials

You'll receive a folder of simulated supplier documents spanning a range of formats and quality levels. Broadly, expect:

- **Machine-readable exports**: JSON from supplier ERP or catalogue systems. These are clean, but no two suppliers use the same field names or structure, and not all of them state a unit price directly.
- **Structured PDFs**: supplier quotation or proforma sheets. Well-formatted, but laid out differently from each other.
- **Email**: at least one supplier message where the relevant figures are stated in natural language rather than tabulated, and where something may be corrected or revised later in the message.
- **"Garbled" images**: low-quality scans or photos (skewed, low-res, partial glare, cropped, etc.) of quotation sheets, to test OCR robustness and how you handle low-confidence extraction.

Some products appear in more than one document, from different suppliers, in different currencies and pack sizes.
All of the material is synthetic. The suppliers, prices and reference numbers are invented, so there's nothing confidential in the pack and you're free to commit it to your repository.

### Build for the next document, not this one

**This is the single thing we weigh most heavily.** The pack you receive is a sample, not the spec. In production these documents arrive continuously, from new suppliers and new customer systems, in shapes nobody told us about in advance.

A solution that overfits these specific files (mapping known field names one by one, branching on known filenames, prompts tuned to the exact layouts we sent) will score poorly no matter how accurate it is on the sample.

We want to see, concretely in the code:

- **A new JSON export appears tomorrow**, from a supplier whose field names you have never seen. What happens? Does it fail loudly, get handled generically, or require a code change? If it requires a code change, how small is that change and where does it go?
- **A new PDF layout appears.** Same question.
- **A new file type appears entirely** (say, an Excel price list). How much of your pipeline has to change?
- **The same product arrives from two systems** that disagree: different pack sizes, currencies, units. Where does that get reconciled, and how does a reviewer see the conflict?

You don't need to *implement* every extension. You do need to make it obvious where it would go, and say so in your write-up. One clear extension point, actually built and used, beats a paragraph describing one.

### Use a framework

We expect a senior AI engineer to be fluent in the current tooling: LangChain / LangGraph, or similar.

We know that for six or seven files a framework can look like overkill, and on its own it would be. 
It isn't, given the section above: the point of the framework is the scalability, retries, structured outputs, tracing, evaluation hooks and swappable components you get when this pipeline is handling a thousand documents a week from twenty sources. Show us you can reach for the right level of abstraction without drowning a small problem in machinery.

Tell us in the write-up which framework you chose, what it bought you, and where it got in your way. 

### Commit as you go

We read the git history as closely as we read the code. It is how we see how you actually work, and it is where AI-assisted work becomes legible rather than a single 4,000-line drop.

- **Commit frequently and in small, coherent steps**, roughly one commit per decision or working increment, not one per session.
- **If you're working with an AI agent, instruct it to commit at every meaningful step.** Put that in your `CLAUDE.md` / agent instructions or system prompt. We want the trail of how the solution evolved: what you tried, what you rejected, what you changed your mind about.
- **Write messages that explain *why*, not *what*.** "Switch supplier JSON mapping to schema-driven resolver, hard-coded field map broke on the third supplier" tells us something. "Update parser" does not.
- Working on branches with merged PRs is welcome but not required. **Do not squash.** A single "initial commit" containing the whole solution is a red flag and will count against you.

### Deliverables

1. **Code**: a runnable private repo (README with setup/run instructions) with a full, granular commit history.
2. **Output table**: the extracted records from the provided documents, including confidence flags and source references.
3. **An LLM/Claude-generated `.md` file** summarizing the conversation with the agent you used to plan and execute the work, highlighting your role in guiding the agent. This file must be agent-generated.
4. **Write-up (1 page max)** covering:
   - Architecture and key design decisions
   - **Extensibility**: what happens when a new format, a new supplier, or a new customer system arrives, and where in the code that is handled
   - Framework choice: what you used, why, and what it cost you
   - How you handled the natural-language and image cases differently from the clean, structured sources
   - How your pipeline signals uncertainty rather than silently guessing
   - What you'd change to make this production-ready (cost, latency, monitoring, handling PII/regulated data, human-in-the-loop UX)
   - CI/CD and infrastructure implications of running this in production (deployment, testing strategy, scaling)
   - Known limitations / what you'd do with more time
   - **Reflecting on the product data model above**: what are its shortcomings, and how would you improve it? (e.g. does it capture what a real quotation needs, how well does it handle variation across suppliers/documents, what's missing or over-simplified)
5. **Show and Tell**: A very short video walkthrough of the solution (maximum 5 minutes).

### Constraints

- Time-box: **4-6 hours**. We're not expecting a polished product. Prefer a narrower pipeline that is well-structured and honest about its gaps over a wide one that special-cases its way to full coverage.
- **Python, and a framework** (see above). The choice of model is up to you and part of what we're evaluating; note which you used and why.
- Please don't spend money on paid API credits beyond what's reasonable for a handful of documents. Use free tiers or your own key with a small budget in mind.
- State any assumptions you make about ambiguous fields.

### Evaluation criteria

- **Generalisation**: would this survive a new document format, supplier, or source system without a rewrite? *(weighted most heavily)*
- **Framework fluency**: sensible use of the ecosystem, and a clear rationale for what was and wasn't delegated to it
- **Commit history**: small, frequent, well-explained commits that show the reasoning as it happened
- **Correctness** on the clean PDF and JSON cases
- **Robustness** on the email and garbled-image cases: does it degrade gracefully, or fail silently/confidently?
- **Schema handling**: sensible normalization across very different input shapes
- **Human-in-the-loop thinking**: is the output actually reviewable, or just a data dump?
- **Code quality and structure**
- **Communication**: clarity of the write-up and tradeoff reasoning

### A recommendation

We are looking for senior, 10x thinkers. We want to find people who can teach us what an AI engineer can do. Bring that ambition to your assignment.

### Submission

Share a private GitHub repository with **@malber**, **@albertovpd** and **@cesar-axmed**.
