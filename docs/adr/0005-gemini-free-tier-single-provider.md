# Gemini free tier, single provider, for the POC

The pipeline calls Google Gemini through LangChain using a free-tier API key: a lighter model extracts, and a stronger Gemini model does the second read for images and failed checks. The author's Claude subscription can't be used. Anthropic documents subscription OAuth tokens only for Claude Code and its Agent SDK, and its terms direct developers to API keys. A paid Anthropic key was declined to keep the take-home at zero cost.

## Considered Options

- **Claude via API key** (~$5–10 for development): rejected on cost.
- **Mixed providers** (Gemini extracts, Claude verifies): its cross-family independence was attractive but was rejected for simplicity.
- **Provider-agnostic configuration with fallbacks:** deferred to production.

## Consequences

- Gemini free-tier inputs may be used by Google to improve its products. That's acceptable for the synthetic pack and unacceptable for real supplier data.
- Free-tier daily quotas are small: 20 requests a day for the second-read model. When it ran out during the eval, the second read fell back to the extraction model through `SDI_SECOND_READ_MODEL`. Same-model reads would hit the LLM cache as identical requests, so the second reader uses its own seeds.
