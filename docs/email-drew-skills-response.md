Subject: Re: QuickPat skills / pattern author approach

Drew,

Thanks for the suggestion on merging the skills. I thought it was worth explaining the reasoning behind how QuickPat uses (and doesn't use) LLM.

The core design goal was deterministic repeatability — same quickstart input produces the same pattern output every time, no LLM required. The analyzer, generator, and pipeline are all pure code. We reference SKILL.md and reference.md, but we encode those rules directly in the Python generator and validator rather than delegating them to an LLM at runtime.

LLM is available as an optional enhancement for two narrow cases: classifying ambiguous secrets when name-pattern matching isn't enough, and running a semantic validation review against the SKILL.md checklist. Both are bolt-on — the tool works fully without them.

So where the Patternizer skills guide an interactive authoring session with an LLM, QuickPat does the same work programmatically. Different approach to the same problem, and I think they complement each other well — Patternizer for hand-crafted patterns where human judgment drives the process, QuickPat for automated conversion where consistency across the 6+ quickstarts matters more.

That said, I think there's room to tighten the alignment between the two tools going forward. Appreciate you pointing me at the Patternizer repo — it's been directly useful in improving our output.

Aaron
