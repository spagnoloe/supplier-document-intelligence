# Generic extraction: loaders own format, the LLM owns layout

Every Source passes through a loader chosen by file type (registered once per type) that turns it into text and/or images, then through one generic LLM extraction prompt with no supplier names, layouts, field-name mappings or examples from the sample pack. A new supplier or layout of a known type needs no code change; a new file type needs one registered loader; an unknown file type fails loudly. We chose this over per-supplier mappings or layout-tuned prompts, which would score better on the sample but break on the next document, the criterion the brief weighs most.

## Consequences

- Large tabular sources are serialized whole into the prompt, which costs tokens and risks dropped rows at scale. The production path (an LLM-proposed column mapping cached per structure fingerprint) is described, not built.
- An Excel loader is added as an isolated commit, as proof that a new file type touches only the loader layer.
